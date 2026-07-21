"""A stand-in for the claude CLI used by tests (and demoable offline):
emits canned stream-json events so CI never needs credentials.

Behavior switches on the prompt (/build, /spec, other) and on
FAKE_CLAUDE_FAIL=1.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def emit(obj):
    print(json.dumps(obj), flush=True)


def commit_one_file(path, message, attempts=10):
    """Commit exactly `path`, the way a /spec agent must in a shared repo.

    Triage fans out several /spec jobs against one checkout, so commits race:
    a bare `git commit` sweeps in whatever a sibling just staged (leaving that
    sibling nothing to commit), and git's index.lock makes concurrent attempts
    fail outright. Scope the commit with a pathspec and retry through the lock.
    """
    last = ""
    for i in range(attempts):
        subprocess.run(["git", "add", "--", str(path)],
                       capture_output=True, text=True)
        p = subprocess.run(["git", "commit", "-q", "-m", message, "--", str(path)],
                           capture_output=True, text=True)
        if p.returncode == 0:
            return
        last = (p.stdout or "") + (p.stderr or "")
        time.sleep(0.1 * (i + 1))
    raise RuntimeError(f"could not commit {path}: {last}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", dest="prompt", default="")
    p.add_argument("--output-format")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--permission-mode")
    p.add_argument("--model")
    p.add_argument("--allowedTools")
    p.add_argument("--resume")
    p.add_argument("--dangerously-skip-permissions", action="store_true")
    args, _ = p.parse_known_args()
    prompt = args.prompt or ""

    session_id = args.resume or f"sess-{os.getpid()}"
    emit({"type": "system", "subtype": "init", "cwd": os.getcwd(),
          "model": args.model or "fake", "session_id": session_id})

    fail = os.environ.get("FAKE_CLAUDE_FAIL")
    if fail:
        result = ("You've hit your monthly spend limit · raise it at "
                  "claude.ai/settings/usage") if fail == "spend" else "fake failure"
        emit({"type": "result", "subtype": "error", "is_error": True,
              "result": result, "usage": {"input_tokens": 10, "output_tokens": 2}})
        sys.exit(1)

    if prompt.startswith("/build"):
        spec_rel = prompt.split(None, 1)[1].strip()
        slug = Path(spec_rel).stem
        if os.environ.get("FAKE_CLAUDE_BIGLINE"):
            # one huge single-line event, like a Read tool result embedding a
            # large file — regression for the 64KB asyncio readline limit
            emit({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "x" * 300_000}]}})
        Path(f"built_{slug}.txt").write_text("built\n", encoding="utf-8")
        # optional deviation from the plan: also modify a nominated file so the
        # actual diff differs from Affected files (exercises reconciliation).
        touch = os.environ.get("FAKE_CLAUDE_TOUCH")
        if touch:
            tp = Path(touch)
            tp.parent.mkdir(parents=True, exist_ok=True)
            with tp.open("a", encoding="utf-8") as fh:
                fh.write("# touched by fake build\n")
        if Path(spec_rel).exists() and not os.environ.get("FAKE_CLAUDE_NO_MOVE"):
            dest = Path("specs/implemented") / Path(spec_rel).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            Path(spec_rel).rename(dest)
        if os.environ.get("FAKE_CLAUDE_LOCK_DENIED"):
            sys.stderr.write(
                "fatal: Unable to create '/tmp/repo/.git/worktrees/spec/index.lock': "
                "Permission denied\n"
            )
            emit({"type": "result", "subtype": "error", "is_error": True,
                  "result": "commit blocked by index.lock permission denied",
                  "usage": {"input_tokens": 500, "output_tokens": 120}})
            sys.exit(1)
        if not os.environ.get("FAKE_CLAUDE_NO_COMMIT"):
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"{slug}: fake build"], check=True)
        emit({"type": "assistant", "message": {
            "content": [{"type": "text", "text": f"Implemented {slug}; checks pass."}]}})
        pr = 100 + sum(ord(c) for c in slug) % 900
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": f"Done. PR: https://github.com/acme/demo/pull/{pr}",
              "usage": {"input_tokens": 1200, "output_tokens": 300,
                        "cache_read_input_tokens": 5000,
                        "cache_creation_input_tokens": 800},
              "total_cost_usd": 1.23})
    elif prompt.startswith("/feedback"):
        # revise on the current branch: a new commit addressing feedback
        Path("feedback_fix.txt").write_text("addressed\n", encoding="utf-8")
        if os.environ.get("FAKE_CLAUDE_LOCK_DENIED"):
            # sandboxed agent leaves edits but can't write index.lock
            sys.stderr.write(
                "fatal: Unable to create '/repo/.git/worktrees/x/index.lock': "
                "Permission denied\n")
            emit({"type": "result", "subtype": "success", "is_error": False,
                  "result": "Made the fix but could not commit: index.lock permission "
                  "denied", "usage": {"input_tokens": 800, "output_tokens": 150},
                  "total_cost_usd": 0.4})
            return
        if not os.environ.get("FAKE_CLAUDE_NO_COMMIT"):
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-q", "-m",
                            "spec-xxxx: address review feedback"], check=True)
        emit({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Fixed the reported issue and pushed."}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "Done. PR: https://github.com/acme/demo/pull/321",
              "usage": {"input_tokens": 800, "output_tokens": 150},
              "total_cost_usd": 0.4})
    elif prompt.startswith("/triage"):
        # the notes doc path is passed inline; SpindleGraph grants read access
        # to its dir via --add-dir, so the agent reads it directly.
        rel = prompt.split(None, 1)[1].strip() if " " in prompt else ""
        try:
            text = Path(rel).read_text(encoding="utf-8", errors="replace")
            report = (
                f"Triaged {len(text)} chars of notes.\n\n"
                "- [size: M] Add dark mode — touches theme layer\n"
                "- [size: S] Faster export — csv path\n"
                "- [size: L] Rewrite sync [needs clarification: scope]\n\n"
                "Suggested next: Add dark mode.\n\n"
                "```json\n" + json.dumps({"candidates": [
                    {"title": "Add dark mode", "size": "M",
                     "grounding": "touches theme layer", "flag": None},
                    {"title": "Faster export", "size": "S",
                     "grounding": "csv path", "flag": None},
                    {"title": "Rewrite sync", "size": "L",
                     "grounding": "new surface", "flag": "needs_clarification"},
                ]}) + "\n```")
            emit({"type": "result", "is_error": False, "result": report,
                  "usage": {"input_tokens": 300, "output_tokens": 90}})
        except OSError as e:
            emit({"type": "result", "subtype": "error", "is_error": True,
                  "result": f"could not read notes: {e}",
                  "usage": {"input_tokens": 10, "output_tokens": 2}})
            sys.exit(1)
    elif prompt.startswith("/spec-chat") or args.resume:
        # A resumed turn carries only the user's raw reply (the real claude
        # remembers /spec-chat via the session); the fake keys off --resume.
        # first turn (no --resume): ask a clarifying question, write nothing.
        # a resumed turn: settle it and write the spec + SPEC_FILE marker.
        if not args.resume:
            emit({"type": "result", "is_error": False,
                  "result": "Grounded it in the code. One question: "
                  "last-write-wins or manual merge on conflict?",
                  "usage": {"input_tokens": 200, "output_tokens": 60},
                  "session_id": session_id})
        else:
            specs = Path("specs")
            specs.mkdir(exist_ok=True)
            nums = [int(f.name.split("-")[0]) for f in specs.glob("*.md")
                    if f.name.split("-")[0].isdigit()]
            n = max(nums, default=0) + 1
            rel = f"specs/{n:04d}-chatted-idea.md"
            Path(rel).write_text(
                "---\ntitle: Chatted idea\nstatus: decided\n---\n\n# Chatted idea\n\n"
                "## Summary\nDeveloped via chat.\n\n"
                "## Affected files\n- `src/config.py` — tweak\n\n"
                "## Decisions needed\n\n"
                "## Risk\n- **Involvement:** Minimal — one file\n"
                "- **Review attention:** Low — no behavior change\n",
                encoding="utf-8")
            emit({"type": "result", "is_error": False,
                  "result": f"Wrote the spec — take a look.\n\nSPEC_FILE: {rel}",
                  "usage": {"input_tokens": 300, "output_tokens": 120},
                  "session_id": session_id})
    elif prompt.startswith("/spec"):
        specs = Path("specs")
        specs.mkdir(exist_ok=True)
        # honor a SpindleGraph-reserved number if present, else pick next free
        m = re.search(r"reserved spec number (\d+)", prompt)
        if m:
            n = int(m.group(1))
        else:
            nums = [int(f.name.split("-")[0]) for f in specs.glob("*.md")
                    if f.name.split("-")[0].isdigit()]
            n = max(nums, default=0) + 1
        f = specs / f"{n:04d}-generated-idea.md"
        f.write_text(
            "---\ntitle: Generated idea\nstatus: draft\n---\n\n# Generated idea\n\n"
            "## Affected files\n- `src/config.py` — tweak\n\n"
            "## Decisions needed\n- [x] scope? → minimal\n\n"
            "## Risk\n"
            "- **Involvement:** Minimal — one config file\n"
            "- **Review attention:** Low — no behavior change\n",
            encoding="utf-8")
        # a compliant /spec agent COMMITS the file (see commands/spec.md) so the
        # build worktree, branched off the default branch, can see it.
        if not os.environ.get("FAKE_CLAUDE_NO_COMMIT"):
            commit_one_file(f, f"spec-{n:04d}: generated")
        emit({"type": "result", "is_error": False, "result": f"Wrote {f}",
              "usage": {"input_tokens": 100, "output_tokens": 50},
              "total_cost_usd": 0.05})
    elif "propose an updated version" in prompt.lower():
        # reconcile pass: echo the embedded spec back with a reconciled note so
        # the proposal is valid, changed markdown the test can detect.
        body = ""
        marker, end = "=== CURRENT SPEC FILE ===", "=== END SPEC FILE ==="
        if marker in prompt and end in prompt:
            body = prompt.split(marker, 1)[1].split(end, 1)[0].strip("\n")
        revised = body + "\n\n## Reconciled\n- adjusted to match the upstream build\n"
        emit({"type": "result", "is_error": False, "result": revised,
              "usage": {"input_tokens": 200, "output_tokens": 80},
              "total_cost_usd": 0.02})
    else:
        emit({"type": "result", "is_error": False, "result": "ok",
              "usage": {"input_tokens": 10, "output_tokens": 5}})


if __name__ == "__main__":
    main()
