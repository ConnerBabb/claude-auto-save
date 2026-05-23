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

Sentinel semantics (two states):

  status=pending   Hook fired but /save-context hasn't completed yet.
                   Subsequent fires suppressed UNTIL the context grows
                   REFIRE_AFTER_GROWTH tokens past the recorded value
                   (in which case the save was probably ignored and we
                   re-fire) OR until the context drops below the
                   recorded value (compaction happened — re-fire fresh).

  status=saved     /save-context completed and wrote the sentinel via
                   memory_write.py --mark-saved. Hook stays quiet for
                   this session UNLESS the context drops below the
                   recorded value (compaction — sentinel becomes
                   invalid because the post-compaction session has
                   fresh attention to fill back up).

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

# If a pending sentinel exists and context has grown by this much past
# the recorded fire point without a completion sentinel landing, assume
# the previous nudge was ignored and re-fire.
REFIRE_AFTER_GROWTH = int(os.environ.get("CLAUDE_HOOK_REFIRE_GROWTH", "50000"))

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
                # Context shrunk -> compaction since sentinel.
                # Invalidate and fall through to fresh-fire logic.
                _safe_unlink(sentinel)
            elif status == "saved":
                # Save already completed at/before this point. Nothing to do.
                return 0
            elif status == "pending":
                if context_used <= recorded_used + REFIRE_AFTER_GROWTH:
                    # Save in progress (or just nudged). Give it room.
                    return 0
                # Grew significantly past the fire point without completion;
                # the model probably didn't act on the previous nudge.
                # Fall through and re-fire.
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
        transcript_path=str(transcript),
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
    nudge = (
        f"CONTEXT NEAR LIMIT: roughly {headroom} tokens of headroom remain "
        f"in a {context_limit}-token window (used: {context_used}). "
        "Auto-compaction will fire if context fills up, which will "
        "destroy the model's attention over the full conversation.\n\n"
        "BEFORE responding to the user's latest message, invoke the "
        "/save-context slash command (or the equivalent auto memory "
        "system described in your system prompt if /save-context is not "
        "available in this session). Use the current full attention to "
        "extract non-obvious learnings, decisions, feedback, project "
        "context, and references that would otherwise be lost when "
        "compaction reduces the session to a summary.\n\n"
        "When /save-context performs the memory writes, pass these "
        "values to the bundled memory_write.py helper so it can mark "
        "the session as saved (suppresses further nudges this session):\n"
        f"    --session-id {session_id}\n"
        f"    --transcript-path {transcript_path}\n"
        f"    helper path: {hook_dir}/memory_write.py\n\n"
        "Then respond to the user's actual message normally.\n\n"
        "Skip rote summarization - capture what is surprising, "
        "non-obvious, or constraint-driven."
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
