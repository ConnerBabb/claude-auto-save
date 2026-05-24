#!/usr/bin/env python3
"""Safe memory writes for /save-context.

Two responsibilities, both done atomically under a lockfile on the
target memory directory:

  1. Write each memory entry from a JSON plan on stdin (temp file +
     atomic rename per file) and append index entries to MEMORY.md
     (dedup-aware).

  2. Optionally write the per-session "saved" sentinel that suppresses
     further hook nudges in the current session. Provide --session-id
     and --transcript-path; the helper computes context_used_at_save
     from the transcript and writes the sentinel via the same atomic
     pattern.

Together these prevent (a) races between concurrent Claude Code
sessions writing the same memory dir, and (b) the "hook fired but
/save-context never ran" footgun by making the completion record
authoritative.

Usage:
    python memory_write.py --memory-dir <dir> < plan.json
    python memory_write.py --memory-dir <dir> --session-id <id> \\
        --transcript-path <jsonl> < plan.json
    python memory_write.py --mark-saved-only --session-id <id> \\
        --transcript-path <jsonl>            # no plan, sentinel as 'saved'
    python memory_write.py --mark-in-progress --session-id <id> \\
        --transcript-path <jsonl>            # no plan, sentinel as 'in_progress'

Plan schema (stdin JSON):
    {
      "entries": [
        {"name": "user_foo", "body": "...full markdown w/ frontmatter..."}
      ],
      "index_entries": [
        "- [Title](user_foo.md) - one-line hook"
      ]
    }

Stdlib only, Python 3.8+. Runs on Windows, macOS, Linux.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

LOCK_FILE = ".write.lock"
STALE_LOCK_SECONDS = 120
ACQUIRE_TIMEOUT_SECONDS = 30
TAIL_LINES = 200  # how far back to scan in transcript for the latest usage


# --- Lock primitives -----------------------------------------------------

def acquire_lock(lock_path: Path) -> None:
    deadline = time.time() + ACQUIRE_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode("utf-8"))
            finally:
                os.close(fd)
            return
        except FileExistsError:
            try:
                info = json.loads(lock_path.read_text(encoding="utf-8"))
                age = time.time() - float(info.get("ts", 0))
                if age > STALE_LOCK_SECONDS:
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
            except (FileNotFoundError, ValueError, OSError):
                continue
            if time.time() > deadline:
                raise TimeoutError(
                    f"Could not acquire {lock_path} within {ACQUIRE_TIMEOUT_SECONDS}s "
                    f"(another /save-context likely running in a parallel session)."
                )
            time.sleep(0.2)


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def atomic_write_text(target: Path, content: str) -> None:
    """Write content to target atomically via .tmp + os.replace."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


# --- Memory + index writes ----------------------------------------------

def write_entries(memory_dir: Path, entries: list[dict]) -> int:
    written = 0
    for entry in entries:
        name = entry.get("name")
        body = entry.get("body")
        if not name or body is None:
            print(f"warn: skipping malformed entry: {entry!r}", file=sys.stderr)
            continue
        filename = name if name.endswith(".md") else f"{name}.md"
        atomic_write_text(memory_dir / filename, body)
        written += 1
    return written


def update_index(index_path: Path, new_lines: list[str]) -> int:
    if not new_lines:
        return 0
    existing_lines: list[str] = []
    if index_path.exists():
        existing_lines = index_path.read_text(encoding="utf-8").splitlines()
    existing_trimmed = {ln.strip() for ln in existing_lines if ln.strip()}
    added = 0
    for raw in new_lines:
        trimmed = raw.strip()
        if not trimmed or trimmed in existing_trimmed:
            continue
        existing_lines.append(raw)
        existing_trimmed.add(trimmed)
        added += 1
    if added == 0:
        return 0
    body = "\n".join(existing_lines)
    if not body.endswith("\n"):
        body += "\n"
    atomic_write_text(index_path, body)
    return added


# --- Saved-sentinel write ------------------------------------------------

def compute_context_used(transcript: Path) -> int | None:
    """Walk the tail of the transcript looking for the most recent
    assistant turn's `message.usage`. Returns total tokens or None."""
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=TAIL_LINES)
    except OSError:
        return None
    last = None
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
        usage = (obj.get("message") or {}).get("usage")
        if usage:
            last = usage
    if last is None:
        return None
    return (
        int(last.get("input_tokens") or 0)
        + int(last.get("cache_read_input_tokens") or 0)
        + int(last.get("cache_creation_input_tokens") or 0)
        + int(last.get("output_tokens") or 0)
    )


def write_sentinel(
    sentinel_dir: Path,
    session_id: str,
    transcript_path: Path,
    status: str,
    model: str | None = None,
) -> int | None:
    """Compute context_used from the transcript and write the sentinel
    with the given status ('saved' or 'in_progress'). Returns the
    recorded context_used, or None if computation failed."""
    if status not in ("saved", "in_progress"):
        raise ValueError(f"invalid sentinel status: {status!r}")
    context_used = compute_context_used(transcript_path)
    if context_used is None:
        return None
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / f"{session_id}.flag"
    data = {
        "status": status,
        "session_id": session_id,
        "model": model,
        "context_used": context_used,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(sentinel, json.dumps(data, indent=2))
    return context_used


# Back-compat alias for callers that still expect the old name.
def write_saved_sentinel(sentinel_dir, session_id, transcript_path, model=None):
    return write_sentinel(sentinel_dir, session_id, transcript_path, "saved", model)


# --- CLI -----------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Safe memory writes + completion sentinel for /save-context.")
    p.add_argument("--memory-dir", help="Memory directory to write into (required unless --mark-saved-only / --mark-in-progress).")
    p.add_argument("--session-id", help="Session ID. If provided with --transcript-path, also writes the saved sentinel after memory writes.")
    p.add_argument("--transcript-path", help="Transcript JSONL path (used to compute context_used).")
    p.add_argument("--sentinel-dir", help="Directory for sentinel files. Defaults to <script_dir>/.sentinels.")
    p.add_argument("--mark-saved-only", action="store_true",
                   help="Skip memory writes; mark this session saved (requires --session-id + --transcript-path).")
    p.add_argument("--mark-in-progress", action="store_true",
                   help="Skip memory writes; mark this session as in-progress so the hook stops nudging while /save-context runs (requires --session-id + --transcript-path).")
    args = p.parse_args()

    sentinel_dir = (Path(args.sentinel_dir).expanduser().resolve()
                    if args.sentinel_dir
                    else Path(__file__).resolve().parent / ".sentinels")

    # Sentinel-only modes: skip plan + memory writes entirely.
    if args.mark_saved_only or args.mark_in_progress:
        if args.mark_saved_only and args.mark_in_progress:
            print("error: --mark-saved-only and --mark-in-progress are mutually exclusive", file=sys.stderr)
            return 2
        if not (args.session_id and args.transcript_path):
            print("error: sentinel-only mode requires --session-id and --transcript-path", file=sys.stderr)
            return 2
        status = "saved" if args.mark_saved_only else "in_progress"
        result = write_sentinel(
            sentinel_dir, args.session_id,
            Path(args.transcript_path).expanduser().resolve(),
            status,
        )
        if result is None:
            print(f"warn: could not compute context_used from transcript {args.transcript_path}", file=sys.stderr)
            return 1
        print(f"marked session {args.session_id} as {status} (context_used={result})")
        return 0

    # Full mode: memory writes + optional sentinel.
    if not args.memory_dir:
        print("error: --memory-dir is required (unless --mark-saved-only)", file=sys.stderr)
        return 2

    memory_dir = Path(args.memory_dir).expanduser().resolve()
    memory_dir.mkdir(parents=True, exist_ok=True)

    try:
        plan = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"error: invalid plan JSON on stdin: {e}", file=sys.stderr)
        return 2

    entries = plan.get("entries") or []
    index_entries = plan.get("index_entries") or []
    wants_sentinel = bool(args.session_id and args.transcript_path)

    if not entries and not index_entries and not wants_sentinel:
        print("nothing to write (empty plan, no sentinel requested)")
        return 0

    lock = memory_dir / LOCK_FILE
    acquire_lock(lock)
    try:
        written = write_entries(memory_dir, entries)
        added = update_index(memory_dir / "MEMORY.md", index_entries)

        sentinel_recorded = None
        if wants_sentinel:
            sentinel_recorded = write_sentinel(
                sentinel_dir, args.session_id,
                Path(args.transcript_path).expanduser().resolve(),
                "saved",
            )
    finally:
        release_lock(lock)

    msg = f"wrote {written} memory file(s); added {added} new index line(s)"
    if wants_sentinel:
        if sentinel_recorded is None:
            msg += "; sentinel skipped (could not compute context_used)"
        else:
            msg += f"; marked session {args.session_id} saved (context_used={sentinel_recorded})"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
