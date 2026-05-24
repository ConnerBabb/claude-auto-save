"""Integration tests for the sentinel state machine in
check-context-headroom.py.

Drives the hook as a subprocess with synthetic transcripts and
controlled sentinel states. Exercises the four decision branches:

  - no sentinel + high context (low headroom)         -> fires, pending
  - pending + small growth                            -> skip
  - pending + growth past REFIRE_AFTER_GROWTH         -> resets, re-fires
  - pending + context shrunk (compaction simulation)  -> resets, re-fires
  - saved + similar or growing context                -> skip
  - saved + context shrunk (compaction simulation)    -> resets, re-fires

Stdlib only. Run from repo root:
    python tests/test_sentinel_state_machine.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = ROOT / "check-context-headroom.py"


def make_transcript(path: Path, input_tokens: int) -> None:
    """Synthesize a transcript with one assistant turn whose usage
    totals roughly `input_tokens` of input + a fixed 100 output."""
    entry = {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-7",
            "usage": {
                "input_tokens": 0,
                "cache_read_input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "output_tokens": 100,
            },
        },
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def run_hook(
    session_id: str,
    transcript_path: Path,
    sentinel_dir: Path,
    context_limit: str = "1000000",
    threshold: str = "15000",
    refire_growth: str = "50000",
) -> tuple[int, str, str]:
    """Invoke the hook script with the given payload + env. The hook
    always writes its sentinel next to check-context-headroom.py
    (./sentinels), so we point its working dir + script location at
    a temp copy."""
    payload = json.dumps({
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "hook_event_name": "UserPromptSubmit",
    })
    env = os.environ.copy()
    env["CLAUDE_HOOK_CONTEXT_LIMIT"] = context_limit
    env["CLAUDE_HOOK_THRESHOLD"] = threshold
    env["CLAUDE_HOOK_REFIRE_GROWTH"] = refire_growth
    proc = subprocess.run(
        [sys.executable, str(sentinel_dir.parent / HOOK_SCRIPT.name)],
        input=payload,
        capture_output=True, text=True, env=env, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def setup_test_hook_dir(tmpdir: Path) -> Path:
    """Copy the hook script to a temp dir so its sentinels go there."""
    hook_copy = tmpdir / HOOK_SCRIPT.name
    hook_copy.write_bytes(HOOK_SCRIPT.read_bytes())
    return tmpdir


def write_sentinel(sentinel_dir: Path, session_id: str, status: str, context_used: int) -> Path:
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    path = sentinel_dir / f"{session_id}.flag"
    path.write_text(json.dumps({
        "status": status,
        "session_id": session_id,
        "context_used": context_used,
    }, indent=2), encoding="utf-8")
    return path


def read_sentinel(sentinel_dir: Path, session_id: str) -> dict | None:
    p = sentinel_dir / f"{session_id}.flag"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def fired(stdout: str) -> bool:
    """Hook 'fired' if it emitted the additionalContext JSON on stdout."""
    return "hookSpecificOutput" in stdout


# --- Test cases ---------------------------------------------------------

def case_no_sentinel_high_context(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 990_000)  # headroom = 10k - 100 < 15k threshold
    sentinel_dir = tmp / ".sentinels"
    sentinel_dir.mkdir(exist_ok=True)
    code, out, err = run_hook("s1", tx, sentinel_dir)
    if not fired(out):
        return False, f"expected fire; got stdout={out!r}, stderr={err!r}"
    s = read_sentinel(sentinel_dir, "s1")
    if not s or s["status"] != "pending":
        return False, f"expected pending sentinel; got {s}"
    return True, ""


def case_pending_small_growth_skips(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 990_100)  # grew 100 tokens since fire
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s2", "pending", 990_000)
    code, out, err = run_hook("s2", tx, sentinel_dir)
    if fired(out):
        return False, f"should not have re-fired within growth tolerance; got {out!r}"
    s = read_sentinel(sentinel_dir, "s2")
    if not s or s["status"] != "pending" or s["context_used"] != 990_000:
        return False, f"sentinel should be unchanged; got {s}"
    return True, ""


def case_pending_large_growth_refires(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    # context_used = 950_100 (= cache_read 950k + output 100)
    # pending sentinel recorded at 890_000 -> growth = 60_100 > 50k REFIRE
    # context_limit = 960_000 -> headroom = 9_900 < 15k threshold -> fires
    make_transcript(tx, 950_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s3", "pending", 890_000)
    code, out, err = run_hook("s3", tx, sentinel_dir, context_limit="960000")
    if not fired(out):
        return False, f"should re-fire after >REFIRE_GROWTH growth; got {out!r}, err={err!r}"
    s = read_sentinel(sentinel_dir, "s3")
    if not s or s["status"] != "pending" or s["context_used"] != 950_100:
        return False, f"sentinel should be refreshed; got {s}"
    return True, ""


def case_pending_shrunk_resets_and_fires(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    # After compaction: usage drops to a fresh, smaller value. We need
    # current_used < recorded AND headroom < threshold to test both
    # invalidate-and-fire-fresh in one go. Set context_limit small so
    # the fresh value also breaches threshold.
    make_transcript(tx, 100_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s4", "pending", 900_000)
    # context_limit=110000, headroom = 110000 - 100100 = 9900 < 15000 -> fires
    code, out, err = run_hook("s4", tx, sentinel_dir, context_limit="110000")
    if not fired(out):
        return False, f"should fire fresh after compaction reset; got {out!r}, err={err!r}"
    s = read_sentinel(sentinel_dir, "s4")
    if not s or s["status"] != "pending" or s["context_used"] != 100_100:
        return False, f"sentinel should reflect fresh fire after reset; got {s}"
    return True, ""


def case_saved_growing_context_skips(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 950_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s5", "saved", 900_000)
    code, out, err = run_hook("s5", tx, sentinel_dir)
    if fired(out):
        return False, f"saved sentinel with growing context should suppress; got {out!r}"
    s = read_sentinel(sentinel_dir, "s5")
    if not s or s["status"] != "saved":
        return False, f"saved sentinel should be preserved; got {s}"
    return True, ""


def case_saved_shrunk_resets_and_fires(tmp: Path) -> tuple[bool, str]:
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 100_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s6", "saved", 900_000)
    code, out, err = run_hook("s6", tx, sentinel_dir, context_limit="110000")
    if not fired(out):
        return False, f"saved + compaction should fire fresh; got {out!r}, err={err!r}"
    s = read_sentinel(sentinel_dir, "s6")
    if not s or s["status"] != "pending":
        return False, f"after re-fire the sentinel should be pending again; got {s}"
    return True, ""


def case_in_progress_growth_skips(tmp: Path) -> tuple[bool, str]:
    # in_progress should silence the hook during the save itself, even
    # if context has grown past the refire-growth tolerance.
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 950_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s7", "in_progress", 890_000)
    code, out, err = run_hook("s7", tx, sentinel_dir, context_limit="960000")
    if fired(out):
        return False, f"in_progress sentinel should suppress fire; got {out!r}"
    s = read_sentinel(sentinel_dir, "s7")
    if not s or s["status"] != "in_progress":
        return False, f"in_progress sentinel should be preserved; got {s}"
    return True, ""


def case_in_progress_shrunk_resets_and_fires(tmp: Path) -> tuple[bool, str]:
    # If compaction somehow happens while in_progress is set, the
    # sentinel still resets via the shrinkage check.
    tx = tmp / "tx.jsonl"
    make_transcript(tx, 100_000)
    sentinel_dir = tmp / ".sentinels"
    write_sentinel(sentinel_dir, "s8", "in_progress", 900_000)
    code, out, err = run_hook("s8", tx, sentinel_dir, context_limit="110000")
    if not fired(out):
        return False, f"in_progress + compaction should reset and fire; got {out!r}, err={err!r}"
    s = read_sentinel(sentinel_dir, "s8")
    if not s or s["status"] != "pending":
        return False, f"after re-fire the sentinel should be pending again; got {s}"
    return True, ""


CASES = [
    ("no sentinel + low headroom -> fires + writes pending", case_no_sentinel_high_context),
    ("pending + small growth -> skip", case_pending_small_growth_skips),
    ("pending + large growth -> reset + re-fire", case_pending_large_growth_refires),
    ("pending + context shrunk -> compaction reset + fire", case_pending_shrunk_resets_and_fires),
    ("saved + growing context -> skip", case_saved_growing_context_skips),
    ("saved + context shrunk -> compaction reset + fire", case_saved_shrunk_resets_and_fires),
    ("in_progress + growth -> skip (no pound during save)", case_in_progress_growth_skips),
    ("in_progress + context shrunk -> compaction reset + fire", case_in_progress_shrunk_resets_and_fires),
]


def main() -> int:
    print(f"Running {len(CASES)} sentinel state-machine cases...")
    print()
    passes = 0
    with tempfile.TemporaryDirectory() as td_root:
        for idx, (name, fn) in enumerate(CASES):
            # Each case gets its own temp dir with a fresh copy of the
            # hook script (so sentinel dir is isolated per case).
            tmp = Path(td_root) / f"case_{idx:02d}"
            tmp.mkdir(exist_ok=True)
            setup_test_hook_dir(tmp)
            ok, why = fn(tmp)
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}")
            if not ok:
                print(f"         {why}")
            else:
                passes += 1
    print()
    print(f"{passes}/{len(CASES)} passed")
    return 0 if passes == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
