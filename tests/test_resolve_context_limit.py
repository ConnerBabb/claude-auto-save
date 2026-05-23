"""Smoke tests for _resolve_context_limit in check-context-headroom.py.

Run from the repo root:
    python tests/test_resolve_context_limit.py

Stdlib only. Exits non-zero on any failure.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hook", ROOT / "check-context-headroom.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def run_case(name: str, env_val: str | None, model: str | None,
             max_obs: int, expected: int) -> bool:
    if env_val is None:
        os.environ.pop("CLAUDE_HOOK_CONTEXT_LIMIT", None)
    else:
        os.environ["CLAUDE_HOOK_CONTEXT_LIMIT"] = env_val
    actual = hook._resolve_context_limit(model, max_obs)
    status = "PASS" if actual == expected else "FAIL"
    print(f"  [{status}] {name}")
    if actual != expected:
        print(f"         expected={expected}, got={actual}")
    return actual == expected


CASES = [
    # name,                                         env,         model,                  max_obs,                       expected
    ("env unset defaults to 1M",                     None,        "claude-opus-4-7",      50_000,                        hook.DEFAULT_CONTEXT_LIMIT),
    ("env=auto, no signal falls back to 200k",       "auto",      "claude-opus-4-7",      50_000,                        hook.AUTO_FALLBACK_LIMIT),
    ("env=auto, prior input > 200k upgrades to 1M",  "auto",      "claude-opus-4-7",      300_000,                       hook.DEFAULT_CONTEXT_LIMIT),
    ("env=auto, [1m] model flag upgrades to 1M",     "auto",      "claude-opus-4-7[1m]",  50_000,                        hook.DEFAULT_CONTEXT_LIMIT),
    ("env=integer string overrides",                 "500000",    "claude-opus-4-7",      50_000,                        500_000),
    ("env=unrecognized value falls through to 1M",   "nonsense",  "claude-opus-4-7",      50_000,                        hook.DEFAULT_CONTEXT_LIMIT),

    # The tricky one. `max_observed_input` is exactly AUTO_FALLBACK_LIMIT (200000).
    # A 200k window running at capacity will produce exactly 200000 tokens of
    # input on its largest turn - that is NOT evidence of a larger variant.
    # `_resolve_context_limit` must use strict `>` for the upgrade comparison.
    # A refactor that swaps it to `>=` would silently break this and we'd
    # start falsely upgrading 200k-window users to a 1M cap.
    ("env=auto, exact 200k boundary stays at 200k",  "auto",      "claude-opus-4-7",      hook.AUTO_FALLBACK_LIMIT,      hook.AUTO_FALLBACK_LIMIT),

    # Another tricky one. `"0"` passes `.isdigit()` so a naive parse accepts
    # it as a valid integer limit. But a 0-token window means headroom is
    # always negative, which means the hook fires on every single prompt
    # forever. `_resolve_context_limit` must reject zero (and negative ints,
    # which `.isdigit()` already excludes since "-1".isdigit() is False) and
    # fall through to the default. Catches a real footgun: a user typing
    # CLAUDE_HOOK_CONTEXT_LIMIT="0" (e.g. as a quick "disable" attempt)
    # would otherwise spam the hook constantly.
    ("env=0 rejected, falls through to default",     "0",         "claude-opus-4-7",      50_000,                        hook.DEFAULT_CONTEXT_LIMIT),
]


def main() -> int:
    print(f"Running {len(CASES)} _resolve_context_limit cases...")
    print()
    passes = sum(1 for c in CASES if run_case(*c))
    print()
    print(f"{passes}/{len(CASES)} passed")
    return 0 if passes == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
