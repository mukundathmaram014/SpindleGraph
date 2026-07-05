"""Job engine: drives claude -p subprocesses, streams NDJSON events to the
log file + WebSocket bus, manages worktree builds and wave batches.

Jobs run in-process; a queued job does not survive a server restart (v0).
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .. import config
from .. import db as dbm
from .. import graph, importer, reconcile
from ..events import bus
from . import executors as ex
from . import worktrees as wt

PR_RE = re.compile(r"https://github\.com/[^\s\"'<>)]+/pull/\d+")
TERMINAL = {"succeeded", "failed", "canceled", "skipped"}


def _quote(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


class JobManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self._prompts: dict[int, str] = {}
        self._waves: dict[int, list[list[int]]] = {}
        self._results: dict[int, str] = {}
        self._reconcile_meta: dict[int, dict] = {}
        self._sem: asyncio.Semaphore | None = None

    # ---------- helpers ----------

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(
                int(config.load_config().get("max_parallel", 3)))
        return self._sem

    @staticmethod
    def job_dict(conn: sqlite3.Connection, job_id: int) -> dict:
        row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        return dbm.row_to_dict(row, dbm.JOB_JSON) if row else {}

    def _pub_job(self, conn: sqlite3.Connection, job_id: int, project_id: int) -> None:
        bus.publish(project_id, {"type": "job.updated",
                                 "job": self.job_dict(conn, job_id)})

    @staticmethod
    def default_executor_id(conn: sqlite3.Connection, project_row) -> int | None:
        """SPEC §7: unset executor falls back to the project default, then to
        the first enabled executor (so outcomes are always attributable)."""
        settings = json.loads(project_row["settings_json"] or "{}")
        did = settings.get("default_executor_id")
        if did and conn.execute("SELECT 1 FROM executor WHERE id=? AND enabled=1",
                                (did,)).fetchone():
            return did
        row = conn.execute(
            "SELECT id FROM executor WHERE enabled=1 ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else None

    @staticmethod
    def _executor_row(conn: sqlite3.Connection, executor_id: int | None) -> dict | None:
        if executor_id is None:
            return None
        r = conn.execute("SELECT * FROM executor WHERE id=?", (executor_id,)).fetchone()
        return dict(r) if r else None

    # ---------- creation ----------

    def create_job(self, conn: sqlite3.Connection, project_id: int, kind: str,
                   spec_ids: list[int] | None = None, executor_id: int | None = None,
                   prompt: str = "", parent_job_id: int | None = None,
                   status: str = "queued", error: str | None = None) -> dict:
        proj = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        settings = json.loads(proj["settings_json"] or "{}")
        command = ""
        if kind != "build_batch" and status == "queued":
            command = ex.describe_command(
                self._executor_row(conn, executor_id), prompt, config.load_config(),
                bool(settings.get("permission_escalation")))
        cur = conn.execute(
            "INSERT INTO job (project_id, kind, spec_ids_json, parent_job_id, status,"
            " executor_id, command, error, log_path, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, kind, json.dumps(spec_ids or []), parent_job_id, status,
             executor_id, command, error, "", dbm.now()),
        )
        job_id = cur.lastrowid
        log_path = config.state_dir() / "logs" / f"{job_id}.ndjson"
        conn.execute("UPDATE job SET log_path=? WHERE id=?", (str(log_path), job_id))
        conn.commit()
        self._prompts[job_id] = prompt
        self._pub_job(conn, job_id, project_id)
        return self.job_dict(conn, job_id)

    def launch(self, job_id: int) -> None:
        task = asyncio.get_running_loop().create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

    async def cancel(self, job_id: int) -> bool:
        proc = self._procs.get(job_id)
        if proc is not None:
            _kill_tree(proc)
        task = self._tasks.get(job_id)
        conn = dbm.connect()
        try:
            row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] in TERMINAL:
                return False
            conn.execute("UPDATE job SET status='canceled', finished_at=? WHERE id=?",
                         (dbm.now(), job_id))
            conn.commit()
            self._pub_job(conn, job_id, row["project_id"])
        finally:
            conn.close()
        if task and proc is None:
            task.cancel()
        return True

    # ---------- execution ----------

    async def _run(self, job_id: int) -> None:
        conn = dbm.connect()
        try:
            job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
            if job is None or job["status"] != "queued":
                return
            proj = conn.execute("SELECT * FROM project WHERE id=?",
                                (job["project_id"],)).fetchone()
            try:
                if job["kind"] == "build":
                    await self._run_build(conn, dict(job), dict(proj))
                elif job["kind"] == "build_batch":
                    await self._run_batch(conn, dict(job), dict(proj))
                elif job["kind"] == "reconcile":
                    await self._run_reconcile(conn, dict(job), dict(proj))
                else:
                    await self._run_simple(conn, dict(job), dict(proj))
            except Exception as e:  # defensive: a job must always reach a terminal state
                conn.execute(
                    "UPDATE job SET status='failed', error=?, finished_at=? WHERE id=?",
                    (f"{type(e).__name__}: {e}", dbm.now(), job_id))
                conn.commit()
                self._pub_job(conn, job_id, job["project_id"])
                if job["kind"] == "build":
                    # a crash mid-build must not strand the spec in 'building'
                    # (which disables rebuilds); re-import restores the file's
                    # status since the importer doesn't preserve 'building'
                    try:
                        importer.import_project(conn, job["project_id"])
                        bus.publish(job["project_id"], {"type": "specs.updated"})
                    except Exception:
                        pass
        finally:
            self._procs.pop(job_id, None)
            self._prompts.pop(job_id, None)
            self._waves.pop(job_id, None)
            self._results.pop(job_id, None)
            self._reconcile_meta.pop(job_id, None)
            conn.close()

    async def _exec(self, conn: sqlite3.Connection, job: dict, cwd: Path,
                    executor: dict | None) -> bool:
        """Run the subprocess, stream events, finalize the job row.
        Returns True on success."""
        job_id, project_id = job["id"], job["project_id"]
        cfg = config.load_config()
        prompt = self._prompts.get(job_id, "")
        proj = conn.execute("SELECT settings_json FROM project WHERE id=?",
                            (project_id,)).fetchone()
        escalate = bool(json.loads(proj["settings_json"] or "{}")
                        .get("permission_escalation"))
        if (executor or {}).get("backend") == "claude_sdk":
            return await self._exec_sdk(conn, job, cwd, executor, escalate)
        argv = ex.build_argv(executor, prompt, cfg, escalate)
        conn.execute("UPDATE job SET status='running', started_at=?, command=? WHERE id=?",
                     (dbm.now(), _quote(argv), job_id))
        conn.commit()
        self._pub_job(conn, job_id, project_id)

        log_path = Path(conn.execute("SELECT log_path FROM job WHERE id=?",
                                     (job_id,)).fetchone()[0])
        state = {"usage": {}, "is_error": False, "result_text": "", "pr_url": None,
                 "total_cost_usd": None}

        def handle_line(line: str, stream_name: str) -> None:
            line = line.rstrip("\r\n")
            if not line:
                return
            try:
                evt = json.loads(line)
                if not isinstance(evt, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                evt = {"type": "stderr" if stream_name == "stderr" else "raw",
                       "text": line}
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt) + "\n")
            if evt.get("type") == "result":
                state["usage"] = evt.get("usage") or {}
                state["is_error"] = bool(evt.get("is_error"))
                state["result_text"] = str(evt.get("result") or "")
                state["total_cost_usd"] = evt.get("total_cost_usd")
            m = PR_RE.search(line)
            if m:
                state["pr_url"] = m.group(0)
            bus.publish(project_id, {"type": "job.log", "job_id": job_id, "event": evt})

        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                # stream-json lines carry whole file contents inside tool
                # results; asyncio's default 64KB readline limit is far too
                # small ("Separator is found, but chunk is longer than limit")
                limit=64 * 1024 * 1024)
        except (OSError, FileNotFoundError) as e:
            conn.execute("UPDATE job SET status='failed', error=?, finished_at=? WHERE id=?",
                         (f"failed to start: {e}", dbm.now(), job_id))
            conn.commit()
            self._pub_job(conn, job_id, project_id)
            return False
        self._procs[job_id] = proc

        async def pump(stream, name):
            while True:
                line = await stream.readline()
                if not line:
                    break
                handle_line(line.decode("utf-8", errors="replace"), name)

        timeout_s = float(cfg.get("job_timeout_min", 30)) * 60
        pump_error: str | None = None
        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr")),
                timeout=timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc)
        except Exception as e:
            # a reader failure must never leave the agent running orphaned
            pump_error = f"stream reader failed: {type(e).__name__}: {e}"
            _kill_tree(proc)
        code = await proc.wait()
        self._procs.pop(job_id, None)

        # a cancel() may have marked the row already
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        if row and row["status"] == "canceled":
            return False

        success = (code == 0 and not state["is_error"] and not timed_out
                   and pump_error is None)
        cost = state["total_cost_usd"]
        if cost is None:
            cost = ex.compute_cost(state["usage"], executor)
        error = None
        if timed_out:
            error = f"timed out after {int(timeout_s)}s"
        elif pump_error:
            error = pump_error
        elif not success:
            error = (state["result_text"] or f"exit code {code}")[:2000]
        conn.execute(
            "UPDATE job SET status=?, exit_code=?, usage_json=?, cost_usd=?, pr_url=?,"
            " error=?, finished_at=? WHERE id=?",
            ("succeeded" if success else "failed", code, json.dumps(state["usage"]),
             cost, state["pr_url"], error, dbm.now(), job_id))
        conn.commit()
        self._results[job_id] = state["result_text"]
        self._pub_job(conn, job_id, project_id)
        return success

    async def _exec_sdk(self, conn: sqlite3.Connection, job: dict, cwd: Path,
                        executor: dict, escalate: bool) -> bool:
        """v2: run via the Claude Agent SDK in-process. Events are normalized
        to the same stream-json dict shapes the CLI backend produces, so the
        log file, WebSocket channel, and UI are backend-agnostic."""
        job_id, project_id = job["id"], job["project_id"]
        cfg = config.load_config()
        prompt = self._prompts.get(job_id, "")
        conn.execute("UPDATE job SET status='running', started_at=?, command=? WHERE id=?",
                     (dbm.now(), f"[claude-agent-sdk] {prompt}", job_id))
        conn.commit()
        self._pub_job(conn, job_id, project_id)
        log_path = Path(conn.execute("SELECT log_path FROM job WHERE id=?",
                                     (job_id,)).fetchone()[0])
        state = {"usage": {}, "is_error": False, "result_text": "", "pr_url": None,
                 "total_cost_usd": None, "seen_result": False}

        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            conn.execute(
                "UPDATE job SET status='failed', error=?, finished_at=? WHERE id=?",
                ("claude-agent-sdk is not installed — pip install claude-agent-sdk",
                 dbm.now(), job_id))
            conn.commit()
            self._pub_job(conn, job_id, project_id)
            return False

        options = ClaudeAgentOptions(
            cwd=str(cwd),
            permission_mode="bypassPermissions" if escalate else "acceptEdits",
            model=(executor or {}).get("model") or None,
        )

        def ingest(evt: dict) -> None:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt) + "\n")
            if evt.get("type") == "result":
                state["seen_result"] = True
                state["usage"] = evt.get("usage") or {}
                state["is_error"] = bool(evt.get("is_error"))
                state["result_text"] = str(evt.get("result") or "")
                state["total_cost_usd"] = evt.get("total_cost_usd")
            m = PR_RE.search(json.dumps(evt))
            if m:
                state["pr_url"] = m.group(0)
            bus.publish(project_id, {"type": "job.log", "job_id": job_id, "event": evt})

        timed_out = False
        error: str | None = None
        timeout_s = float(cfg.get("job_timeout_min", 30)) * 60

        async def run() -> None:
            async for msg in query(prompt=prompt, options=options):
                ingest(_sdk_event(msg))

        try:
            await asyncio.wait_for(run(), timeout=timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
        except Exception as e:  # SDK/CLI failures surface as exceptions here
            error = f"{type(e).__name__}: {e}"

        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        if row and row["status"] == "canceled":
            return False
        success = (state["seen_result"] and not state["is_error"]
                   and not timed_out and error is None)
        cost = state["total_cost_usd"]
        if cost is None:
            cost = ex.compute_cost(state["usage"], executor)
        if timed_out:
            error = f"timed out after {int(timeout_s)}s"
        elif not success and error is None:
            error = (state["result_text"] or "sdk run produced no result")[:2000]
        conn.execute(
            "UPDATE job SET status=?, usage_json=?, cost_usd=?, pr_url=?, error=?,"
            " finished_at=? WHERE id=?",
            ("succeeded" if success else "failed", json.dumps(state["usage"]), cost,
             state["pr_url"], None if success else error, dbm.now(), job_id))
        conn.commit()
        self._results[job_id] = state["result_text"]
        self._pub_job(conn, job_id, project_id)
        return success

    async def _run_simple(self, conn: sqlite3.Connection, job: dict, proj: dict) -> None:
        """triage / spec / scaffold: run in the repo, re-import on success."""
        repo = Path(proj["repo_path"])
        wt.ensure_commands(repo)
        executor = self._executor_row(conn, job["executor_id"])
        success = await self._exec(conn, job, repo, executor)
        if success:
            importer.import_project(conn, proj["id"])
            bus.publish(proj["id"], {"type": "graph.updated"})
            bus.publish(proj["id"], {"type": "specs.updated"})

    async def _run_build(self, conn: sqlite3.Connection, job: dict, proj: dict) -> None:
        spec_ids = json.loads(job["spec_ids_json"])
        spec = conn.execute("SELECT * FROM spec WHERE id=?", (spec_ids[0],)).fetchone()
        repo = Path(proj["repo_path"])
        prev_status = spec["status"]
        executor = self._executor_row(conn, job["executor_id"])

        async with self._semaphore():
            conn.execute("UPDATE spec SET status='building', updated_at=? WHERE id=?",
                         (dbm.now(), spec["id"]))
            conn.commit()
            bus.publish(proj["id"], {"type": "specs.updated"})

            spec_key = f"{spec['number']:04d}-{spec['slug']}"
            try:
                path, branch = wt.create_worktree(
                    repo, proj["slug"], spec_key, proj["default_branch"])
            except RuntimeError as e:
                conn.execute("UPDATE job SET status='failed', error=?, finished_at=?"
                             " WHERE id=?", (str(e), dbm.now(), job["id"]))
                conn.execute("UPDATE spec SET status=?, updated_at=? WHERE id=?",
                             (prev_status, dbm.now(), spec["id"]))
                conn.commit()
                self._pub_job(conn, job["id"], proj["id"])
                bus.publish(proj["id"], {"type": "specs.updated"})
                return
            wt.ensure_commands(path, overwrite=False)
            conn.execute("UPDATE job SET worktree_path=?, branch=? WHERE id=?",
                         (str(path), branch, job["id"]))
            conn.commit()

            success = await self._exec(conn, job, path, executor)

        jrow = self.job_dict(conn, job["id"])
        actual: list[dict] = []
        if success:
            # /build contract: at least one commit on the branch. An agent
            # that exits cleanly WITHOUT committing (e.g. blocked, or just
            # reporting) is not a successful build — and deleting its
            # worktree would destroy uncommitted work.
            code_c, out_c = wt.run_git(
                ["rev-list", "--count", f"{proj['default_branch']}..HEAD"],
                Path(jrow["worktree_path"]))
            if code_c != 0 or not out_c.strip().isdigit() or int(out_c) == 0:
                success = False
                conn.execute(
                    "UPDATE job SET status='failed', outcome='failure', error=?"
                    " WHERE id=?",
                    ("agent finished without committing — see its final report"
                     f" in the log; worktree kept at {jrow['worktree_path']}",
                     job["id"]))
        if success:
            provenance = {"branch": jrow.get("branch"), "pr_url": jrow.get("pr_url"),
                          "built_at": dbm.now(), "job_id": job["id"]}
            actual = reconcile.capture_actual_files(
                repo, proj["default_branch"], jrow.get("branch"), spec["file_path"])
            conn.execute(
                "UPDATE spec SET status='built', provenance_json=?, files_actual_json=?,"
                " updated_at=? WHERE id=?",
                (json.dumps(provenance), json.dumps(actual), dbm.now(), spec["id"]))
            conn.execute("UPDATE job SET outcome='success' WHERE id=?", (job["id"],))
            wt.remove_worktree(repo, Path(jrow["worktree_path"]))
        else:
            conn.execute("UPDATE spec SET status=?, updated_at=? WHERE id=?",
                         (prev_status, dbm.now(), spec["id"]))
            if jrow.get("status") == "failed":
                conn.execute("UPDATE job SET outcome='failure' WHERE id=?", (job["id"],))
            # worktree kept for inspection
        conn.commit()
        if executor and jrow.get("status") in ("succeeded", "failed"):
            _record_outcome(conn, executor["id"], success, jrow.get("cost_usd"))
        if success:
            await self._reconcile_after_build(conn, proj, dict(spec), actual, job["id"])
        bus.publish(proj["id"], {"type": "specs.updated"})
        self._pub_job(conn, job["id"], proj["id"])

    async def _reconcile_after_build(self, conn: sqlite3.Connection, proj: dict,
                                     spec: dict, actual: list[dict],
                                     build_job_id: int) -> None:
        """v1 reconcile (§10): re-derive the graph with the built spec's actual
        files, flag specs whose conflict set changed as ``stale``, and launch a
        proposal job for each. Proposals are stored, never auto-applied."""
        stale = reconcile.mark_stale_after_build(conn, proj["id"], spec["id"])
        bus.publish(proj["id"], {"type": "graph.updated"})
        if not stale:
            return
        for st in stale:
            prompt = reconcile.build_prompt(
                spec["number"], spec["title"], actual, st["body_md"])
            child = self.create_job(
                conn, proj["id"], "reconcile", [st["id"]],
                executor_id=self.default_executor_id(conn, proj),
                prompt=prompt, parent_job_id=build_job_id)
            self._reconcile_meta[child["id"]] = {
                "stale_spec_id": st["id"], "trigger_spec_id": spec["id"],
                "prior_status": st["prior_status"]}
            self.launch(child["id"])
        bus.publish(proj["id"], {"type": "specs.updated"})

    async def _run_reconcile(self, conn: sqlite3.Connection, job: dict,
                             proj: dict) -> None:
        """Run one stale spec's proposal pass in the repo; store the result."""
        repo = Path(proj["repo_path"])
        wt.ensure_commands(repo)
        executor = self._executor_row(conn, job["executor_id"])
        meta = self._reconcile_meta.get(job["id"], {})
        success = await self._exec(conn, job, repo, executor)
        result_text = (self._results.get(job["id"]) or "").strip()
        if not (success and meta):
            return
        spec_id = meta["stale_spec_id"]
        prior = meta.get("prior_status", "draft")
        no_change = (not result_text
                     or result_text.upper().startswith(reconcile.NO_CHANGES))
        conn.execute(
            "INSERT INTO reconcile_proposal (project_id, spec_id, trigger_spec_id,"
            " job_id, prior_status, proposed_body, no_change, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (proj["id"], spec_id, meta.get("trigger_spec_id"), job["id"], prior,
             "" if no_change else result_text, int(no_change),
             "rejected" if no_change else "pending", dbm.now()))
        if no_change:
            conn.execute(
                "UPDATE spec SET status=?, updated_at=? WHERE id=? AND status='stale'",
                (prior, dbm.now(), spec_id))
        conn.commit()
        bus.publish(proj["id"], {"type": "specs.updated"})
        bus.publish(proj["id"], {"type": "proposals.updated"})

    async def _run_batch(self, conn: sqlite3.Connection, job: dict, proj: dict) -> None:
        waves = self._waves.get(job["id"]) or []
        conn.execute("UPDATE job SET status='running', started_at=?, command=? WHERE id=?",
                     (dbm.now(), json.dumps({"waves": waves}), job["id"]))
        conn.commit()
        self._pub_job(conn, job["id"], proj["id"])

        edges = [dict(r) for r in conn.execute(
            "SELECT spec_a, spec_b FROM edge WHERE project_id=?", (proj["id"],))]
        conflict_pairs = {(e["spec_a"], e["spec_b"]) for e in edges}

        def conflicts_with_failed(sid: int, failed: set[int]) -> bool:
            return any((sid, f) in conflict_pairs or (f, sid) in conflict_pairs
                       for f in failed)

        failed: set[int] = set()
        any_failure = False
        for wave in waves:
            children: list[tuple[int, int]] = []  # (job_id, spec_id)
            for sid in wave:
                srow = conn.execute("SELECT * FROM spec WHERE id=?", (sid,)).fetchone()
                if srow is None:
                    continue
                if conflicts_with_failed(sid, failed):
                    self.create_job(
                        conn, proj["id"], "build", [sid],
                        parent_job_id=job["id"], status="skipped",
                        error="skipped_due_to_conflict: a conflicting spec failed"
                              " in an earlier wave")
                    continue
                child = self.create_job(
                    conn, proj["id"], "build", [sid],
                    executor_id=srow["executor_id"]
                    or self.default_executor_id(conn, proj),
                    prompt=f"/build {srow['file_path']}",
                    parent_job_id=job["id"])
                self.launch(child["id"])
                children.append((child["id"], sid))
            # wait for every child in this wave to reach a terminal state
            for cid, sid in children:
                t = self._tasks.get(cid)
                if t:
                    await t
                crow = conn.execute("SELECT status FROM job WHERE id=?", (cid,)).fetchone()
                if crow and crow["status"] != "succeeded":
                    failed.add(sid)
                    any_failure = True

        conn.execute(
            "UPDATE job SET status=?, error=?, finished_at=? WHERE id=?",
            ("succeeded" if not any_failure else "failed",
             None if not any_failure else
             f"{len(failed)} spec build(s) failed or were skipped",
             dbm.now(), job["id"]))
        conn.commit()
        self._pub_job(conn, job["id"], proj["id"])

    def set_waves(self, job_id: int, waves: list[list[int]]) -> None:
        self._waves[job_id] = waves


def _record_outcome(conn: sqlite3.Connection, executor_id: int, success: bool,
                    cost: float | None) -> None:
    row = conn.execute("SELECT * FROM executor WHERE id=?", (executor_id,)).fetchone()
    if row is None:
        return
    successes = row["successes"] + (1 if success else 0)
    failures = row["failures"] + (0 if success else 1)
    avg = row["avg_build_cost_usd"]
    if cost is not None:
        n = successes + failures
        avg = cost if avg is None else avg + (cost - avg) / n
    conn.execute(
        "UPDATE executor SET successes=?, failures=?, avg_build_cost_usd=? WHERE id=?",
        (successes, failures, avg, executor_id))
    conn.commit()


def _sdk_event(msg) -> dict:
    """Normalize a claude-agent-sdk message object to the CLI's stream-json
    shape (duck-typed so tests can stub the SDK module)."""
    t = type(msg).__name__
    if t == "AssistantMessage":
        content = []
        for b in getattr(msg, "content", None) or []:
            bt = type(b).__name__
            if bt == "TextBlock":
                content.append({"type": "text", "text": getattr(b, "text", "")})
            elif bt == "ToolUseBlock":
                content.append({"type": "tool_use", "name": getattr(b, "name", "?")})
        return {"type": "assistant", "message": {"content": content}}
    if t == "ResultMessage":
        usage = getattr(msg, "usage", None) or {}
        if not isinstance(usage, dict):
            usage = {k: v for k, v in vars(usage).items()
                     if isinstance(v, (int, float))}
        return {"type": "result", "is_error": bool(getattr(msg, "is_error", False)),
                "result": getattr(msg, "result", "") or "",
                "usage": usage,
                "total_cost_usd": getattr(msg, "total_cost_usd", None)}
    if t == "SystemMessage":
        return {"type": "system", "subtype": getattr(msg, "subtype", "")}
    return {"type": "sdk_event", "event": t}


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


manager = JobManager()
