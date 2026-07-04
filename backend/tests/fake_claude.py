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
        Path(f"built_{slug}.txt").write_text("built\n", encoding="utf-8")
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
            "## Decisions needed\n- [x] scope? → minimal\n",
            encoding="utf-8")
        emit({"type": "result", "is_error": False, "result": f"Wrote {f}",
              "usage": {"input_tokens": 100, "output_tokens": 50},
              "total_cost_usd": 0.05})
    else:
        emit({"type": "result", "is_error": False, "result": "ok",
              "usage": {"input_tokens": 10, "output_tokens": 5}})


if __name__ == "__main__":
    main()
