"""App state directory and global configuration.

Everything SpindleGraph persists lives under the state dir (default
``~/.spindlegraph``), never inside a target repo. ``SPINDLEGRAPH_HOME``
overrides the location (used by tests).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "claude_bin": "claude",
    "max_parallel": 3,
    "job_timeout_min": 30,
}


def state_dir() -> Path:
    d = Path(os.environ.get("SPINDLEGRAPH_HOME", str(Path.home() / ".spindlegraph")))
    for sub in ("", "worktrees", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return state_dir() / "spindlegraph.db"


def load_config() -> dict:
    p = state_dir() / "config.json"
    cfg = dict(DEFAULTS)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    (state_dir() / "config.json").write_text(
        json.dumps(merged, indent=2), encoding="utf-8"
    )
    return merged
