"""Stand-in for a local coding agent CLI (aider-style): plain text output,
commits its work, exit 0 on success. Used by the local_cli backend tests."""
import subprocess
import sys
from pathlib import Path

print("local agent starting")
print(f"prompt was: {' '.join(sys.argv[1:])[:80]}")
Path("local_agent_output.txt").write_text("done\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "local agent: fake build"], check=True)
print("PR: https://github.com/acme/demo/pull/777")
print("done")
