import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "sg-home"
    monkeypatch.setenv("SPINDLEGRAPH_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def reset_singletons():
    """The JobManager is a module-global singleton; without this its in-memory
    state (and especially an asyncio.Semaphore bound to a *prior* test's event
    loop) leaks between tests and causes intermittent hangs/failures."""
    from spindlegraph.orchestrator.jobs import manager
    for attr in ("_tasks", "_procs", "_prompts", "_waves", "_results",
                 "_reconcile_meta"):
        getattr(manager, attr).clear()
    manager._sem = None
    try:
        from spindlegraph.api import routes
        routes._last_fingerprint.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def conn(state_home):
    from spindlegraph import db
    db.init_db()
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path):
    """A fixture target repo with a specs/ dir and a few source files."""
    root = tmp_path / "target-repo"
    (root / "specs").mkdir(parents=True)
    (root / "src" / "api").mkdir(parents=True)
    (root / "src" / "settings").mkdir(parents=True)
    for f in ("src/api/middleware.py", "src/config.py", "src/auth_views.py",
              "src/settings/loader.py", "src/settings/schema.py"):
        (root / f).write_text("# stub\n", encoding="utf-8")
    return root


def make_project(conn, repo) -> int:
    from spindlegraph import db
    conn.execute(
        "INSERT INTO project (slug, name, repo_path, created_at) VALUES (?,?,?,?)",
        (repo.name, repo.name, str(repo), db.now()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM project WHERE repo_path=?",
                        (str(repo),)).fetchone()[0]
