"""Git worktree lifecycle + bundled-command sync.

Worktrees live under the app state dir (never inside the target repo).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import config

BUNDLED_COMMANDS = Path(__file__).resolve().parents[3] / "commands"


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def detect_default_branch(repo: Path) -> str:
    code, out = run_git(["symbolic-ref", "--short", "HEAD"], repo)
    if code == 0 and out:
        return out.splitlines()[0].strip()
    return "main"


def worktree_path(project_slug: str, spec_key: str) -> Path:
    return config.state_dir() / "worktrees" / project_slug / spec_key


def create_worktree(repo: Path, project_slug: str, spec_key: str,
                    base_branch: str) -> tuple[Path, str]:
    """Create (or reuse) a worktree on branch spec/<spec_key>."""
    branch = f"spec/{spec_key}"
    path = worktree_path(project_slug, spec_key)
    if path.exists():
        # never destroy uncommitted agent work (e.g. a build that finished
        # coding but couldn't commit) — surface it instead
        code, out = run_git(["status", "--porcelain"], path)
        if code == 0 and out.strip():
            raise RuntimeError(
                f"worktree {path} has uncommitted changes from a previous "
                "build — commit or discard them there first (git status in "
                "that directory), then rebuild")
        remove_worktree(repo, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_git(["worktree", "add", "-b", branch, str(path), base_branch], repo)
    if code != 0 and "already exists" in out:
        # branch left over from a previous attempt — reuse it
        code, out = run_git(["worktree", "add", str(path), branch], repo)
    if code != 0:
        raise RuntimeError(f"git worktree add failed: {out}")
    return path, branch


def remove_worktree(repo: Path, path: Path) -> None:
    run_git(["worktree", "remove", "--force", str(path)], repo)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    run_git(["worktree", "prune"], repo)


def ensure_commands(target: Path, overwrite: bool = True) -> list[str]:
    """Copy bundled workflow commands into <target>/.claude/commands/.
    Returns the names written (existing identical files are skipped).

    Pass overwrite=False inside build worktrees: updating tracked files there
    dirties the agent's diff with changes it didn't make."""
    dest = target / ".claude" / "commands"
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    if not BUNDLED_COMMANDS.is_dir():
        return written
    for src in sorted(BUNDLED_COMMANDS.glob("*.md")):
        out = dest / src.name
        content = src.read_text(encoding="utf-8")
        if not out.exists():
            out.write_text(content, encoding="utf-8")
            written.append(src.name)
        elif overwrite and out.read_text(encoding="utf-8") != content:
            out.write_text(content, encoding="utf-8")
            written.append(src.name)
    return written
