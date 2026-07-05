"""Reconciliation: the post-build pass (docs/SPEC.md §10).

When a build lands, its *actual* git diff replaces the spec's *planned* file
list, the graph is re-derived, and any other spec whose conflict set changed is
marked ``stale``. A ``reconcile`` job then proposes edits to each stale spec;
proposals are stored (never auto-applied) for the user to accept or reject.

Pure/thin helpers here; the job wiring lives in ``orchestrator/jobs.py``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import db as dbm
from . import graph
from .orchestrator.worktrees import run_git


def capture_actual_files(repo: Path, default_branch: str, branch: str | None,
                         spec_file_path: str) -> list[dict]:
    """``git diff --name-status <default>...<branch>`` -> ``[{path, change}]``.

    Runs in the main repo (the spec branch outlives its worktree). The spec
    file itself is excluded. Returns ``[]`` if the diff can't be taken.
    """
    if not branch:
        return []
    code, out = run_git(
        ["diff", "--name-status", f"{default_branch}...{branch}"], repo)
    if code != 0:
        return []
    files: list[dict] = []
    seen: set[str] = set()
    for line in out.splitlines():
        parts = [p for p in line.split("\t") if p]
        if len(parts) < 2:
            continue
        change = parts[0].strip()[:1].upper()
        path = parts[-1].strip().replace("\\", "/")  # new path on renames
        # exclude spec files entirely — builds move them to specs/implemented/
        if not path or path == spec_file_path or path.startswith("specs/")                 or path in seen:
            continue
        seen.add(path)
        files.append({"path": path, "change": change})
    return files


def _signatures(edges: list[dict]) -> dict[int, set[tuple[int, tuple[str, ...]]]]:
    """spec_id -> {(neighbour_id, shared_files)} for each incident conflict edge."""
    sig: dict[int, set[tuple[int, tuple[str, ...]]]] = {}
    for e in edges:
        a, b = e["spec_a"], e["spec_b"]
        shared = tuple(e["shared_files"])
        sig.setdefault(a, set()).add((b, shared))
        sig.setdefault(b, set()).add((a, shared))
    return sig


def mark_stale_after_build(conn: sqlite3.Connection, project_id: int,
                           built_spec_id: int) -> list[dict]:
    """Re-derive the graph and flag specs whose conflict set changed.

    Assumes the built spec's ``files_actual`` is already stored. Returns the
    newly-stale specs as dicts carrying the context a proposal needs.
    """
    def read_edges() -> list[dict]:
        return [dbm.row_to_dict(r, ("shared_files_json",)) for r in conn.execute(
            "SELECT * FROM edge WHERE project_id=?", (project_id,))]

    before = _signatures(read_edges())
    graph.recompute(conn, project_id)
    after = _signatures(read_edges())

    rows = {r["id"]: r for r in conn.execute(
        "SELECT * FROM spec WHERE project_id=?", (project_id,))}
    stale: list[dict] = []
    for sid, row in rows.items():
        if sid == built_spec_id:
            continue
        if before.get(sid, set()) == after.get(sid, set()):
            continue
        if row["status"] in ("built", "archived", "building", "stale"):
            continue
        stale.append({
            "id": sid, "prior_status": row["status"], "number": row["number"],
            "slug": row["slug"], "title": row["title"], "body_md": row["body_md"],
            "file_path": row["file_path"],
        })
    for st in stale:
        conn.execute("UPDATE spec SET status='stale', updated_at=? WHERE id=?",
                     (dbm.now(), st["id"]))
    conn.commit()
    return stale


NO_CHANGES = "NO CHANGES NEEDED"


def build_prompt(trigger_number: int, trigger_title: str,
                 actual_files: list[dict], stale_body: str) -> str:
    """Prompt for a ``reconcile`` job: ask the agent to revise one stale spec
    in light of what a just-built spec actually changed."""
    changed = "\n".join(f"- {f['change']} {f['path']}" for f in actual_files) \
        or "- (no file changes detected)"
    return (
        f'Spec #{trigger_number:04d} "{trigger_title}" was just built. Its actual '
        f"changed files were:\n{changed}\n\n"
        "The spec file below may now be stale because its planned files overlap "
        "with what actually changed. Review it against that reality and propose "
        "an updated version.\n\n"
        f"=== CURRENT SPEC FILE ===\n{stale_body}\n=== END SPEC FILE ===\n\n"
        "Propose an updated version of this spec's markdown file: adjust the "
        "'Affected files' section and any assumptions to match what actually "
        "landed. Output ONLY the full revised markdown file content and nothing "
        f"else. If the spec is still accurate, output exactly: {NO_CHANGES}"
    )
