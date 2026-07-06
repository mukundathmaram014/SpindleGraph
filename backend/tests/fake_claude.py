"""A stand-in for the claude CLI used by tests (and demoable offline):
emits canned stream-json events so CI never needs credentials.

Behavior switches on the prompt (/build, /spec, other) and on
FAKE_CLAUDE_FAIL=1.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def emit(obj):
    print(json.dumps(obj), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", dest="prompt", default="")
    p.add_argument("--output-format")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--permission-mode")
    p.add_argument("--model")
    p.add_argument("--allowedTools")
    p.add_argument("--dangerously-skip-permissions", action="store_true")
    args, _ = p.parse_known_args()
    prompt = args.prompt or ""

    emit({"type": "system", "subtype": "init", "cwd": os.getcwd(),
          "model": args.model or "fake"})

    if os.environ.get("FAKE_CLAUDE_FAIL"):
        emit({"type": "result", "subtype": "error", "is_error": True,
              "result": "fake failure", "usage": {"input_tokens": 10, "output_tokens": 2}})
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
        if not os.environ.get("FAKE_CLAUDE_NO_COMMIT"):
            Path("feedback_fix.txt").write_text("addressed\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-q", "-m",
                            "spec-xxxx: address review feedback"], check=True)
        emit({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Fixed the reported issue and pushed."}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "Done. PR: https://github.com/acme/demo/pull/321",
              "usage": {"input_tokens": 800, "output_tokens": 150},
              "total_cost_usd": 0.4})
    elif prompt.startswith("/spec"):
        specs = Path("specs")
        specs.mkdir(exist_ok=True)
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
