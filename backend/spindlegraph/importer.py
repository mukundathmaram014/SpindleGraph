"""Importer: specs/*.md <-> Spec records, per docs/SPEC.md §4.

The file is canonical for content; the DB is a synced projection. Parsing is
tolerant — hand-written specs predate SpindleGraph.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import yaml

from . import db as dbm

FILENAME_RE = re.compile(r"^(\d+)-(.+)\.md$", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
FILES_HEADING_RE = re.compile(
    r"^((affected|modified?|modifies|touched)\s+files?|files)\s*:?$", re.IGNORECASE
)
DECISIONS_HEADING_RE = re.compile(
    r"^decisions?(\s+(needed|required))?\s*:?$", re.IGNORECASE
)
RISK_HEADING_RE = re.compile(r"^risks?(\s+assessment)?\s*:?$", re.IGNORECASE)
INVOLVEMENT_RE = re.compile(
    r"involvement[^a-z]*?(minimal|moderate|involved)", re.IGNORECASE)
REVIEW_RE = re.compile(
    r"review(\s+attention)?[^a-z]*?(low|medium|high)", re.IGNORECASE)
LIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[( |x|X)\]\s+(.*)$")
CODE_SPAN_RE = re.compile(r"`([^`]+)`")
STATUSES = {"draft", "decided", "building", "built", "stale", "archived"}


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("\n", 1)
        if len(parts) == 2:
            m = re.search(r"^---\s*$", parts[1], re.MULTILINE)
            if m:
                raw = parts[1][: m.start()]
                try:
                    data = yaml.safe_load(raw)
                    if isinstance(data, dict):
                        return data, text
                except yaml.YAMLError:
                    pass
    return {}, text


def _normalize_path(p: str) -> str:
    p = p.strip().strip('"').strip("'").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _extract_path_and_rationale(item: str) -> tuple[str, str]:
    rationale = ""
    m = CODE_SPAN_RE.search(item)
    if m:
        path = m.group(1)
        rest = item[m.end():]
    else:
        parts = item.split(None, 1)
        path = (parts[0] if parts else "").rstrip(":,")
        rest = parts[1] if len(parts) > 1 else ""
    rm = re.match(r"\s*(?:—|--|:|-)\s*(.*)$", rest)
    if rm:
        rationale = rm.group(1).strip()
    elif rest.strip():
        rationale = rest.strip()
    return _normalize_path(path), rationale


def _expand_glob(pattern: str, repo_root: Path) -> list[str]:
    try:
        matches = sorted(
            str(p.relative_to(repo_root)).replace("\\", "/")
            for p in repo_root.glob(pattern)
            if p.is_file() and ".git/" not in str(p.relative_to(repo_root)).replace("\\", "/")
        )
    except (ValueError, OSError, NotImplementedError):
        matches = []
    return matches


def parse_spec_file(path: Path, repo_root: Path) -> dict | None:
    """Parse one spec markdown file. Returns None if the filename doesn't
    match ``NNNN-slug.md``."""
    m = FILENAME_RE.match(path.name)
    if not m:
        return None
    number, slug = int(m.group(1)), m.group(2)
    body = path.read_text(encoding="utf-8")
    fm, _ = _split_frontmatter(body)

    status = str(fm.get("status", "")).strip().lower()
    if status not in STATUSES:
        status = "draft"

    title = str(fm.get("title") or "").strip()
    lines = body.splitlines()

    files: list[dict] = []
    decisions: list[dict] = []
    risk: dict = {}
    section = None  # None | "files" | "decisions" | "risk"
    for line in lines:
        h = HEADING_RE.match(line)
        if h:
            heading = h.group(1)
            if not title and line.startswith("# "):
                title = heading
            if FILES_HEADING_RE.match(heading):
                section = "files"
            elif DECISIONS_HEADING_RE.match(heading):
                section = "decisions"
            elif RISK_HEADING_RE.match(heading):
                section = "risk"
            else:
                section = None
            continue
        # a non-blank paragraph line ends a list section (tolerant parsing:
        # sections are "the list under the heading")
        if section and line.strip() and not LIST_ITEM_RE.match(line):
            section = None
        if section == "files":
            li = None if CHECKBOX_RE.match(line) else LIST_ITEM_RE.match(line)
            if li:
                raw_path, rationale = _extract_path_and_rationale(li.group(1).strip())
                if not raw_path:
                    continue
                if any(ch in raw_path for ch in "*?["):
                    expanded = _expand_glob(raw_path, repo_root)
                    if expanded:
                        for p in expanded:
                            files.append({"path": p, "rationale": rationale,
                                          "planned_new": False, "from_glob": raw_path})
                        continue
                planned_new = not (repo_root / raw_path).exists()
                files.append({"path": raw_path, "rationale": rationale,
                              "planned_new": planned_new, "from_glob": None})
        elif section == "risk":
            li = LIST_ITEM_RE.match(line)
            if li:
                item = li.group(1).strip()
                note = ""
                nm = re.search(r"(?:—|--)\s*(.*)$", item)
                if nm:
                    note = nm.group(1).strip()
                im = INVOLVEMENT_RE.search(item)
                if im:
                    risk["involvement"] = im.group(1).lower()
                    if note:
                        risk["involvement_note"] = note
                    continue
                rm2 = REVIEW_RE.search(item)
                if rm2:
                    risk["review"] = rm2.group(2).lower()
                    if note:
                        risk["review_note"] = note
        elif section == "decisions":
            cb = CHECKBOX_RE.match(line)
            if cb:
                resolved = cb.group(1).lower() == "x"
                text = cb.group(2).strip()
                answer = ""
                am = re.search(r"(?:→|->|\*\*Answer:?\*\*)\s*(.*)$", text)
                if am:
                    answer = am.group(1).strip()
                    text = text[: am.start()].strip()
                decisions.append({"text": text, "resolved": resolved, "answer": answer})
            else:
                li = LIST_ITEM_RE.match(line)
                if li and li.group(1).strip():
                    decisions.append({"text": li.group(1).strip(),
                                      "resolved": False, "answer": ""})

    if not title:
        title = slug.replace("-", " ").replace("_", " ").capitalize()

    # de-dup file paths, keep first occurrence
    seen: set[str] = set()
    files = [f for f in files if not (f["path"] in seen or seen.add(f["path"]))]

    return {
        "number": number,
        "slug": slug,
        "title": title,
        "status": status,
        "body_md": body,
        "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "files_planned": files,
        "decisions": decisions,
        "risk": risk,
    }


def write_status_to_file(path: Path, status: str) -> None:
    """Write ``status`` into the spec file's frontmatter (creating one if
    absent) — the one field SpindleGraph routinely writes into spec files."""
    text = path.read_text(encoding="utf-8")
    fm, _ = _split_frontmatter(text)
    if fm:
        head_end = re.search(r"^---\s*$", text.split("\n", 1)[1], re.MULTILINE)
        raw = text.split("\n", 1)[1][: head_end.start()]
        if re.search(r"^status\s*:", raw, re.MULTILINE):
            new_raw = re.sub(r"^status\s*:.*$", f"status: {status}", raw,
                             flags=re.MULTILINE)
        else:
            new_raw = raw.rstrip("\n") + f"\nstatus: {status}\n"
        rest = text.split("\n", 1)[1][head_end.start():]
        text = "---\n" + new_raw + rest
    else:
        text = f"---\nstatus: {status}\n---\n\n" + text
    path.write_text(text, encoding="utf-8")


def import_project(conn: sqlite3.Connection, project_id: int) -> dict:
    """Scan the project's specs/ dir and sync Spec records. File wins for
    content; DB-only fields are preserved. Missing files -> archived."""
    proj = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if proj is None:
        raise ValueError(f"no project {project_id}")
    repo_root = Path(proj["repo_path"])
    specs_dir = repo_root / "specs"
    parsed: dict[int, dict] = {}
    if specs_dir.is_dir():
        for p in sorted(specs_dir.glob("*.md")):
            rec = parse_spec_file(p, repo_root)
            if rec:
                rec["file_path"] = f"specs/{p.name}"
                parsed[rec["number"]] = rec

    existing = {
        r["number"]: r
        for r in conn.execute("SELECT * FROM spec WHERE project_id=?", (project_id,))
    }
    imported, archived = 0, 0
    for number, rec in parsed.items():
        args = (
            rec["slug"], rec["title"], rec["status"], rec["file_path"],
            rec["body_md"], rec["body_hash"],
            json.dumps(rec["files_planned"]), json.dumps(rec["decisions"]),
            json.dumps(rec.get("risk") or {}),
            dbm.now(),
        )
        if number in existing:
            # 'built'/'stale' are operational states owned by SpindleGraph: a spec
            # built on an unmerged branch still reads draft/decided in the
            # default-branch file, and staleness is a derived hint, so don't let a
            # re-import silently downgrade either.
            if existing[number]["status"] in ("built", "stale") \
                    and rec["status"] in ("draft", "decided"):
                args = args[:2] + (existing[number]["status"],) + args[3:]
            conn.execute(
                "UPDATE spec SET slug=?, title=?, status=?, file_path=?, body_md=?,"
                " body_hash=?, files_planned_json=?, decisions_json=?, risk_json=?,"
                " updated_at=? WHERE id=?",
                args + (existing[number]["id"],),
            )
        else:
            conn.execute(
                "INSERT INTO spec (slug, title, status, file_path, body_md, body_hash,"
                " files_planned_json, decisions_json, risk_json, updated_at,"
                " project_id, number) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                args + (project_id, number),
            )
        imported += 1
    for number, row in existing.items():
        if number not in parsed and row["status"] != "archived":
            conn.execute("UPDATE spec SET status='archived', updated_at=? WHERE id=?",
                         (dbm.now(), row["id"]))
            archived += 1
    conn.commit()

    from . import graph
    graph.recompute(conn, project_id)
    return {"imported": imported, "archived": archived}
