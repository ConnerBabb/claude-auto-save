# claude-auto-save

A Claude Code hook + skill package that automatically extracts learnings, decisions, and project context from the conversation into memory **before** auto-compaction destroys the model's attention over the full session.

## The problem

Claude Code's auto-compaction is lossy. When your context fills up, Claude summarizes the conversation to free space — but summarization can't recover the rich attention pathways the model had while the full conversation was loaded. Anything subtle, surprising, or judgment-laden that wasn't explicitly highlighted gets flattened.

If you're working in a large context window (especially the 1M Opus beta), that's a lot of valuable signal lost on every compaction.

## What this does

A `UserPromptSubmit` hook watches your transcript file. When remaining headroom drops below a configurable threshold (default: 15,000 tokens), it injects an instruction telling Claude to invoke the `/save-context` skill **on the next turn, while the full conversation is still in attention**. That extraction runs with the model's actual judgment over the entire context — then normal compaction proceeds afterward with the high-value notes already on disk.

The hook fires **once per session** via a sentinel file, so it doesn't nag.

## Install

Requires Python 3.8+ (already present on virtually every dev machine).

```bash
git clone https://github.com/ConnerBabb/claude-auto-save.git
cd claude-auto-save
python install.py
```

The installer:
- Copies `check-context-headroom.py` to `~/.claude/hooks/`
- Copies `save-context.md` and `close-session.md` to `~/.claude/commands/` (skipped if already present; pass `--force` to overwrite)
- Adds a `UserPromptSubmit` hook entry to `~/.claude/settings.json`, merging with your existing config rather than clobbering it
- Resolves the absolute Python path and script path at install time so the config has no path-expansion surprises

It's idempotent — re-running won't duplicate hook entries.

## What gets installed

| File | Destination | Purpose |
|---|---|---|
| `check-context-headroom.py` | `~/.claude/hooks/` | The hook itself |
| `save-context.md` | `~/.claude/commands/` | The `/save-context` slash command — session review + memory writes |
| `close-session.md` | `~/.claude/commands/` | The `/close-session` slash command — runs `/save-context`, then handles uncommitted changes and worktree exit |
| (hook entry) | `~/.claude/settings.json` | `UserPromptSubmit` registration |

## Configuration

The hook reads two environment variables. Set them in the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_HOOK_CONTEXT_LIMIT": "1000000",
    "CLAUDE_HOOK_THRESHOLD": "15000"
  }
}
```

| Variable | Default | What it does |
|---|---|---|
| `CLAUDE_HOOK_CONTEXT_LIMIT` | (auto) | Total context window in tokens. The hook tries to detect this from usage history (assumes 1M if any turn has exceeded 200k), but if you're consistently running the 1M-context Opus beta, set this explicitly to `1000000` so the hook fires near the right boundary from the start of every session. |
| `CLAUDE_HOOK_THRESHOLD` | `15000` | Fire the nudge when remaining headroom drops below this many tokens. Lower = fires later (less safety margin). Higher = fires earlier (more room for the extraction turn itself). |

## How it works

1. On every `UserPromptSubmit`, the hook reads the transcript JSONL file (path supplied in the hook payload).
2. It walks the tail of the file backwards to find the most recent assistant message's `usage` block (real API token counts: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`).
3. Compares against the resolved context limit. If `headroom < threshold` and no sentinel exists for this `session_id`, it emits a `hookSpecificOutput.additionalContext` JSON nudge to stdout.
4. Claude Code injects that text into the next turn's context. Claude sees the instruction, invokes `/save-context` while the full conversation is still loaded, then handles your actual prompt.
5. A `.flag` file is written under `<hook-dir>/.sentinels/<session_id>.flag` to ensure the hook fires only once per session.

To force a re-fire mid-session, delete the matching sentinel file.

## Bundled skills

### `/save-context`

Reviews the session, asks the user about open follow-ups, and writes memories. Does **not** touch uncommitted changes, WSL state, or worktrees — those are `/close-session`'s job. Steps:

1. Session review — what was accomplished, uncommitted changes, unpushed commits, docs that drifted, follow-ups, things to remember
2. Create GitHub Issues for follow-ups (user-approved)
3. Documentation updates that drifted from reality
4. Save memories per the auto-memory rules in your `CLAUDE.md`

### `/close-session`

The bigger sibling — runs `/save-context` first, then handles uncommitted changes (commit / stash / leave), terminates WSL, and exits the worktree if applicable.

## Cross-platform notes

- **Windows**: tested on PowerShell 5.1 + Python 3.x. Hook command uses the running Python interpreter's absolute path with forward slashes (avoids `\U` and `%c` style parser quirks in Claude Code's hook command pipeline).
- **Linux**: verified end-to-end on Ubuntu 22.04 (WSL) with Python 3.10. Installer writes `/usr/bin/python3 /home/<user>/.claude/hooks/check-context-headroom.py` into settings.json. Hook fire, idempotent re-install, and uninstall all confirmed working.
- **macOS**: should work — installer uses `pathlib` and `sys.executable` which are platform-agnostic — but not yet verified on a Mac. Reports welcome.
- **No external dependencies.** Stdlib only.

The installer also runs `claude --version` to detect whether Claude Code is on PATH; if not, it warns but proceeds (the hook simply won't fire until Claude Code is installed).

## Uninstall

```bash
python install.py --uninstall
```

Removes the hook entry from `settings.json` and deletes `check-context-headroom.py` and the sentinel directory. Leaves skill files in place (they're user-editable; remove manually if desired).

## Dry run

To preview without making changes:

```bash
python install.py --dry-run
python install.py --uninstall --dry-run
```

## License

MIT.
