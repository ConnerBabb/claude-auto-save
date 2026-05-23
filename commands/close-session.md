Close out the current session cleanly. Capture context, then handle local cleanup (uncommitted changes, WSL, worktree).

## Step 1: Save context

Invoke `/save-context` using the Skill tool (`skill: "save-context"`). Follow its instructions completely — it reviews what was accomplished, files GitHub issues for deferred work, updates stale docs, and writes anything notable to memory.

When that skill returns, continue with the cleanup steps below.

## Step 2: Handle uncommitted changes

If there are uncommitted tracked changes (`save-context` Step 1 surfaces these but doesn't act on them):
1. Show the user what's uncommitted
2. Ask: "Want me to commit these, stash them, or leave them?"
3. Act on their decision

## Step 3: Terminate WSL if running

```bash
wsl --list --running 2>/dev/null
```

If Ubuntu is running, terminate and save the cooldown sentinel so the next WSL-using actor waits the required 80s (per `feedback_wsl_cooldown`). Per `apify-actors/CLAUDE.md`, each actor that uses WSL keeps its own `.wsl_terminate_ts` in its actor directory:

```bash
wsl --terminate Ubuntu
echo "$(date +%s)" > apify-actors/<actor>/.wsl_terminate_ts
```

The cooldown is per-actor — each actor reads its own sentinel before launching. Write the timestamp to the directory of whatever actor you were running.

## Step 4: Exit worktree (if in one)

If the current working directory is inside a `.claude/worktrees/` path, ask the user:

"We're in a worktree. Want to keep it (for resuming later) or remove it?"

Then invoke the ExitWorktree tool with the user's choice:
- "keep" → `ExitWorktree(action: "keep")`
- "remove" → `ExitWorktree(action: "remove")`

If ExitWorktree refuses due to uncommitted changes, confirm with the user before retrying with `discard_changes: true`.

If NOT in a worktree, skip this step.
