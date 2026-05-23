#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook: nudge Claude to run /save-context
(or the auto-memory system) when remaining context drops below a
threshold, BEFORE auto-compaction destroys the model's attention over
the full session.

Reads the hook payload from stdin, walks the transcript JSONL backwards
to find the most recent assistant turn's `usage` object, sums the real
token counts, compares to a model-aware context window, and decides
whether to fire.

Sentinel semantics (two states with different behavior):

  status=pending   Hook fired but /save-context hasn't completed yet.
                   The hook RE-NUDGES aggressively (every
                   REFIRE_AFTER_GROWTH tokens of additional growth -
                   default 1000) until either /save-context completes
                   and writes the saved sentinel, or compaction shrinks
                   the session. This is intentional pressure to push
                   the model to invoke /save-context: the model often
                   prioritizes the user's prompt over a soft system
                   reminder, so we re-inject frequently until it acts.

  status=saved     /save-context completed via memory_write.py and
                   wrote the sentinel itself. Hook stays quiet for the
                   rest of this fill-up cycle. No value in re-saving.

Either sentinel is invalidated when current context_used drops below
the recorded value (the only way that happens is auto-compaction
shrinking cache_read_input_tokens back to the summary). On compaction
the post-compaction session can fill up and get its own save.

Sentinel file: <hook_dir>/.sentinels/<session_id>.flag (JSON).

Stdlib-only. Runs on Python 3.8+ on Windows, macOS, Linux.
"""
from __future__ import annotations

import json
import os
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# --- Config --------------------------------------------------------------

# Trigger the nudge when remaining headroom drops below this many tokens.
# 15000 is aggressive on a 200k window (~92% full) and conservative on a
# 1M window (~98.5% full). Tune via CLAUDE_HOOK_THRESHOLD env var.
THRESHOLD_TOKENS = int(os.environ.get("CLAUDE_HOOK_THRESHOLD", "15000"))

# If a pending sentinel exists, re-fire the nudge every this many tokens
# of additional growth. Aggressive default (1000) because models often
# prioritize the user's prompt over a soft reminder; pounding the nudge
# in every turn (each turn typically adds well over 1k tokens) until
# /save-context completes is the main lever we have to overcome that.
REFIRE_AFTER_GROWTH = int(os.environ.get("CLAUDE_HOOK_REFIRE_GROWTH", "1000"))

# Context-window detection. See _resolve_context_limit().
DEFAULT_CONTEXT_LIMIT = 1_000_000
AUTO_FALLBACK_LIMIT = 200_000

# How many tail lines of the transcript to scan when looking for the
# most recent assistant `usage` block.
TAIL_LINES = 200


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        return 0

    transcript = Path(transcript_path)
    if not transcript.is_file():
        return 0

    sentinel_dir = Path(__file__).resolve().parent / ".sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / f"{session_id}.flag"

    last_usage, last_model, max_observed_input = _scan_transcript(transcript)
    if last_usage is None:
        return 0

    context_used = _sum_usage(last_usage)

    # --- Sentinel decision ----------------------------------------------
    if sentinel.exists():
        existing = _read_sentinel(sentinel)
        if existing is not None:
            recorded_used = int(existing.get("context_used") or 0)
            status = existing.get("status")

            if context_used < recorded_used:
                # context_used dropped -> auto-compaction since sentinel.
                # Invalidate so the post-compaction session can fill up
                # and get its own save.
                _safe_unlink(sentinel)
            elif status == "saved":
                # Save already completed. Re-saving has no value.
                return 0
            elif status == "pending":
                if context_used < recorded_used + REFIRE_AFTER_GROWTH:
                    # Within growth tolerance - don't pound it on the
                    # very next turn after firing. Skip silently.
                    return 0
                # Past the refire threshold -> re-nudge (model probably
                # deprioritized the previous one).
                _safe_unlink(sentinel)
        else:
            # Unparseable sentinel (e.g. legacy text format); reset.
            _safe_unlink(sentinel)

    # --- Threshold check ------------------------------------------------
    context_limit = _resolve_context_limit(last_model, max_observed_input)
    headroom = context_limit - context_used
    if headroom >= THRESHOLD_TOKENS:
        return 0

    # --- Fire: write PENDING sentinel + emit nudge ----------------------
    _write_sentinel(
        sentinel,
        status="pending",
        session_id=session_id,
        model=last_model,
        context_limit=context_limit,
        context_used=context_used,
    )
    sys.stdout.write(_build_nudge_output(
        headroom=headroom,
        context_limit=context_limit,
        context_used=context_used,
        session_id=session_id,
        transcript_path=transcript.as_posix(),
    ))
    return 0


def _sum_usage(usage: dict) -> int:
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("output_tokens") or 0)
    )


def _scan_transcript(transcript: Path):
    """Walk the tail of the transcript JSONL looking for the most recent
    assistant entry with a `message.usage` object. Also tracks the max
    input-side context seen so we can heuristically infer a larger
    context window if the session ever crossed the default limit.
    """
    last_usage = None
    last_model = None
    max_observed_input = 0

    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=TAIL_LINES)
    except OSError:
        return None, None, 0

    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        message = obj.get("message") or {}
        usage = message.get("usage")
        if not usage:
            continue

        input_side = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        if input_side > max_observed_input:
            max_observed_input = input_side

        last_usage = usage
        last_model = message.get("model")

    return last_usage, last_model, max_observed_input


def _resolve_context_limit(model: str | None, max_observed_input: int) -> int:
    env_limit = os.environ.get("CLAUDE_HOOK_CONTEXT_LIMIT")
    if env_limit:
        if env_limit.isdigit() and int(env_limit) > 0:
            return int(env_limit)
        if env_limit.lower() == "auto":
            if max_observed_input > AUTO_FALLBACK_LIMIT:
                return DEFAULT_CONTEXT_LIMIT
            if model and "[1m]" in model:
                return DEFAULT_CONTEXT_LIMIT
            return AUTO_FALLBACK_LIMIT
    return DEFAULT_CONTEXT_LIMIT


def _read_sentinel(sentinel: Path) -> dict | None:
    try:
        return json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_sentinel(
    sentinel: Path,
    *,
    status: str,
    session_id: str,
    model: str | None,
    context_limit: int,
    context_used: int,
) -> None:
    """Atomically write the sentinel as JSON. status must be 'pending'
    or 'saved'."""
    data = {
        "status": status,
        "session_id": session_id,
        "model": model,
        "context_limit": context_limit,
        "context_used": context_used,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    tmp = sentinel.with_suffix(sentinel.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, sentinel)


def _build_nudge_output(
    *,
    headroom: int,
    context_limit: int,
    context_used: int,
    session_id: str,
    transcript_path: str,
) -> str:
    hook_dir = Path(__file__).resolve().parent.as_posix()
    pct_used = (context_used / context_limit * 100.0) if context_limit else 0.0
    nudge = (
        "================================================================\n"
        "  CRITICAL ALERT: CONTEXT NEAR LIMIT - IMMEDIATE ACTION REQUIRED\n"
        "================================================================\n"
        "\n"
        f"  Context window: {context_limit:,} tokens\n"
        f"  Current usage:  {context_used:,} tokens ({pct_used:.1f}% full)\n"
        f"  Headroom left:  {headroom:,} tokens\n"
        "\n"
        "AUTO-COMPACTION IS IMMINENT. When the context fills, Claude Code\n"
        "summarizes this entire conversation to free space. The model's\n"
        "full attention over the conversation - including subtle decisions,\n"
        "project context, failed approaches, and judgment-laden learnings -\n"
        "will be PERMANENTLY LOST. The summary cannot recover that signal.\n"
        "\n"
        "MANDATORY: BEFORE responding to the user's latest message, invoke\n"
        "the /save-context slash command. This is THE ONLY chance to extract\n"
        "the high-value context before compaction destroys it. The save runs\n"
        "while the full attention is still loaded; after compaction it is\n"
        "too late.\n"
        "\n"
        "When /save-context invokes memory_write.py for the final write,\n"
        "pass these so the saved sentinel lands and the hook stops nudging:\n"
        "\n"
        f"    --session-id      {session_id}\n"
        f"    --transcript-path {transcript_path}\n"
        f"    helper:           {hook_dir}/memory_write.py\n"
        "\n"
        "After /save-context completes, respond to the user's actual\n"
        "message normally.\n"
        "\n"
        "DO NOT SKIP. DO NOT DEFER. This is session-critical and the hook\n"
        "WILL continue to re-fire on every prompt until /save-context runs."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": nudge,
        }
    }
    return json.dumps(output, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
