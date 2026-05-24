Dismiss the auto-save nudge for this session without running `/save-context`.

Use this when you've decided the current session has nothing material worth saving (a quick experiment, a read-only Q&A, a throwaway debug loop, etc.) and you just want the hook to stop nudging.

## What it does

Writes the `saved` sentinel for this session without touching memory. The hook stays quiet for the rest of this fill-up cycle. If auto-compaction happens later, the sentinel resets and the hook will get a fresh chance to fire on the post-compaction conversation - so this is *skip for now*, not *skip forever*.

## How to invoke

The hook nudge in your current context contains `--session-id` and `--transcript-path` values. Pass them to the bundled helper in sentinel-only mode:

```bash
python ~/.claude/hooks/memory_write.py \
    --mark-saved-only \
    --session-id <session_id_from_nudge> \
    --transcript-path <transcript_path_from_nudge>
```

That's the whole skill. One command, no memory writes, no review steps.

## When NOT to use this

- If anything *non-obvious* happened in the session (decisions made, patterns established, failed approaches discovered, user preferences observed), use `/save-context` instead - those are exactly the kind of things that get lost on compaction.
- If the hook hasn't fired (no nudge in your current context), there's no pending sentinel to clear and `/skip-save` is a no-op.
