"""v2: risk parsing + ordering, executor backends, project delete."""
import json
import sys
import subprocess
import types
from pathlib import Path

import pytest

from spindlegraph import graph, importer
from test_orchestrator_api import (  # noqa: F401 (fixtures)
    add_project, client, git_repo, spec_by_number, wait_job,
)

FAKE_LOCAL = Path(__file__).resolve().parent / "fake_local.py"

RISKY = """---
title: Risky migration
status: decided
---

# Risky migration

## Affected files
- `src/config.py`

## Risk
- **Involvement:** Involved — touches every service's config load path
- **Review attention:** High — prod migration with carry-forward
"""


def test_risk_parsing(repo):
    p = repo / "specs" / "0021-risky-migration.md"
    p.write_text(RISKY, encoding="utf-8")
    rec = importer.parse_spec_file(p, repo)
    assert rec["risk"] == {
        "involvement": "involved",
        "involvement_note": "touches every service's config load path",
        "review": "high",
        "review_note": "prod migration with carry-forward",
    }


def test_risk_missing_is_empty(repo):
    p = repo / "specs" / "0022-no-risk.md"
    p.write_text("# No risk section\n\n## Affected files\n- `a.py`\n", encoding="utf-8")
    assert importer.parse_spec_file(p, repo)["risk"] == {}


def _spec(i, number, files, risk=None):
    return {"id": i, "number": number, "status": "draft",
            "files_planned": [{"path": p} for p in files], "files_actual": [],
            "depends_on": [], "risk": risk or {}}


def test_risk_score():
    assert graph.risk_score(_spec(1, 1, [])) == 0
    assert graph.risk_score(_spec(1, 1, [], {"involvement": "involved",
                                             "review": "high"})) == 4
    assert graph.risk_score(_spec(1, 1, [], {"review": "medium"})) == 1


def test_risky_spec_scheduled_first():
    # A (high risk) conflicts with B; C independent. Without risk, B (lower
    # number, same degree) would land in wave 1 — risk flips it.
    a = _spec(10, 5, ["core.py"], {"involvement": "involved", "review": "high"})
    b = _spec(11, 1, ["core.py"])
    c = _spec(12, 2, ["other.py"])
    edges = graph.compute_edges([a, b, c])
    waves = graph.suggest_waves([a, b, c], edges, [10, 11, 12])
    assert waves == [[10, 12], [11]]  # risky A first (and listed first in-wave)
    # sanity: without risk the tie breaks the other way
    a2 = _spec(10, 5, ["core.py"])
    waves2 = graph.suggest_waves([a2, b, c], graph.compute_edges([a2, b, c]),
                                 [10, 11, 12])
    assert waves2 == [[11, 12], [10]]


def test_local_cli_backend_build(client, git_repo):
    proj = add_project(client, git_repo)
    r = client.post("/api/executors", json={
        "name": "Local agent", "backend": "local_cli",
        "command_template": f'"{sys.executable}" "{FAKE_LOCAL}" {{prompt}}',
        "prior_success": 0.6,
    })
    assert r.status_code == 200, r.text
    exec_id = r.json()["id"]
    s9 = spec_by_number(client, proj["id"], 9)
    job = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                         "spec_ids": [s9["id"]],
                                         "executor_id": exec_id}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded", done
    assert done["pr_url"] == "https://github.com/acme/demo/pull/777"
    events = client.get(f"/api/jobs/{job['id']}").json()["log_events"]
    assert any(e.get("type") == "raw" and "local agent starting" in e.get("text", "")
               for e in events)
    ex = next(e for e in client.get("/api/executors").json() if e["id"] == exec_id)
    assert ex["successes"] == 1


def test_large_stream_lines_survive(client, git_repo, monkeypatch):
    """Real claude events can exceed asyncio's default 64KB readline limit
    (one Read result = whole file in one JSON line). Regression: this used to
    kill the reader with 'Separator is found, but chunk is longer than limit'
    and leave the job failed."""
    monkeypatch.setenv("FAKE_CLAUDE_BIGLINE", "1")
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    events = client.get(f"/api/jobs/{job['id']}").json()["log_events"]
    big = [e for e in events if e.get("type") == "assistant"
           and len(json.dumps(e)) > 200_000]
    assert big, "large event should have streamed through intact"


def test_executor_backend_validation(client):
    assert client.post("/api/executors", json={
        "name": "bogus", "backend": "nope"}).status_code == 400
    assert client.post("/api/executors", json={
        "name": "no-template", "backend": "local_cli"}).status_code == 400


def test_sdk_backend_stubbed(client, git_repo, monkeypatch):
    """claude_sdk backend, with the SDK module stubbed: verifies the event
    normalization, success detection, and cost capture without credentials."""
    class TextBlock:
        def __init__(self, text): self.text = text

    class AssistantMessage:
        def __init__(self): self.content = [TextBlock("sdk says hi")]

    class ResultMessage:
        is_error = False
        result = "sdk done"
        usage = {"input_tokens": 50, "output_tokens": 10}
        total_cost_usd = 0.42

    class ClaudeAgentOptions:
        def __init__(self, **kw): self.__dict__.update(kw)

    async def query(prompt, options):
        yield AssistantMessage()
        yield ResultMessage()

    stub = types.ModuleType("claude_agent_sdk")
    stub.ClaudeAgentOptions = ClaudeAgentOptions
    stub.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", stub)

    proj = add_project(client, git_repo)
    r = client.post("/api/executors", json={"name": "SDK", "backend": "claude_sdk",
                                            "model": "claude-opus-4-8"})
    exec_id = r.json()["id"]
    job = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "scaffold",
                                         "executor_id": exec_id}).json()
    assert job["command"].startswith("[claude-agent-sdk]")
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded", done
    assert done["cost_usd"] == pytest.approx(0.42)
    events = client.get(f"/api/jobs/{job['id']}").json()["log_events"]
    assert {"type": "assistant",
            "message": {"content": [{"type": "text", "text": "sdk says hi"}]}} in events


def test_sdk_backend_missing_module(client, git_repo, monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # forces ImportError
    proj = add_project(client, git_repo)
    exec_id = client.post("/api/executors", json={
        "name": "SDK-missing", "backend": "claude_sdk"}).json()["id"]
    job = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "scaffold",
                                         "executor_id": exec_id}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "failed"
    assert "claude-agent-sdk is not installed" in done["error"]


def test_delete_project(client, git_repo):
    proj = add_project(client, git_repo)
    assert client.delete(f"/api/projects/{proj['id']}").json() == {"deleted": True}
    assert client.get(f"/api/projects/{proj['id']}").status_code == 404
    # target repo untouched
    assert (git_repo / "specs" / "0007-add-rate-limiting.md").exists()


def test_allowed_tools_passed_to_cli(state_home):
    from spindlegraph import config as cfgm
    from spindlegraph.orchestrator import executors as exm
    cfg = cfgm.load_config()
    argv = exm.build_argv(None, "/build specs/x.md", cfg)
    i = argv.index("--allowedTools")
    rules = argv[i + 1]
    assert "Bash(git:*)" in rules and "Bash(gh:*)" in rules and "Bash(npm:*)" in rules
    # escalation drops the allowlist (bypass covers everything)
    argv2 = exm.build_argv(None, "p", cfg, escalate=True)
    assert "--allowedTools" not in argv2 and "--dangerously-skip-permissions" in argv2


def test_clean_exit_without_commit_is_failure(client, git_repo, monkeypatch):
    """Regression: an agent that exits 0 but never commits (e.g. blocked on
    permissions) used to count as success — and the success-path cleanup
    destroyed its uncommitted worktree."""
    monkeypatch.setenv("FAKE_CLAUDE_NO_COMMIT", "1")
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed"
    assert "without committing" in job["error"]
    assert Path(job["worktree_path"]).exists()   # work preserved
    assert spec_by_number(client, proj["id"], 9)["status"] == "decided"


def test_explicit_status_patch_overrides_built_preservation(client, git_repo):
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    wait_job(client, r.json()["id"])
    assert spec_by_number(client, proj["id"], 9)["status"] == "built"
    client.patch(f"/api/specs/{s9['id']}", json={"status": "decided"})
    assert spec_by_number(client, proj["id"], 9)["status"] == "decided"


def test_implemented_folder_imports_as_built(conn, repo):
    from conftest import make_project
    from spindlegraph import importer as imp
    imp_dir = repo / "specs" / "implemented"
    imp_dir.mkdir(parents=True)
    (imp_dir / "0030-shipped-thing.md").write_text(
        "---\ntitle: Shipped thing\nstatus: draft\n---\n\n# Shipped thing\n\n"
        "## Affected files\n- `src/config.py`\n", encoding="utf-8")
    pid = make_project(conn, repo)
    imp.import_project(conn, pid)
    row = conn.execute("SELECT status, file_path FROM spec WHERE number=30").fetchone()
    assert row["status"] == "built"          # location implies status
    assert row["file_path"] == "specs/implemented/0030-shipped-thing.md"


def test_feedback_revises_built_spec_on_its_branch(client, git_repo):
    """After a build, feedback runs a follow-up on the SAME branch, adds a new
    commit, and keeps the spec built."""
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    b = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    wait_job(client, b.json()["id"])
    assert spec_by_number(client, proj["id"], 9)["status"] == "built"

    head_before = subprocess.run(
        ["git", "rev-parse", "spec/0009-fix-login-redirect"], cwd=git_repo,
        capture_output=True, text=True).stdout.strip()

    fb = client.post("/api/jobs", json={
        "project_id": proj["id"], "kind": "feedback", "spec_ids": [s9["id"]],
        "idea": "the redirect still loops on mobile"})
    assert fb.status_code == 200, fb.text
    job = wait_job(client, fb.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["branch"] == "spec/0009-fix-login-redirect"

    head_after = subprocess.run(
        ["git", "rev-parse", "spec/0009-fix-login-redirect"], cwd=git_repo,
        capture_output=True, text=True).stdout.strip()
    assert head_after != head_before        # new commit on the same branch
    assert spec_by_number(client, proj["id"], 9)["status"] == "built"


def test_feedback_requires_built_spec(client, git_repo):
    proj = add_project(client, git_repo)
    s14 = spec_by_number(client, proj["id"], 14)   # decided, never built
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "feedback",
                                       "spec_ids": [s14["id"]], "idea": "x"})
    assert r.status_code == 409


def test_feedback_no_commit_is_failure(client, git_repo, monkeypatch):
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    wait_job(client, client.post("/api/jobs", json={
        "project_id": proj["id"], "kind": "build", "spec_ids": [s9["id"]]}).json()["id"])
    monkeypatch.setenv("FAKE_CLAUDE_NO_COMMIT", "1")
    fb = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "feedback",
                                        "spec_ids": [s9["id"]], "idea": "still broken"})
    job = wait_job(client, fb.json()["id"])
    assert job["status"] == "failed"
    assert "no new commit" in job["error"]


def test_feedback_lock_denied_host_finalizes(client, git_repo, monkeypatch):
    """Codex-style sandbox: agent makes the fix but can't write index.lock.
    The host runner commits it on the branch so the revision isn't lost."""
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    wait_job(client, client.post("/api/jobs", json={
        "project_id": proj["id"], "kind": "build", "spec_ids": [s9["id"]]}).json()["id"])
    head_before = subprocess.run(
        ["git", "rev-parse", "spec/0009-fix-login-redirect"], cwd=git_repo,
        capture_output=True, text=True).stdout.strip()

    monkeypatch.setenv("FAKE_CLAUDE_LOCK_DENIED", "1")
    fb = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "feedback",
                                        "spec_ids": [s9["id"]], "idea": "still broken"})
    job = wait_job(client, fb.json()["id"])
    assert job["status"] == "succeeded", job
    assert "host_finalize" in json.dumps(job["log_events"]).lower()
    head_after = subprocess.run(
        ["git", "rev-parse", "spec/0009-fix-login-redirect"], cwd=git_repo,
        capture_output=True, text=True).stdout.strip()
    assert head_after != head_before   # host committed the agent's fix


def test_open_pr_for_built_spec_without_pr(client, git_repo, monkeypatch):
    """A built spec whose branch has no PR gets one via the host on demand."""
    from spindlegraph.orchestrator import worktrees as wtm
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    wait_job(client, client.post("/api/jobs", json={
        "project_id": proj["id"], "kind": "build", "spec_ids": [s9["id"]]}).json()["id"])
    # simulate a build that committed+pushed but never opened a PR
    from spindlegraph import db as dbm
    conn = dbm.connect()
    conn.execute("UPDATE spec SET provenance_json=? WHERE id=?",
                 (json.dumps({"branch": "spec/0009-fix-login-redirect", "pr_url": None,
                              "job_id": 1}), s9["id"]))
    conn.commit(); conn.close()

    monkeypatch.setattr(wtm, "open_or_get_pr",
                        lambda repo, branch, base: (
                            "https://github.com/acme/demo/pull/909", "PR created"))
    r = client.post(f"/api/specs/{s9['id']}/open-pr")
    assert r.status_code == 200, r.text
    assert r.json()["pr_url"].endswith("/pull/909")
    assert spec_by_number(client, proj["id"], 9)["provenance"]["pr_url"].endswith("/pull/909")


def test_open_pr_rejected_when_not_built(client, git_repo):
    proj = add_project(client, git_repo)
    s14 = spec_by_number(client, proj["id"], 14)  # decided, never built
    assert client.post(f"/api/specs/{s14['id']}/open-pr").status_code == 409


def test_build_reimports_and_blocks_on_file_decisions(client, git_repo):
    """Editing a spec file to ADD an unresolved decision (outside the drawer)
    must block the build even if the DB was last imported without it."""
    proj = add_project(client, git_repo)
    s14 = spec_by_number(client, proj["id"], 14)  # decided, no decisions
    # edit the file directly (as if via git/editor), then don't re-import
    p = Path(git_repo) / s14["file_path"]
    p.write_text(p.read_text(encoding="utf-8") +
                 "\n## Decisions needed\n- [ ] which color scheme?\n", encoding="utf-8")
    # DB still shows 0 decisions until the build re-syncs
    assert spec_by_number(client, proj["id"], 14)["decisions"] == []
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s14["id"]]})
    assert r.status_code == 409
    # and the re-sync updated the DB so the board now shows the decision
    assert len(spec_by_number(client, proj["id"], 14)["decisions"]) == 1


def test_import_if_changed_gate(client, git_repo):
    """?if_changed=true re-imports only when a spec file actually changed."""
    proj = add_project(client, git_repo)
    # first if_changed call: fingerprint unset -> imports (changed True)
    r1 = client.post(f"/api/jobs" if False else
                     f"/api/projects/{proj['id']}/import?if_changed=true")
    assert r1.json()["changed"] is True
    # nothing moved -> no-op
    r2 = client.post(f"/api/projects/{proj['id']}/import?if_changed=true")
    assert r2.json()["changed"] is False
    # edit a spec file on disk -> detected
    p = Path(git_repo) / "specs" / "0014-dark-mode.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n<!-- touched -->\n", encoding="utf-8")
    r3 = client.post(f"/api/projects/{proj['id']}/import?if_changed=true")
    assert r3.json()["changed"] is True
    # a new spec file appearing is detected
    (Path(git_repo) / "specs" / "0099-new.md").write_text(
        "# New\n## Affected files\n- `x.py`\n", encoding="utf-8")
    assert client.post(f"/api/projects/{proj['id']}/import?if_changed=true").json()["changed"] is True


def test_limit_classification(client, git_repo):
    from spindlegraph.orchestrator.jobs import classify_limit
    assert classify_limit("You've hit your monthly spend limit · raise it at "
                          "claude.ai/settings/usage") == "spend_capped"
    assert classify_limit("Error 429: rate limit exceeded, retry later") == "rate_limited"
    assert classify_limit("overloaded_error: the model is overloaded") == "rate_limited"
    assert classify_limit("agent finished without committing") is None
    assert classify_limit("") is None


def test_spend_limit_job_surfaces_limit_hit(client, git_repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "spend")  # see fake_claude
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed"
    assert job["limit_hit"] == "spend_capped"
    # and the list endpoint carries it too
    listed = next(j for j in client.get(f"/api/jobs?project_id={proj['id']}").json()
                  if j["id"] == job["id"])
    assert listed["limit_hit"] == "spend_capped"


def test_batch_limit_failure_does_not_skip_neighbors(client, git_repo, monkeypatch):
    """A spend/rate-limit failure builds nothing, so it must NOT cascade-skip
    conflicting specs in later waves (they're fine — they just didn't run)."""
    proj = add_project(client, git_repo)
    # 7 and 9 conflict? use 7 (has decisions) — resolve first; make 7 & 9 share a file
    # simpler: two specs where the batch would skip a neighbor on failure.
    s7 = spec_by_number(client, proj["id"], 7)
    body = s7["body_md"].replace("- [ ] Counter store: redis or in-memory?",
                                 "- [x] Counter store → in-memory")
    # make 7 conflict with 9 by sharing src/auth_views.py
    body = body.replace("- `src/config.py` — settings",
                        "- `src/config.py` — settings\n- `src/auth_views.py`")
    client.patch(f"/api/specs/{s7['id']}", json={"body_md": body})
    s9 = spec_by_number(client, proj["id"], 9)

    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "spend")
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build_batch",
                                       "spec_ids": [s7["id"], s9["id"]],
                                       "waves": [[s7["id"]], [s9["id"]]]})
    batch = wait_job(client, r.json()["id"], timeout=60)
    jobs = client.get(f"/api/jobs?project_id={proj['id']}").json()
    children = [j for j in jobs if j.get("parent_job_id") == batch["id"]]
    # the spend-capped build of 7 must NOT have skipped 9
    skipped = [j for j in children if j["status"] == "skipped"]
    assert not skipped, f"limit failure wrongly skipped neighbors: {skipped}"


def test_dismiss_stale_clears_flag(client, git_repo, monkeypatch):
    """A spec stuck 'stale' (reconcile failed/never ran) can be dismissed back
    to its file's natural status."""
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)  # decided
    from spindlegraph import db as dbm
    conn = dbm.connect()
    conn.execute("UPDATE spec SET status='stale' WHERE id=?", (s9["id"],))
    conn.commit(); conn.close()
    assert spec_by_number(client, proj["id"], 9)["status"] == "stale"
    r = client.post(f"/api/specs/{s9['id']}/dismiss-stale")
    assert r.status_code == 200
    assert spec_by_number(client, proj["id"], 9)["status"] == "decided"
    # bulk endpoint
    conn = dbm.connect(); conn.execute("UPDATE spec SET status='stale' WHERE id=?",
                                       (s9["id"],)); conn.commit(); conn.close()
    assert client.post(f"/api/projects/{proj['id']}/dismiss-stale").json()["dismissed"] == 1
    assert spec_by_number(client, proj["id"], 9)["status"] == "decided"


def test_triage_grants_read_access_to_external_notes_doc(client, git_repo, tmp_path):
    """A notes doc OUTSIDE the repo (e.g. an Obsidian vault) can't be read by a
    repo-scoped headless agent. SpindleGraph passes --add-dir for the doc's
    directory so the agent can open it (embedding it would blow the ~32K
    Windows command-line limit for large docs)."""
    notes = tmp_path / "vault" / "ideas.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("- add dark mode\n- faster export\n", encoding="utf-8")
    proj = add_project(client, git_repo)
    client.patch(f"/api/projects/{proj['id']}", json={"notes_doc_path": str(notes)})
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "triage"})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    # the agent got read access to the doc's dir, not the embedded content
    assert "--add-dir" in job["command"]
    assert str(notes.parent) in job["command"]


def test_triage_missing_notes_doc_errors_clearly(client, git_repo):
    proj = add_project(client, git_repo)
    client.patch(f"/api/projects/{proj['id']}",
                 json={"notes_doc_path": "C:/nope/missing-notes.md"})
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "triage"})
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]


def test_triage_candidates_endpoint_parses_structured_block(client, git_repo, tmp_path):
    """After triage, its candidates are exposed for the pick-and-fan-out UI."""
    notes = tmp_path / "ideas.md"
    notes.write_text("- dark mode\n- export\n- sync\n", encoding="utf-8")
    proj = add_project(client, git_repo)
    client.patch(f"/api/projects/{proj['id']}", json={"notes_doc_path": str(notes)})
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "triage"})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    cands = client.get(f"/api/jobs/{job['id']}/triage-candidates").json()["candidates"]
    assert [c["title"] for c in cands] == ["Add dark mode", "Faster export", "Rewrite sync"]
    assert cands[0]["flag"] is None and cands[0]["size"] == "M"
    assert cands[2]["flag"] == "needs_clarification"


def test_triage_candidates_only_on_triage_jobs(client, git_repo):
    proj = add_project(client, git_repo)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "scaffold"})
    job = wait_job(client, r.json()["id"])
    resp = client.get(f"/api/jobs/{job['id']}/triage-candidates")
    assert resp.status_code == 400


def _wait_chat(client, chat_id, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = client.get(f"/api/spec-chats/{chat_id}").json()
        if not c["turn_running"]:
            return c
        time.sleep(0.15)
    raise TimeoutError(f"chat {chat_id} turn stuck")


def test_spec_chat_first_turn_asks_and_captures_session(client, git_repo):
    proj = add_project(client, git_repo)
    r = client.post("/api/spec-chats",
                    json={"project_id": proj["id"], "topic": "Add offline sync"})
    assert r.status_code == 200, r.text
    chat = _wait_chat(client, r.json()["id"])
    roles = [m["role"] for m in chat["messages"]]
    assert roles == ["user", "agent"]  # our opener + the agent's question
    assert "?" in chat["messages"][1]["text"]  # it asked something
    assert chat["session_id"]                  # session captured for --resume
    assert chat["spec_id"] is None             # nothing written yet
    assert chat["status"] == "active"


def test_spec_chat_reply_resumes_and_writes_spec(client, git_repo):
    proj = add_project(client, git_repo)
    chat = client.post("/api/spec-chats",
                       json={"project_id": proj["id"], "topic": "Add offline sync"}).json()
    chat = _wait_chat(client, chat["id"])
    session = chat["session_id"]

    r = client.post(f"/api/spec-chats/{chat['id']}/messages",
                    json={"text": "last-write-wins, ship it"})
    assert r.status_code == 200, r.text
    chat = _wait_chat(client, chat["id"])

    # the follow-up turn resumed the captured session
    last_user = [m for m in chat["messages"] if m["role"] == "user"][-1]
    turn_job = client.get(f"/api/jobs/{last_user['job_id']}").json()
    assert f"--resume {session}" in turn_job["command"]

    # and the agent wrote + linked a spec, now visible on the board
    assert chat["spec_id"] is not None
    linked = next(s for s in client.get(f"/api/projects/{proj['id']}/specs").json()
                  if s["id"] == chat["spec_id"])
    assert linked["status"] == "decided"


def test_spec_chat_reply_requires_active_chat(client, git_repo):
    proj = add_project(client, git_repo)
    chat = client.post("/api/spec-chats",
                       json={"project_id": proj["id"], "topic": "x"}).json()
    _wait_chat(client, chat["id"])
    client.post(f"/api/spec-chats/{chat['id']}/done")
    r = client.post(f"/api/spec-chats/{chat['id']}/messages", json={"text": "more"})
    assert r.status_code == 409


def test_parallel_spec_jobs_get_distinct_numbers(client, git_repo):
    """Fanning out several /spec jobs must not let them all pick the same next
    number and clobber each other on import."""
    proj = add_project(client, git_repo)
    before = {s["number"] for s in client.get(f"/api/projects/{proj['id']}/specs").json()}
    jobs = [client.post("/api/jobs", json={"project_id": proj["id"], "kind": "spec",
                                           "idea": f"idea {i}"}).json() for i in range(4)]
    # each job's prompt should carry a distinct reserved number
    reserved = [j["command"] for j in jobs]
    import re
    nums = [int(re.search(r"reserved spec number (\d+)", c).group(1)) for c in reserved]
    assert len(set(nums)) == 4, nums  # all distinct
    for j in jobs:
        wait_job(client, j["id"])
    after = {s["number"] for s in client.get(f"/api/projects/{proj['id']}/specs").json()}
    assert len(after - before) == 4  # four new specs actually landed


def test_spec_job_that_never_commits_fails_instead_of_phantom(client, git_repo, monkeypatch):
    """A /spec agent that writes the file but never commits it produces a spec
    the importer can see but /build (a fresh worktree off the default branch)
    cannot. The job must fail loudly rather than import an unbuildable phantom."""
    monkeypatch.setenv("FAKE_CLAUDE_NO_COMMIT", "1")
    proj = add_project(client, git_repo)
    before = {s["number"] for s in client.get(f"/api/projects/{proj['id']}/specs").json()}
    job = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "spec",
                                         "idea": "uncommitted idea"}).json()
    j = wait_job(client, job["id"])
    assert j["status"] == "failed"
    assert "never committed" in (j["error"] or "")
    after = {s["number"] for s in client.get(f"/api/projects/{proj['id']}/specs").json()}
    assert after == before  # no phantom spec landed


def test_parse_triage_candidates_takes_last_valid_block():
    from spindlegraph.api.routes import parse_triage_candidates
    text = (
        "here is a bad block:\n```json\n{not json}\n```\n"
        "an unrelated block:\n```json\n{\"other\": 1}\n```\n"
        "the real one:\n```json\n"
        '{"candidates": [{"title": "Do X", "size": "s", "grounding": "g",'
        ' "flag": "already_exists"}, {"title": "", "size": "M"}]}\n```\n')
    out = parse_triage_candidates(text)
    assert len(out) == 1  # the empty-title candidate is dropped
    assert out[0] == {"title": "Do X", "size": "S", "grounding": "g",
                      "flag": "already_exists"}
