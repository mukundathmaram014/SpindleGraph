"""SQLite store — schema per docs/SPEC.md §5.

Plain stdlib ``sqlite3``; connections are cheap, so callers open one per
request/task via :func:`connect`. JSON columns are TEXT holding JSON.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  id            INTEGER PRIMARY KEY,
  slug          TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  repo_path     TEXT NOT NULL UNIQUE,
  notes_doc_path TEXT,
  default_branch TEXT NOT NULL DEFAULT 'main',
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executor (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  backend       TEXT NOT NULL DEFAULT 'claude_code',
  model         TEXT,
  prior_success REAL NOT NULL DEFAULT 0.8,
  prior_strength REAL NOT NULL DEFAULT 10,
  successes     INTEGER NOT NULL DEFAULT 0,
  failures      INTEGER NOT NULL DEFAULT 0,
  input_price_per_mtok  REAL,
  output_price_per_mtok REAL,
  avg_build_cost_usd REAL,
  command_template TEXT,
  enabled       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS spec (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  number        INTEGER NOT NULL,
  slug          TEXT NOT NULL,
  title         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'draft',
  file_path     TEXT NOT NULL,
  body_md       TEXT NOT NULL,
  body_hash     TEXT NOT NULL,
  files_planned_json TEXT NOT NULL DEFAULT '[]',
  files_actual_json  TEXT NOT NULL DEFAULT '[]',
  decisions_json     TEXT NOT NULL DEFAULT '[]',
  risk_json          TEXT NOT NULL DEFAULT '{}',
  depends_on_json    TEXT NOT NULL DEFAULT '[]',
  executor_id   INTEGER REFERENCES executor(id),
  provenance_json    TEXT NOT NULL DEFAULT '{}',
  updated_at    TEXT NOT NULL,
  UNIQUE (project_id, number)
);

CREATE TABLE IF NOT EXISTS edge (
  project_id    INTEGER NOT NULL REFERENCES project(id),
  spec_a        INTEGER NOT NULL REFERENCES spec(id),
  spec_b        INTEGER NOT NULL REFERENCES spec(id),
  shared_files_json TEXT NOT NULL,
  weight        REAL NOT NULL,
  overridden    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project_id, spec_a, spec_b)
);

CREATE TABLE IF NOT EXISTS job (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  kind          TEXT NOT NULL,
  spec_ids_json TEXT NOT NULL DEFAULT '[]',
  parent_job_id INTEGER REFERENCES job(id),
  status        TEXT NOT NULL,
  executor_id   INTEGER REFERENCES executor(id),
  outcome       TEXT,
  usage_json    TEXT NOT NULL DEFAULT '{}',
  cost_usd      REAL,
  command       TEXT NOT NULL,
  worktree_path TEXT,
  branch        TEXT,
  pr_url        TEXT,
  exit_code     INTEGER,
  error         TEXT,
  log_path      TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_chat (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id),
  spec_id       INTEGER REFERENCES spec(id),   -- linked once the agent writes the file
  session_id    TEXT,                          -- claude session for --resume continuity
  topic         TEXT NOT NULL,                 -- the seed (triage candidate / spec title)
  status        TEXT NOT NULL DEFAULT 'active',-- active | done
  executor_id   INTEGER REFERENCES executor(id),
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_chat_message (
  id            INTEGER PRIMARY KEY,
  chat_id       INTEGER NOT NULL REFERENCES spec_chat(id),
  role          TEXT NOT NULL,                 -- user | agent
  text          TEXT NOT NULL,
  job_id        INTEGER REFERENCES job(id),    -- the turn this message belongs to
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconcile_proposal (
  id              INTEGER PRIMARY KEY,
  project_id      INTEGER NOT NULL REFERENCES project(id),
  spec_id         INTEGER NOT NULL REFERENCES spec(id),
  trigger_spec_id INTEGER REFERENCES spec(id),  -- the built spec that caused staleness
  job_id          INTEGER REFERENCES job(id),
  prior_status    TEXT NOT NULL DEFAULT 'draft',-- status to restore on accept/reject
  proposed_body   TEXT NOT NULL DEFAULT '',     -- full revised spec markdown
  no_change       INTEGER NOT NULL DEFAULT 0,   -- agent said the spec is still accurate
  status          TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | rejected
  created_at      TEXT NOT NULL
);
"""

# Seeded once, editable in the GUI. Prices are USD per 1M tokens (2026-07
# published rates); model values are `claude --model` arguments.
SEED_EXECUTORS = [
    ("Claude (CLI default)", "claude_code", None, 0.85, 10, None, None),
    ("Opus 4.8", "claude_code", "claude-opus-4-8", 0.90, 10, 5.0, 25.0),
    ("Sonnet 5", "claude_code", "claude-sonnet-5", 0.85, 10, 3.0, 15.0),
    ("Haiku 4.5", "claude_code", "claude-haiku-4-5", 0.70, 10, 1.0, 5.0),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# additive migrations for DBs created before these columns existed
MIGRATIONS = [
    ("spec", "risk_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("executor", "command_template", "TEXT"),
]


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        if conn.execute("SELECT COUNT(*) FROM executor").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO executor (name, backend, model, prior_success,"
                " prior_strength, input_price_per_mtok, output_price_per_mtok)"
                " VALUES (?,?,?,?,?,?,?)",
                SEED_EXECUTORS,
            )
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row, json_fields: tuple[str, ...] = ()) -> dict:
    d = dict(row)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            key = f.removesuffix("_json")
            d[key] = json.loads(d[f])
            if key != f:
                del d[f]
    return d


SPEC_JSON = ("files_planned_json", "files_actual_json", "decisions_json",
             "risk_json", "depends_on_json", "provenance_json")
JOB_JSON = ("spec_ids_json", "usage_json")
