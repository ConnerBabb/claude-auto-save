#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook: nudge Claude to run the auto-memory
system (or invoke /save-context) when remaining context drops below a
threshold, BEFORE auto-compaction fires and destroys the model's
attention over the full session.

Reads the hook payload from stdin, walks the transcript JSONL backwards
to find the most recent assistant turn's `usage` object, sums the real
token counts (input + cache_read + cache_creation + output), and
compares to a model-aware context window. If headroom < THRESHOLD_TOKENS
and no per-session sentinel exists, emits a
hookSpecificOutput.additionalContext nudge and creates the sentinel so
it only fires once per session.

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

# Context-window detection:
#   1. If CLAUDE_HOOK_CONTEXT_LIMIT is set, use it (most reliable).
#   2. Otherwise, if any recorded usage in the transcript has exceeded
#      DEFAULT_CONTEXT_LIMIT, the session must be on a larger variant.
#   3. Otherwise default to DEFAULT_CONTEXT_LIMIT.
# The API response's `message.model` field comes back as plain
# `claude-opus-4-7` even when the [1m] beta header is in use, so the
# model string alone is not a reliable signal.
DEFAULT_CONTEXT_LIMIT = 200_000
ONE_MILLION_CONTEXT_LIMIT = 1_000_000

# How many tail lines of the transcript to scan when looking for the
# most recent assistant `usage` block.
TAIL_LINES = 200


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        return 0

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        return 0

    transcript = Path(transcript_path)
    if not transcript.is_file():
        return 0

    # Sentinel lives next to this script so the package directory is
    # self-contained. .sentinels/ is gitignored in the repo.
    sentinel_dir = Path(__file__).resolve().parent / ".sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / f"{session_id}.flag"
    if sentinel.exists():
        return 0

    last_usage, last_model, max_observed_input = _scan_transcript(transcript)
    if last_usage is None:
        return 0

    context_used = (
        int(last_usage.get("input_tokens") or 0)
        + int(last_usage.get("cache_read_input_tokens") or 0)
        + int(last_usage.get("cache_creation_input_tokens") or 0)
        + int(last_usage.get("output_tokens") or 0)
    )

    context_limit = _resolve_context_limit(last_model, max_observed_input)
    headroom = context_limit - context_used
    if headroom >= THRESHOLD_TOKENS:
        return 0

    _write_sentinel(sentinel, session_id, last_model, context_limit, context_used, headroom)
    sys.stdout.write(_build_nudge_output(headroom, context_limit, context_used))
    return 0


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

        # Overwrite as we move forward through the tail — the final
        # assignment is the most recent assistant turn in the window.
        last_usage = usage
        last_model = message.get("model")

    return last_usage, last_model, max_observed_input


def _resolve_context_limit(model: str | None, max_observed_input: int) -> int:
    env_limit = os.environ.get("CLAUDE_HOOK_CONTEXT_LIMIT")
    if env_limit and env_limit.isdigit():
        return int(env_limit)
    if max_observed_input > DEFAULT_CONTEXT_LIMIT:
        return ONE_MILLION_CONTEXT_LIMIT
    if model and "[1m]" in model:
        return ONE_MILLION_CONTEXT_LIMIT
    return DEFAULT_CONTEXT_LIMIT


def _write_sentinel(
    sentinel: Path,
    session_id: str,
    model: str | None,
    context_limit: int,
    context_used: int,
    headroom: int,
) -> None:
    sentinel.write_text(
        f"session_id: {session_id}\n"
        f"model: {model}\n"
        f"context_limit: {context_limit}\n"
        f"context_used: {context_used}\n"
        f"headroom: {headroom}\n"
        f"fired_at: {datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )


def _build_nudge_output(headroom: int, context_limit: int, context_used: int) -> str:
    nudge = (
        f"CONTEXT NEAR LIMIT: roughly {headroom} tokens of headroom remain "
        f"in a {context_limit}-token window (used: {context_used}). "
        "Auto-compaction will fire shortly and will destroy the model's "
        "attention over the full conversation.\n\n"
        "BEFORE responding to the user's latest message, invoke the "
        "/save-context slash command (or the equivalent auto memory "
        "system described in your system prompt if /save-context is not "
        "available in this session). Use the current full attention to "
        "extract non-obvious learnings, decisions, feedback, project "
        "context, and references that would otherwise be lost when "
        "compaction reduces the session to a summary. Write to whichever "
        "memory directory your system prompt specifies for this CWD.\n\n"
        "Then respond to the user's actual message normally.\n\n"
        "This is your only chance to extract from the full attention "
        "state before compaction collapses it. Skip rote summarization - "
        "capture what is surprising, non-obvious, or constraint-driven."
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
