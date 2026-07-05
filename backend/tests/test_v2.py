"""v2: risk parsing + ordering, executor backends, project delete."""
import json
import sys
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
