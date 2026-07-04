"""Stand-in for a local coding agent CLI (aider-style): plain text output,
exit 0 on success. Used by the local_cli backend tests."""
import sys

print("local agent starting")
print(f"prompt was: {' '.join(sys.argv[1:])[:80]}")
print("PR: https://github.com/acme/demo/pull/777")
print("done")
