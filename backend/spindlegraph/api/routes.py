"""REST API per docs/SPEC.md §8."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from .. import db as dbm
from .. import graph, importer
from ..events import bus
from ..orchestrator import executors as ex
from ..orchestrator import worktrees as wt
from ..orchestrator.jobs import manager

router = APIRouter(prefix="/api")


def _conn():
    return dbm.connect()


def _spec_dict(row) -> dict:
    return dbm.row_to_dict(row, dbm.SPEC_JSON)


def _executor_dict(row) -> dict:
    d = dict(row)
    d["estimated_success"] = round(ex.estimated_success(d), 3)
    return d


# ---------------- health & config ----------------

@router.get("/health")
def health():
    cfg = config.load_config()
    claude = shutil.which(cfg["claude_bin"].split()[0])
    gh = shutil.which("gh")
    version = None
    if claude:
        try:
            version = subprocess.run([claude, "--version"], capture_output=True,
                                     text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"claude_path": claude, "claude_version": version, "gh": bool(gh)}


@router.get("/config")
def get_config():
    return config.load_config()


class ConfigPatch(BaseModel):
    claude_bin: str | None = None
    max_parallel: int | None = None
    job_timeout_min: int | None = None


@router.patch("/config")
def patch_config(body: ConfigPatch):
    return config.save_config({**config.load_config(),
                               **body.model_dump(exclude_none=True)})


# ---------------- projects ----------------

class ProjectCreate(BaseModel):
    repo_path: str
    name: str | None = None
    notes_doc_path: str | None = None
    create_specs_dir: bool = True
    copy_commands: bool = True


@router.post("/projects")
def create_project(body: ProjectCreate):
    repo = Path(body.repo_path).expanduser()
    if not repo.is_dir():
        raise HTTPException(400, f"not a directory: {repo}")
    if not (repo / ".git").exists():
        raise HTTPException(400, f"not a git repository: {repo}")
    specs_dir = repo / "specs"
    if not specs_dir.is_dir():
        if not body.create_specs_dir:
            raise HTTPException(400, "repo has no specs/ directory")
        specs_dir.mkdir()
        (specs_dir / ".gitkeep").touch()
    if body.copy_commands:
        wt.ensure_commands(repo)
    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM project WHERE repo_path=?",
                        (str(repo),)).fetchone():
            raise HTTPException(409, "project already exists for this path")
        base_slug = repo.name.lower().replace(" ", "-")
        slug, i = base_slug, 2
        while conn.execute("SELECT 1 FROM project WHERE slug=?", (slug,)).fetchone():
            slug, i = f"{base_slug}-{i}", i + 1
        conn.execute(
            "INSERT INTO project (slug, name, repo_path, notes_doc_path,"
            " default_branch, created_at) VALUES (?,?,?,?,?,?)",
            (slug, body.name or repo.name, str(repo), body.notes_doc_path,
             wt.detect_default_branch(repo), dbm.now()),
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM project WHERE repo_path=?",
                           (str(repo),)).fetchone()[0]
        importer.import_project(conn, pid)
        return dict(conn.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()


@router.get("/projects")
def list_projects():
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM project ORDER BY id")]
    finally:
        conn.close()


def _get_project(conn, project_id: int):
    row = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "project not found")
    return row


@router.get("/projects/{project_id}")
def get_project(project_id: int):
    conn = _conn()
    try:
        d = dict(_get_project(conn, project_id))
        d["settings"] = json.loads(d.pop("settings_json") or "{}")
        return d
    finally:
        conn.close()


class ProjectPatch(BaseModel):
    name: str | None = None
    notes_doc_path: str | None = None
    default_branch: str | None = None
    settings: dict | None = None


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, body: ProjectPatch):
    conn = _conn()
    try:
        row = _get_project(conn, project_id)
        settings = json.loads(row["settings_json"] or "{}")
        if body.settings is not None:
            settings.update(body.settings)
        conn.execute(
            "UPDATE project SET name=?, notes_doc_path=?, default_branch=?,"
            " settings_json=? WHERE id=?",
            (body.name or row["name"],
             body.notes_doc_path if body.notes_doc_path is not None
             else row["notes_doc_path"],
             body.default_branch or row["default_branch"],
             json.dumps(settings), project_id))
        conn.commit()
        return get_project(project_id)
    finally:
        conn.close()


@router.delete("/projects/{project_id}")
def delete_project(project_id: int):
    """Remove a project's records from SpindleGraph. Never touches the target
    repo itself (specs, branches, and commands stay put)."""
    conn = _conn()
    try:
        _get_project(conn, project_id)
        for tbl in ("reconcile_proposal", "edge", "job", "spec"):
            conn.execute(f"DELETE FROM {tbl} WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project WHERE id=?", (project_id,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()


@router.post("/projects/{project_id}/import")
def reimport(project_id: int):
    conn = _conn()
    try:
        _get_project(conn, project_id)
        res = importer.import_project(conn, project_id)
        bus.publish(project_id, {"type": "specs.updated"})
        bus.publish(project_id, {"type": "graph.updated"})
        return res
    finally:
        conn.close()


# ---------------- specs ----------------

@router.get("/projects/{project_id}/specs")
def list_specs(project_id: int):
    conn = _conn()
    try:
        _get_project(conn, project_id)
        return [_spec_dict(r) for r in conn.execute(
            "SELECT * FROM spec WHERE project_id=? ORDER BY number", (project_id,))]
    finally:
        conn.close()


def _get_spec(conn, spec_id: int):
    row = conn.execute("SELECT * FROM spec WHERE id=?", (spec_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "spec not found")
    return row


@router.get("/specs/{spec_id}")
def get_spec(spec_id: int):
    conn = _conn()
    try:
        return _spec_dict(_get_spec(conn, spec_id))
    finally:
        conn.close()


class SpecPatch(BaseModel):
    body_md: str | None = None
    status: str | None = None
    executor_id: int | None = None
    depends_on: list[int] | None = None


@router.patch("/specs/{spec_id}")
def patch_spec(spec_id: int, body: SpecPatch):
    conn = _conn()
    try:
        spec = _get_spec(conn, spec_id)
        proj = _get_project(conn, spec["project_id"])
        path = Path(proj["repo_path"]) / spec["file_path"]
        if body.body_md is not None:
            path.write_text(body.body_md, encoding="utf-8")
        if body.status is not None:
            if body.status not in importer.STATUSES:
                raise HTTPException(400, f"invalid status: {body.status}")
            importer.write_status_to_file(path, body.status)
        if body.executor_id is not None:
            conn.execute("UPDATE spec SET executor_id=? WHERE id=?",
                         (body.executor_id or None, spec_id))
        if body.depends_on is not None:
            conn.execute("UPDATE spec SET depends_on_json=? WHERE id=?",
                         (json.dumps(body.depends_on), spec_id))
        conn.commit()
        importer.import_project(conn, spec["project_id"])
        bus.publish(spec["project_id"], {"type": "specs.updated"})
        bus.publish(spec["project_id"], {"type": "graph.updated"})
        return _spec_dict(_get_spec(conn, spec_id))
    finally:
        conn.close()


# ---------------- graph ----------------

@router.get("/projects/{project_id}/graph")
def get_graph(project_id: int):
    conn = _conn()
    try:
        _get_project(conn, project_id)
        specs = [_spec_dict(r) for r in conn.execute(
            "SELECT * FROM spec WHERE project_id=? AND status != 'archived'"
            " ORDER BY number", (project_id,))]
        edges = [dbm.row_to_dict(r, ("shared_files_json",)) for r in conn.execute(
            "SELECT * FROM edge WHERE project_id=?", (project_id,))]
        nodes = [{
            "id": s["id"], "number": s["number"], "slug": s["slug"],
            "title": s["title"], "status": s["status"],
            "executor_id": s["executor_id"],
            "file_count": len(s["files_planned"]),
            "unknown_footprint": not graph.effective_files(s),
            "unresolved_decisions": sum(1 for d in s["decisions"] if not d["resolved"]),
            "pr_url": (s["provenance"] or {}).get("pr_url"),
            "risk": s.get("risk") or {},
        } for s in specs]
        # directed dependency edges: source depends on (must build after) target.
        present = {s["id"] for s in specs}
        deps = [
            {"source": s["id"], "target": d}
            for s in specs
            for d in dict.fromkeys(s.get("depends_on") or [])
            if d in present and d != s["id"]
        ]
        return {"nodes": nodes, "edges": edges, "deps": deps}
    finally:
        conn.close()


class CheckBody(BaseModel):
    spec_ids: list[int]


@router.post("/projects/{project_id}/graph/check")
def graph_check(project_id: int, body: CheckBody):
    conn = _conn()
    try:
        _get_project(conn, project_id)
        specs = [_spec_dict(r) for r in conn.execute(
            "SELECT * FROM spec WHERE project_id=?", (project_id,))]
        edges = [dbm.row_to_dict(r, ("shared_files_json",)) for r in conn.execute(
            "SELECT * FROM edge WHERE project_id=?", (project_id,))]
        return graph.check_selection(specs, edges, body.spec_ids)
    finally:
        conn.close()


class EdgePatch(BaseModel):
    weight: float | None = None
    overridden: bool | None = None


@router.patch("/projects/{project_id}/edges/{spec_a}/{spec_b}")
def patch_edge(project_id: int, spec_a: int, spec_b: int, body: EdgePatch):
    conn = _conn()
    try:
        a, b = sorted((spec_a, spec_b))
        row = conn.execute(
            "SELECT * FROM edge WHERE project_id=? AND spec_a=? AND spec_b=?",
            (project_id, a, b)).fetchone()
        if row is None:
            raise HTTPException(404, "edge not found")
        weight = body.weight if body.weight is not None else row["weight"]
        overridden = row["overridden"]
        if body.weight is not None:
            overridden = 1
        if body.overridden is not None:
            overridden = int(body.overridden)
        conn.execute(
            "UPDATE edge SET weight=?, overridden=? WHERE project_id=? AND spec_a=?"
            " AND spec_b=?", (weight, overridden, project_id, a, b))
        conn.commit()
        bus.publish(project_id, {"type": "graph.updated"})
        return dbm.row_to_dict(conn.execute(
            "SELECT * FROM edge WHERE project_id=? AND spec_a=? AND spec_b=?",
            (project_id, a, b)).fetchone(), ("shared_files_json",))
    finally:
        conn.close()


# ---------------- reconcile proposals ----------------

@router.get("/projects/{project_id}/proposals")
def list_proposals(project_id: int, status: str | None = "pending"):
    conn = _conn()
    try:
        _get_project(conn, project_id)
        q = "SELECT * FROM reconcile_proposal WHERE project_id=?"
        params: list = [project_id]
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY id DESC"
        out = []
        for r in conn.execute(q, params):
            d = dict(r)
            spec = conn.execute(
                "SELECT number, slug, title, body_md, status FROM spec WHERE id=?",
                (d["spec_id"],)).fetchone()
            if spec:
                d.update(spec_number=spec["number"], spec_slug=spec["slug"],
                         spec_title=spec["title"], spec_status=spec["status"],
                         current_body=spec["body_md"])
            if d.get("trigger_spec_id"):
                trig = conn.execute("SELECT number, title FROM spec WHERE id=?",
                                    (d["trigger_spec_id"],)).fetchone()
                if trig:
                    d.update(trigger_number=trig["number"], trigger_title=trig["title"])
            out.append(d)
        return out
    finally:
        conn.close()


def _get_proposal(conn, proposal_id: int):
    row = conn.execute("SELECT * FROM reconcile_proposal WHERE id=?",
                       (proposal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "proposal not found")
    return row


@router.post("/proposals/{proposal_id}/accept")
def accept_proposal(proposal_id: int):
    conn = _conn()
    try:
        p = _get_proposal(conn, proposal_id)
        if p["status"] != "pending":
            raise HTTPException(409, f"proposal already {p['status']}")
        spec = _get_spec(conn, p["spec_id"])
        proj = _get_project(conn, p["project_id"])
        if not p["no_change"] and p["proposed_body"]:
            path = Path(proj["repo_path"]) / spec["file_path"]
            path.write_text(p["proposed_body"], encoding="utf-8")
        conn.execute("UPDATE reconcile_proposal SET status='accepted' WHERE id=?",
                     (proposal_id,))
        conn.commit()
        importer.import_project(conn, p["project_id"])
        # importer preserves 'stale'; clear it now the spec is reconciled.
        conn.execute(
            "UPDATE spec SET status=?, updated_at=? WHERE id=? AND status='stale'",
            (p["prior_status"], dbm.now(), p["spec_id"]))
        conn.commit()
        bus.publish(p["project_id"], {"type": "specs.updated"})
        bus.publish(p["project_id"], {"type": "graph.updated"})
        bus.publish(p["project_id"], {"type": "proposals.updated"})
        return _spec_dict(_get_spec(conn, p["spec_id"]))
    finally:
        conn.close()


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int):
    conn = _conn()
    try:
        p = _get_proposal(conn, proposal_id)
        if p["status"] != "pending":
            raise HTTPException(409, f"proposal already {p['status']}")
        conn.execute("UPDATE reconcile_proposal SET status='rejected' WHERE id=?",
                     (proposal_id,))
        conn.execute(
            "UPDATE spec SET status=?, updated_at=? WHERE id=? AND status='stale'",
            (p["prior_status"], dbm.now(), p["spec_id"]))
        conn.commit()
        bus.publish(p["project_id"], {"type": "specs.updated"})
        bus.publish(p["project_id"], {"type": "graph.updated"})
        bus.publish(p["project_id"], {"type": "proposals.updated"})
        return {"ok": True}
    finally:
        conn.close()


# ---------------- executors ----------------

@router.get("/executors")
def list_executors():
    conn = _conn()
    try:
        return [_executor_dict(r) for r in
                conn.execute("SELECT * FROM executor ORDER BY id")]
    finally:
        conn.close()


class ExecutorBody(BaseModel):
    name: str
    backend: str = "claude_code"
    model: str | None = None
    command_template: str | None = None   # local_cli: must contain {prompt}
    prior_success: float = 0.8
    prior_strength: float = 10
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    enabled: bool = True


@router.post("/executors")
def create_executor(body: ExecutorBody):
    if body.backend not in ex.BACKENDS:
        raise HTTPException(400, f"backend must be one of {ex.BACKENDS}")
    if body.backend == "local_cli" and "{prompt}" not in (body.command_template or ""):
        raise HTTPException(400, "local_cli needs a command_template containing {prompt}")
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO executor (name, backend, model, command_template,"
            " prior_success, prior_strength, input_price_per_mtok,"
            " output_price_per_mtok, enabled) VALUES (?,?,?,?,?,?,?,?,?)",
            (body.name, body.backend, body.model, body.command_template,
             body.prior_success, body.prior_strength, body.input_price_per_mtok,
             body.output_price_per_mtok, int(body.enabled)))
        conn.commit()
        return _executor_dict(conn.execute("SELECT * FROM executor WHERE id=?",
                                           (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


class ExecutorPatch(BaseModel):
    name: str | None = None
    backend: str | None = None
    model: str | None = None
    command_template: str | None = None
    prior_success: float | None = None
    prior_strength: float | None = None
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    enabled: bool | None = None


@router.patch("/executors/{executor_id}")
def patch_executor(executor_id: int, body: ExecutorPatch):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM executor WHERE id=?",
                           (executor_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "executor not found")
        updates = body.model_dump(exclude_none=True)
        if "backend" in updates and updates["backend"] not in ex.BACKENDS:
            raise HTTPException(400, f"backend must be one of {ex.BACKENDS}")
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE executor SET {sets} WHERE id=?",
                         (*updates.values(), executor_id))
            conn.commit()
        return _executor_dict(conn.execute("SELECT * FROM executor WHERE id=?",
                                           (executor_id,)).fetchone())
    finally:
        conn.close()


# ---------------- jobs ----------------

class JobCreate(BaseModel):
    project_id: int
    kind: str  # triage | spec | build | build_batch | scaffold
    spec_ids: list[int] = []
    idea: str | None = None            # kind=spec
    executor_id: int | None = None
    waves: list[list[int]] | None = None  # kind=build_batch


@router.post("/jobs")
async def create_job(body: JobCreate):
    conn = _conn()
    try:
        proj = _get_project(conn, body.project_id)
        kind = body.kind
        if kind == "spec":
            if not body.idea:
                raise HTTPException(400, "kind=spec requires 'idea'")
            idea = body.idea.replace('"', "'")
            job = manager.create_job(conn, proj["id"], "spec",
                                     executor_id=body.executor_id,
                                     prompt=f'/spec "{idea}"')
        elif kind == "triage":
            notes = proj["notes_doc_path"]
            if not notes:
                raise HTTPException(400, "project has no notes_doc_path configured")
            job = manager.create_job(conn, proj["id"], "triage",
                                     executor_id=body.executor_id,
                                     prompt=f"/triage {notes}")
        elif kind == "scaffold":
            job = manager.create_job(conn, proj["id"], "scaffold",
                                     executor_id=body.executor_id, prompt="/init")
        elif kind == "build":
            if len(body.spec_ids) != 1:
                raise HTTPException(400, "kind=build requires exactly one spec id")
            spec = _get_spec(conn, body.spec_ids[0])
            unresolved = [d for d in json.loads(spec["decisions_json"])
                          if not d["resolved"]]
            if unresolved:
                raise HTTPException(
                    409, f"spec has {len(unresolved)} unresolved decision(s)")
            job = manager.create_job(
                conn, proj["id"], "build", [spec["id"]],
                executor_id=body.executor_id or spec["executor_id"]
                or manager.default_executor_id(conn, proj),
                prompt=f"/build {spec['file_path']}")
        elif kind == "build_batch":
            if not body.spec_ids:
                raise HTTPException(400, "kind=build_batch requires spec_ids")
            waves = body.waves
            if not waves:
                specs = [_spec_dict(r) for r in conn.execute(
                    "SELECT * FROM spec WHERE project_id=?", (proj["id"],))]
                edges = [dbm.row_to_dict(r, ("shared_files_json",)) for r in
                         conn.execute("SELECT * FROM edge WHERE project_id=?",
                                      (proj["id"],))]
                waves = graph.suggest_waves(specs, edges, body.spec_ids)
            job = manager.create_job(conn, proj["id"], "build_batch", body.spec_ids)
            manager.set_waves(job["id"], waves)
        else:
            raise HTTPException(400, f"unknown kind: {kind}")
        manager.launch(job["id"])
        return job
    finally:
        conn.close()


@router.get("/jobs")
def list_jobs(project_id: int):
    conn = _conn()
    try:
        return [dbm.row_to_dict(r, dbm.JOB_JSON) for r in conn.execute(
            "SELECT * FROM job WHERE project_id=? ORDER BY id DESC LIMIT 200",
            (project_id,))]
    finally:
        conn.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: int, tail: int = 500):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "job not found")
        d = dbm.row_to_dict(row, dbm.JOB_JSON)
        log = Path(d["log_path"])
        events = []
        if log.exists():
            lines = log.read_text(encoding="utf-8").splitlines()[-tail:]
            for ln in lines:
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    events.append({"type": "raw", "text": ln})
        d["log_events"] = events
        return d
    finally:
        conn.close()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int):
    ok = await manager.cancel(job_id)
    if not ok:
        raise HTTPException(409, "job is not running or queued")
    return {"canceled": True}
