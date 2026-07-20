import json
from pathlib import Path

from spindlegraph import db, importer
from conftest import make_project

CANONICAL = """---
title: Add rate limiting to the public API
status: decided
---

# Add rate limiting to the public API

## Summary
Throttle abusive clients.

## Affected files
- `src/api/middleware.py` — add limiter middleware
- `src/config.py` — new RATE_LIMIT_* settings
- `tests/test_middleware.py` — new

## Decisions needed
- [x] Algorithm? → token bucket
- [ ] Counter store: redis or in-memory?

## Implementation notes
Keep it simple.
"""

MESSY = """# fix login redirect

some intro text without frontmatter

### Files
* src/auth_views.py: handle next param
* src/settings/*.py

Decision needed
- [ ] should we log redirects?
"""


def test_parse_canonical(repo):
    p = repo / "specs" / "0007-add-rate-limiting.md"
    p.write_text(CANONICAL, encoding="utf-8")
    rec = importer.parse_spec_file(p, repo)
    assert rec["number"] == 7
    assert rec["slug"] == "add-rate-limiting"
    assert rec["title"] == "Add rate limiting to the public API"
    assert rec["status"] == "decided"
    paths = {f["path"]: f for f in rec["files_planned"]}
    assert set(paths) == {"src/api/middleware.py", "src/config.py",
                          "tests/test_middleware.py"}
    assert paths["src/api/middleware.py"]["rationale"] == "add limiter middleware"
    assert paths["src/api/middleware.py"]["planned_new"] is False
    assert paths["tests/test_middleware.py"]["planned_new"] is True
    assert rec["decisions"] == [
        {"text": "Algorithm?", "resolved": True, "answer": "token bucket"},
        {"text": "Counter store: redis or in-memory?", "resolved": False, "answer": ""},
    ]


def test_parse_messy_tolerant(repo):
    p = repo / "specs" / "0009-fix-login-redirect.md"
    p.write_text(MESSY, encoding="utf-8")
    rec = importer.parse_spec_file(p, repo)
    assert rec["title"] == "fix login redirect"
    assert rec["status"] == "draft"
    paths = {f["path"] for f in rec["files_planned"]}
    # glob expanded against the repo tree
    assert paths == {"src/auth_views.py", "src/settings/loader.py",
                     "src/settings/schema.py"}
    globbed = [f for f in rec["files_planned"] if f["from_glob"]]
    assert all(f["from_glob"] == "src/settings/*.py" for f in globbed)
    # "Decision needed" without heading marker is not a heading -> no decisions
    assert rec["decisions"] == []


WRAPPED = """---
title: Feature with wrapped affected-file bullets
status: decided
---

# Feature with wrapped affected-file bullets

## Affected files
- `src/db.py` — add `repeat_days` column to `Model`: mirror the sibling,
  touching `__init__` and `serialize` (continuation line, indented).
- `src/app.py` — new startup migration
  (`ALTER TABLE ...`), called from `create_app`. **Prod schema change.**
- `tests/test_db.py` — new cases

## Implementation notes
Flush-left prose here must still end the section.
- `src/should_not_be_captured.py` — this bullet is under a prose paragraph
"""


def test_parse_wrapped_bullets_keeps_all_files(repo):
    """A bullet whose text wraps onto an indented second line must not end the
    Affected files section — every later bullet was being dropped, so the
    conflict graph saw ~1 file per spec and scheduled conflicting specs together."""
    p = repo / "specs" / "0031-wrapped-bullets.md"
    p.write_text(WRAPPED, encoding="utf-8")
    rec = importer.parse_spec_file(p, repo)
    paths = {f["path"] for f in rec["files_planned"]}
    assert paths == {"src/db.py", "src/app.py", "tests/test_db.py"}
    # code spans inside a continuation line (`__init__`, `serialize`) are not files
    assert "src/should_not_be_captured.py" not in paths  # after flush-left prose


def test_non_spec_filename_ignored(repo):
    p = repo / "specs" / "README.md"
    p.write_text("# not a spec", encoding="utf-8")
    assert importer.parse_spec_file(p, repo) is None


def test_import_project_sync_and_archive(conn, repo):
    (repo / "specs" / "0007-add-rate-limiting.md").write_text(CANONICAL, encoding="utf-8")
    (repo / "specs" / "0009-fix-login-redirect.md").write_text(MESSY, encoding="utf-8")
    pid = make_project(conn, repo)

    res = importer.import_project(conn, pid)
    assert res == {"imported": 2, "archived": 0}
    rows = conn.execute("SELECT * FROM spec WHERE project_id=? ORDER BY number",
                        (pid,)).fetchall()
    assert [r["number"] for r in rows] == [7, 9]

    # file wins on re-import; deleting a file archives the record
    (repo / "specs" / "0009-fix-login-redirect.md").unlink()
    res = importer.import_project(conn, pid)
    assert res["archived"] == 1
    row = conn.execute("SELECT status FROM spec WHERE project_id=? AND number=9",
                       (pid,)).fetchone()
    assert row["status"] == "archived"


def test_write_status_roundtrip(repo):
    p = repo / "specs" / "0007-add-rate-limiting.md"
    p.write_text(CANONICAL, encoding="utf-8")
    importer.write_status_to_file(p, "built")
    rec = importer.parse_spec_file(p, repo)
    assert rec["status"] == "built"
    assert rec["title"] == "Add rate limiting to the public API"

    q = repo / "specs" / "0009-fix-login-redirect.md"
    q.write_text(MESSY, encoding="utf-8")
    importer.write_status_to_file(q, "building")
    rec = importer.parse_spec_file(q, repo)
    assert rec["status"] == "building"
    assert rec["title"] == "fix login redirect"
