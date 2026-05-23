Close out the current session cleanly. Capture context, then handle local cleanup (uncommitted changes, worktree).

## Step 1: Save context

Invoke `/save-context` using the Skill tool (`skill: "save-context"`). Follow its instructions completely — it reviews what was accomplished, files GitHub issues for deferred work, updates stale docs, and writes anything notable to memory.

When that skill returns, continue with the cleanup steps below.

## Step 2: Handle uncommitted changes

If there are uncommitted tracked changes (`/save-context` Step 1 surfaces these but doesn't act on them):
1. Show the user what's uncommitted
2. Ask: "Want me to commit these, stash them, or leave them?"
3. Act on their decision

## Step 3: Terminate long-running side processes (optional, project-specific)

If this project relies on a long-running shell or VM session that you started during this work (e.g., WSL, a dev container, a persistent SSH tunnel, a background dev server), wind it down now. Skip if none apply.

## Step 4: Exit worktree (if in one)

If the current working directory is inside a `.claude/worktrees/` path, ask the user:

"We're in a worktree. Want to keep it (for resuming later) or remove it?"

Then invoke the ExitWorktree tool with the user's choice:
- "keep" → `ExitWorktree(action: "keep")`
- "remove" → `ExitWorktree(action: "remove")`

If ExitWorktree refuses due to uncommitted changes, confirm with the user before retrying with `discard_changes: true`.

If NOT in a worktree, skip this step.
