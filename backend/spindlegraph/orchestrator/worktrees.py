"""Git worktree lifecycle + bundled-command sync.

Worktrees live under the app state dir (never inside the target repo).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .. import config

BUNDLED_COMMANDS = Path(__file__).resolve().parents[3] / "commands"


# files SpindleGraph itself copies into every worktree (see ensure_commands)
GENERATED_RE = re.compile(r"^\.claude/commands/[^/]+\.md$")


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


def agent_changes(porcelain: str) -> list[str]:
    """Paths from ``git status --porcelain`` the agent actually touched.

    ``ensure_commands`` copies bundled commands into every worktree, so a
    worktree that has done nothing still reports `.claude/commands/*.md` as
    dirty. Counting those as progress pins the worktree to its original base
    forever: a rebuild resumes the stale branch instead of re-cutting from the
    (fixed) base, so the agent keeps reading the spec as it was.
    """
    paths = []
    for line in porcelain.splitlines():
        p = line[3:].strip()
        if " -> " in p:  # rename: "orig -> new"
            p = p.split(" -> ", 1)[1]
        p = p.strip('"')
        if p and not GENERATED_RE.match(p):
            paths.append(p)
    return paths


def create_worktree(repo: Path, project_slug: str, spec_key: str,
                    base_branch: str) -> tuple[Path, str]:
    """Create (or reuse) a worktree on branch spec/<spec_key>."""
    branch = f"spec/{spec_key}"
    path = worktree_path(project_slug, spec_key)
    if path.exists():
        # If a failed run left uncommitted edits behind, resume directly in the
        # same worktree instead of forcing manual cleanup. This preserves agent
        # progress across reruns (e.g. spend-limit interruptions).
        # -uall: without it git collapses an untracked dir to ".claude/", which
        # would hide whether the only changes are the commands we copied in
        code, out = run_git(["status", "--porcelain", "-uall"], path)
        if code == 0 and agent_changes(out):
            code_b, out_b = run_git(["symbolic-ref", "--short", "HEAD"], path)
            if code_b == 0 and out_b.strip() and out_b.strip() != branch:
                # best effort: switch to the spec branch before resuming
                run_git(["checkout", branch], path)
            return path, branch
        remove_worktree(repo, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_git(["worktree", "add", "-b", branch, str(path), base_branch], repo)
    if code != 0 and "already exists" in out:
        # Branch left over from a previous attempt. If it carries no commits of
        # its own it is just a stale pointer at an older base — re-point it at
        # the current base, otherwise the rebuild silently reads the spec (and
        # the rest of the tree) exactly as it was when that attempt started.
        # A branch WITH commits is real work: reuse it untouched.
        code_c, out_c = run_git(
            ["rev-list", "--count", f"{base_branch}..{branch}"], repo)
        if code_c == 0 and out_c.strip() == "0":
            run_git(["branch", "-f", branch, base_branch], repo)
        code, out = run_git(["worktree", "add", str(path), branch], repo)
    if code != 0:
        raise RuntimeError(f"git worktree add failed: {out}")
    return path, branch


def worktree_on_branch(repo: Path, project_slug: str, spec_key: str,
                       branch: str) -> Path:
    """Worktree checked out on an EXISTING branch (for feedback/revision of an
    already-built spec — the revision must land on the same branch so it rides
    the open PR)."""
    path = worktree_path(project_slug, spec_key)
    if path.exists():
        code, out = run_git(["status", "--porcelain"], path)
        if code == 0 and out.strip():
            run_git(["checkout", branch], path)
            return path
        remove_worktree(repo, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_git(["worktree", "add", str(path), branch], repo)
    if code != 0:
        raise RuntimeError(f"git worktree add on {branch} failed: {out}")
    return path


def branch_head(repo: Path, branch: str) -> str | None:
    code, out = run_git(["rev-parse", branch], repo)
    return out.strip() if code == 0 and out.strip() else None


def file_matches_branch(repo: Path, rel_path: str, branch: str) -> bool:
    """True if ``rel_path`` on disk is identical to what ``branch`` has committed.

    A build worktree is cut from ``branch``, so anything living only in the
    working tree is invisible to the agent running in it.
    """
    code, _ = run_git(["diff", "--quiet", branch, "--", rel_path], repo)
    if code != 0:
        return False
    # `git diff` is blind to untracked paths, so a never-committed spec looks
    # "clean" above — confirm the branch actually carries the file.
    code, _ = run_git(["cat-file", "-e", f"{branch}:{rel_path}"], repo)
    return code == 0


def commit_file_if_dirty(repo: Path, rel_path: str, message: str) -> tuple[bool, str]:
    """Stage + commit a single file if it differs from HEAD. Returns (committed, note)."""
    code, out = run_git(["status", "--porcelain", "--", rel_path], repo)
    if code != 0:
        return False, f"git status failed: {out}"
    if not out.strip():
        return False, "nothing to commit"
    code, out = run_git(["add", "--", rel_path], repo)
    if code != 0:
        return False, f"git add failed: {out}"
    code, out = run_git(["commit", "-m", message, "--", rel_path], repo)
    if code != 0:
        return False, f"git commit failed: {out}"
    return True, "committed"


def run_gh(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=90)
    except FileNotFoundError:
        return 127, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return 124, "gh timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def open_or_get_pr(repo: Path, branch: str, base: str) -> tuple[str | None, str]:
    """Push the branch if needed, then return the existing PR URL or create
    one via gh. Returns (pr_url, note). Runs on the (unsandboxed) host."""
    code_r, remote = run_git(["remote", "get-url", "origin"], repo)
    if code_r != 0 or not remote.strip():
        return None, "no 'origin' remote — push the branch and open a PR manually"
    # make sure the branch is on the remote
    push_code, push_out = run_git(["push", "-u", "origin", branch], repo)
    if push_code != 0 and "up-to-date" not in push_out.lower() \
            and "up to date" not in push_out.lower():
        return None, f"git push failed: {push_out[:200]}"
    # existing PR?
    code_v, out_v = run_gh(
        ["pr", "view", branch, "--json", "url", "-q", ".url"], repo)
    if code_v == 0 and out_v.strip().startswith("http"):
        return out_v.strip(), "PR already existed"
    # create one
    code_c, out_c = run_gh(
        ["pr", "create", "--head", branch, "--base", base, "--fill"], repo)
    m = re.search(r"https://github\.com/\S+/pull/\d+", out_c)
    if code_c == 0 and m:
        return m.group(0), "PR created"
    return None, f"gh pr create failed: {out_c[:200]}"


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
