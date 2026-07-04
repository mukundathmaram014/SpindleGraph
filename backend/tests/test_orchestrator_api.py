"""Integration tests: API + orchestrator against a real git fixture repo,
with fake_claude.py standing in for the claude CLI."""
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FAKE = Path(__file__).resolve().parent / "fake_claude.py"
TERMINAL = {"succeeded", "failed", "canceled", "skipped"}

SPEC_7 = """---
title: Add rate limiting
status: draft
---

# Add rate limiting

## Affected files
- `src/api/middleware.py` — limiter
- `src/config.py` — settings

## Decisions needed
- [ ] Counter store: redis or in-memory?
"""

SPEC_9 = """---
title: Fix login redirect
status: decided
---

# Fix login redirect

## Affected files
- `src/auth_views.py`
"""

SPEC_14 = """---
title: Dark mode
status: decided
---

# Dark mode

## Affected files
- `src/settings/loader.py`
"""


@pytest.fixture
def git_repo(repo):
    def run(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("config", "commit.gpgsign", "false")
    (repo / "specs" / "0007-add-rate-limiting.md").write_text(SPEC_7, encoding="utf-8")
    (repo / "specs" / "0009-fix-login-redirect.md").write_text(SPEC_9, encoding="utf-8")
    (repo / "specs" / "0014-dark-mode.md").write_text(SPEC_14, encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def client(state_home):
    from spindlegraph import config as cfg
    cfg.save_config({"claude_bin": f"{sys.executable} {FAKE}",
                     "max_parallel": 2, "job_timeout_min": 2})
    from spindlegraph.main import app
    with TestClient(app) as c:
        yield c


def add_project(client, git_repo) -> dict:
    r = client.post("/api/projects", json={"repo_path": str(git_repo)})
    assert r.status_code == 200, r.text
    return r.json()


def wait_job(client, job_id, timeout=60) -> dict:
    deadline = time.time() + timeout
    j = {}
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in TERMINAL:
            return j
        time.sleep(0.15)
    raise TimeoutError(f"job {job_id} stuck: {j.get('status')}")


def spec_by_number(client, pid, number) -> dict:
    specs = client.get(f"/api/projects/{pid}/specs").json()
    return next(s for s in specs if s["number"] == number)


def test_project_import_and_graph(client, git_repo):
    proj = add_project(client, git_repo)
    g = client.get(f"/api/projects/{proj['id']}/graph").json()
    assert {n["number"] for n in g["nodes"]} == {7, 9, 14}
    assert g["edges"] == []  # no overlap among these three
    check = client.post(f"/api/projects/{proj['id']}/graph/check",
                        json={"spec_ids": [n["id"] for n in g["nodes"]]}).json()
    assert check["safe"] is True
    assert len(check["waves"]) == 1


def test_build_blocked_on_unresolved_decisions(client, git_repo):
    proj = add_project(client, git_repo)
    s7 = spec_by_number(client, proj["id"], 7)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s7["id"]]})
    assert r.status_code == 409


def test_build_job_end_to_end(client, git_repo):
    proj = add_project(client, git_repo)
    s7 = spec_by_number(client, proj["id"], 7)
    # resolve the decision by editing the body (as the drawer does)
    body = s7["body_md"].replace("- [ ] Counter store: redis or in-memory?",
                                 "- [x] Counter store: redis or in-memory? → in-memory")
    client.patch(f"/api/specs/{s7['id']}", json={"body_md": body})

    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s7["id"]]})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["pr_url"] and "/pull/" in job["pr_url"]
    assert job["cost_usd"] == 1.23
    assert job["usage"]["output_tokens"] == 300

    s7 = spec_by_number(client, proj["id"], 7)
    assert s7["status"] == "built"
    assert s7["provenance"]["pr_url"] == job["pr_url"]

    out = subprocess.run(["git", "branch", "--list", "spec/0007-add-rate-limiting"],
                         cwd=git_repo, capture_output=True, text=True)
    assert "spec/0007-add-rate-limiting" in out.stdout
    assert not Path(job["worktree_path"]).exists()  # cleaned up on success

    log = client.get(f"/api/jobs/{job['id']}").json()["log_events"]
    assert any(e.get("type") == "result" for e in log)


def test_build_failure_keeps_worktree(client, git_repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "1")
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]]})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "failed"
    assert Path(job["worktree_path"]).exists()  # kept for inspection
    s9 = spec_by_number(client, proj["id"], 9)
    assert s9["status"] == "decided"  # rolled back


def test_spec_job_creates_and_imports(client, git_repo):
    proj = add_project(client, git_repo)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "spec",
                                       "idea": "add caching layer"})
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded"
    specs = client.get(f"/api/projects/{proj['id']}/specs").json()
    assert any(s["slug"] == "generated-idea" for s in specs)


def test_build_batch_two_waves(client, git_repo):
    proj = add_project(client, git_repo)
    s9 = spec_by_number(client, proj["id"], 9)
    s14 = spec_by_number(client, proj["id"], 14)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build_batch",
                                       "spec_ids": [s9["id"], s14["id"]]})
    batch = wait_job(client, r.json()["id"], timeout=90)
    assert batch["status"] == "succeeded", batch
    jobs = client.get(f"/api/jobs?project_id={proj['id']}").json()
    children = [j for j in jobs if j.get("parent_job_id") == batch["id"]]
    assert len(children) == 2
    assert all(j["status"] == "succeeded" for j in children)
    for n in (9, 14):
        assert spec_by_number(client, proj["id"], n)["status"] == "built"


def test_executor_calibration_updates(client, git_repo):
    proj = add_project(client, git_repo)
    execs = client.get("/api/executors").json()
    sonnet = next(e for e in execs if "Sonnet" in e["name"])
    before = sonnet["successes"]
    s9 = spec_by_number(client, proj["id"], 9)
    r = client.post("/api/jobs", json={"project_id": proj["id"], "kind": "build",
                                       "spec_ids": [s9["id"]],
                                       "executor_id": sonnet["id"]})
    wait_job(client, r.json()["id"])
    after = next(e for e in client.get("/api/executors").json()
                 if e["id"] == sonnet["id"])
    assert after["successes"] == before + 1
    assert after["avg_build_cost_usd"] == pytest.approx(1.23)
