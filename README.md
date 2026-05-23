# claude-auto-save

A Claude Code hook + skill package that automatically extracts learnings, decisions, and project context from the conversation into memory **before** auto-compaction destroys the model's attention over the full session.

## The problem

Claude Code's auto-compaction is lossy. When your context fills up, Claude summarizes the conversation to free space — but summarization can't recover the rich attention pathways the model had while the full conversation was loaded. Anything subtle, surprising, or judgment-laden that wasn't explicitly highlighted gets flattened.

If you're working in a large context window (especially 1M Opus), that's a lot of valuable signal lost on every compaction.

## What this does

A `UserPromptSubmit` hook watches your transcript file. When remaining headroom drops below a configurable threshold (default: 15,000 tokens), it injects an instruction telling Claude to invoke the `/save-context` skill **on the next turn, while the full conversation is still in attention**. That extraction runs with the model's actual judgment over the entire context — then normal compaction proceeds afterward with the high-value notes already on disk.

A per-session sentinel file controls when the hook re-fires:
- **Saved sentinel** (written by `/save-context` when it completes via the bundled `memory_write.py` helper) suppresses further nudges for this session.
- **Pending sentinel** (written by the hook itself when it fires) suppresses immediate re-firing while `/save-context` is supposed to be running. If context grows another 50k tokens past the fire point without a saved sentinel landing, the hook assumes the nudge was ignored and re-fires.
- **Compaction reset.** If current `context_used` drops below the recorded value (the only way that happens is auto-compaction), the sentinel is invalidated and the hook fires fresh on the new post-compaction conversation. `session_id` doesn't change across compaction — only the in-attention context shrinks — so the same session can validly trigger multiple saves across its lifetime.

Concurrent same-project sessions are race-safe: all memory writes serialize through a lockfile on the memory directory.

## Install

Requires Python 3.8+ (already present on virtually every dev machine). Windows users: run this from Git Bash (it ships with Git for Windows) or WSL.

```bash
git clone https://github.com/ConnerBabb/claude-auto-save.git && cd claude-auto-save && python3 install.py
```

If `python3` isn't on your `PATH`, use `python` instead.

The installer:
- Copies `check-context-headroom.py` to `~/.claude/hooks/` (the hook)
- Copies `memory_write.py` to `~/.claude/hooks/` (the locked-write helper used by `/save-context`)
- Copies `save-context.md` to `~/.claude/commands/` (skipped if already present; pass `--force` to overwrite)
- Adds a `UserPromptSubmit` hook entry to `~/.claude/settings.json`, merging with your existing config rather than clobbering it
- Resolves the absolute Python path and script path at install time so the config has no path-expansion surprises

It's idempotent — re-running won't duplicate hook entries.

## What gets installed

| File | Destination | Purpose |
|---|---|---|
| `check-context-headroom.py` | `~/.claude/hooks/` | The `UserPromptSubmit` hook |
| `memory_write.py` | `~/.claude/hooks/` | Atomic locked memory-write helper invoked by `/save-context`; also writes the saved-sentinel on completion |
| `save-context.md` | `~/.claude/commands/` | The `/save-context` slash command — session review + memory writes |
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
| `CLAUDE_HOOK_CONTEXT_LIMIT` | `1000000` | Total context window in tokens. Defaults to 1M because current Opus uses a 1M window. Override with any explicit integer (e.g. `"200000"`), or set to `"auto"` to fall back to 200k unless the session has shown evidence of a larger window (any prior turn's input exceeded 200k, or the model string carries a `[1m]` flag). |
| `CLAUDE_HOOK_THRESHOLD` | `15000` | Fire the nudge when remaining headroom drops below this many tokens. Lower = fires later (less safety margin). Higher = fires earlier (more room for the extraction turn itself). |
| `CLAUDE_HOOK_REFIRE_GROWTH` | `50000` | If a pending sentinel exists and `context_used` has grown by more than this many tokens past the recorded fire point without `/save-context` having completed, the hook assumes the nudge was ignored and re-fires. |

## How it works

1. On every `UserPromptSubmit`, the hook reads the transcript JSONL file (path supplied in the hook payload).
2. It walks the tail of the file backwards to find the most recent assistant message's `usage` block (real API token counts: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens + output_tokens`).
3. **Sentinel check** at `<hook-dir>/.sentinels/<session_id>.flag`:
   - If `status: saved` and `current_used >= recorded`: skip silently.
   - If `current_used < recorded` (compaction happened): delete sentinel and proceed.
   - If `status: pending` and `current_used <= recorded + REFIRE_GROWTH`: skip silently (save in progress).
   - If `status: pending` and `current_used > recorded + REFIRE_GROWTH`: nudge was probably ignored — re-fire.
4. If the sentinel allows firing and `headroom < threshold`: write a *pending* sentinel and emit a `hookSpecificOutput.additionalContext` JSON nudge to stdout.
5. Claude Code injects the nudge into the next turn. Claude invokes `/save-context`, which builds a plan and pipes it to `memory_write.py`. The helper takes a lockfile on the memory directory, writes the entries atomically, updates `MEMORY.md` dedup-aware, then writes the *saved* sentinel — all under the same lock.
6. Subsequent hook fires in this session see the *saved* sentinel and stay quiet, unless compaction or significant growth resets it.

To force a re-fire mid-session, delete the matching sentinel file.

## The bundled skill: `/save-context`

Reviews the session, surfaces follow-ups and uncommitted state, and asks before writing memories. Steps:

0. Skip-if-nothing-material early exit
1. Session review — what was accomplished, uncommitted changes, unpushed commits, docs that drifted, follow-ups, things to remember
2. Create GitHub Issues for follow-ups (user-approved, requires `gh` CLI)
3. Documentation updates that drifted from reality
4. Propose memory entries, wait for user approval, then write to the auto-memory directory using the bundled frontmatter conventions

Out of scope for this skill: uncommitted code changes, WSL state, and worktree exit. Handle those separately if needed.

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

Removes the hook entry from `settings.json` and deletes `check-context-headroom.py`, `memory_write.py`, and the sentinel directory. Leaves skill files in place (they're user-editable; remove manually if desired).

## Dry run

To preview without making changes:

```bash
python install.py --dry-run
python install.py --uninstall --dry-run
```

## License

MIT.
