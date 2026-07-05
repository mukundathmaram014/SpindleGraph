"""Executor backends (SPEC §7, v2).

- ``claude_code``  — the claude CLI as a subprocess, stream-json events.
- ``local_cli``    — any local coding agent invocable as a command line
  (aider, a codex wrapper, an ollama harness, …): the executor's
  ``command_template`` runs with ``{prompt}`` substituted; plain-text output
  streams as raw log events; exit 0 = success.
- ``claude_sdk``   — the Claude Agent SDK, in-process (see jobs._exec_sdk).
"""
from __future__ import annotations

import shlex
import shutil

BACKENDS = ("claude_code", "local_cli", "claude_sdk")


def build_argv(executor: dict | None, prompt: str, cfg: dict,
               escalate: bool = False) -> list[str]:
    backend = (executor or {}).get("backend", "claude_code")
    if backend == "local_cli":
        template = (executor or {}).get("command_template") or ""
        if "{prompt}" not in template:
            raise ValueError(
                "local_cli executor needs a command_template containing {prompt}")
        argv = [p.strip('"').replace("{prompt}", prompt)
                for p in shlex.split(template, posix=False)]
        resolved = shutil.which(argv[0])
        if resolved:
            argv[0] = resolved
        return argv
    if backend != "claude_code":
        raise ValueError(f"backend '{backend}' has no subprocess argv")
    # claude_bin may carry args (tests use "python fake_claude.py")
    bin_parts = shlex.split(str(cfg.get("claude_bin", "claude")), posix=False)
    bin_parts = [p.strip('"') for p in bin_parts]
    resolved = shutil.which(bin_parts[0])
    if resolved:
        bin_parts[0] = resolved
    argv = bin_parts + ["-p", prompt, "--output-format", "stream-json", "--verbose"]
    if escalate:
        argv.append("--dangerously-skip-permissions")
    else:
        argv += ["--permission-mode", "acceptEdits"]
        # acceptEdits only auto-approves file edits — Bash needs explicit
        # allow rules or every git/npm/test command dies headless (D2)
        rules = [r for r in (cfg.get("allowed_tools") or []) if r.strip()]
        if rules:
            argv += ["--allowedTools", ",".join(rules)]
    model = (executor or {}).get("model")
    if model:
        argv += ["--model", model]
    return argv


def describe_command(executor: dict | None, prompt: str, cfg: dict,
                     escalate: bool = False) -> str:
    """Audit string for the job row (claude_sdk has no argv)."""
    import subprocess
    if (executor or {}).get("backend") == "claude_sdk":
        return f"[claude-agent-sdk] {prompt}"
    return subprocess.list2cmdline(build_argv(executor, prompt, cfg, escalate))


def estimated_success(executor: dict) -> float:
    """Beta-mean estimate per SPEC §10."""
    n = executor["successes"] + executor["failures"]
    return (executor["prior_success"] * executor["prior_strength"]
            + executor["successes"]) / (executor["prior_strength"] + n)


def compute_cost(usage: dict, executor: dict | None) -> float | None:
    """Cost from accumulated usage and executor pricing; cache reads at 0.1x
    input price, cache writes at 1.25x."""
    if not executor or executor.get("input_price_per_mtok") is None:
        return None
    p_in = executor["input_price_per_mtok"]
    p_out = executor["output_price_per_mtok"] or 0.0
    return (
        usage.get("input_tokens", 0) * p_in
        + usage.get("output_tokens", 0) * p_out
        + usage.get("cache_read_input_tokens", 0) * 0.1 * p_in
        + usage.get("cache_creation_input_tokens", 0) * 1.25 * p_in
    ) / 1_000_000
