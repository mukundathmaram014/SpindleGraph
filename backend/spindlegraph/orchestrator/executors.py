"""Executor -> subprocess argv. v0 ships exactly one backend, claude_code;
the interface exists so other backends (codex_cli, local_oss) plug in later
without touching the runner."""
from __future__ import annotations

import shlex
import shutil


def build_argv(executor: dict | None, prompt: str, cfg: dict,
               escalate: bool = False) -> list[str]:
    backend = (executor or {}).get("backend", "claude_code")
    if backend != "claude_code":
        raise ValueError(f"backend '{backend}' is not supported in v0")
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
    model = (executor or {}).get("model")
    if model:
        argv += ["--model", model]
    return argv


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
