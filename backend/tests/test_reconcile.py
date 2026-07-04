"""Unit tests for the reconciliation core (docs/SPEC.md §10)."""
import json
import subprocess

from spindlegraph import graph, reconcile
from conftest import make_project


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _insert_spec(conn, pid, number, files_planned, status="draft", files_actual=None):
    from spindlegraph import db
    conn.execute(
        "INSERT INTO spec (project_id, number, slug, title, status, file_path,"
        " body_md, body_hash, files_planned_json, files_actual_json, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pid, number, f"s{number}", f"Spec {number}", status,
         f"specs/{number:04d}-s{number}.md", "body", "h",
         json.dumps([{"path": p} for p in files_planned]),
         json.dumps([{"path": p} for p in (files_actual or [])]), db.now()),
    )
    conn.commit()
    return conn.execute("SELECT id FROM spec WHERE project_id=? AND number=?",
                        (pid, number)).fetchone()[0]


def test_capture_actual_files(tmp_path):
    repo = tmp_path / "r"
    (repo / "specs").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "keep.py").write_text("x\n", encoding="utf-8")
    (repo / "specs" / "0001-x.md").write_text("# x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "checkout", "-q", "-b", "spec/0001-x")
    (repo / "new.py").write_text("n\n", encoding="utf-8")
    (repo / "keep.py").write_text("y\n", encoding="utf-8")
    (repo / "specs" / "0001-x.md").write_text("# x changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")

    files = reconcile.capture_actual_files(repo, "main", "spec/0001-x", "specs/0001-x.md")
    paths = {f["path"]: f["change"] for f in files}
    assert paths == {"new.py": "A", "keep.py": "M"}  # the spec file is excluded


def test_capture_actual_files_no_branch(tmp_path):
    assert reconcile.capture_actual_files(tmp_path, "main", None, "x.md") == []


def test_mark_stale_after_build(conn, repo):
    pid = make_project(conn, repo)
    a = _insert_spec(conn, pid, 1, ["a.py"], status="decided")
    b = _insert_spec(conn, pid, 2, ["shared.py"], status="decided")
    c = _insert_spec(conn, pid, 3, ["c.py"], status="draft")
    graph.recompute(conn, pid)
    assert conn.execute("SELECT COUNT(*) FROM edge WHERE project_id=?",
                        (pid,)).fetchone()[0] == 0

    # spec a lands, actually touching shared.py -> a now conflicts with b
    conn.execute("UPDATE spec SET status='built', files_actual_json=? WHERE id=?",
                 (json.dumps([{"path": "shared.py"}]), a))
    conn.commit()
    stale = reconcile.mark_stale_after_build(conn, pid, a)

    assert [s["id"] for s in stale] == [b]
    assert stale[0]["prior_status"] == "decided"
    assert conn.execute("SELECT status FROM spec WHERE id=?", (b,)).fetchone()[0] == "stale"
    assert conn.execute("SELECT status FROM spec WHERE id=?", (c,)).fetchone()[0] == "draft"
    # the built spec is never itself marked stale
    assert conn.execute("SELECT status FROM spec WHERE id=?", (a,)).fetchone()[0] == "built"


def test_mark_stale_noop_when_no_edge_change(conn, repo):
    pid = make_project(conn, repo)
    a = _insert_spec(conn, pid, 1, ["a.py"], status="decided")
    _insert_spec(conn, pid, 2, ["b.py"], status="decided")
    graph.recompute(conn, pid)
    # a lands touching only its own new file — no new overlaps
    conn.execute("UPDATE spec SET status='built', files_actual_json=? WHERE id=?",
                 (json.dumps([{"path": "a.py"}]), a))
    conn.commit()
    assert reconcile.mark_stale_after_build(conn, pid, a) == []


def test_build_prompt_mentions_changes_and_body():
    prompt = reconcile.build_prompt(
        9, "Fix login", [{"path": "src/x.py", "change": "M"}], "# body here")
    assert "#0009" in prompt
    assert "src/x.py" in prompt
    assert "# body here" in prompt
    assert reconcile.NO_CHANGES in prompt
