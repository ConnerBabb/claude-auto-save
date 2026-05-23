Save context from the current session — review what was done, capture anything important to memory or issue tracking, and update docs that drifted. Does NOT touch uncommitted changes, WSL state, or worktrees — that's `/close-session`'s job. If you also need to act on uncommitted state, exit a worktree, or terminate WSL, use `/close-session` (it invokes this skill first, then handles cleanup).

## Step 1: Session review

Review the conversation history and identify:

1. **What was accomplished** — PRs created/merged, features built, bugs fixed
2. **Uncommitted code changes** — run `git status --short` and `git diff --stat` (read-only — don't act on these here; surface them so the user knows what's outstanding)
3. **Unpushed commits** — run `git log --oneline @{upstream}..HEAD`. Don't suppress stderr — if there's no upstream tracking branch, the agent should see "fatal: no upstream configured" and report it as actionable info (the branch hasn't been pushed yet).
4. **Documentation updates needed** — do CLAUDE.md, README, or inline docs need updating to reflect what changed? All CLAUDE.md docs must 100% reflect what's in the code before the PR is merged.
5. **Follow-up work** — anything deferred, noted as "TODO", "follow-up", or "later" during the session
6. **Things to remember** — decisions made, patterns established, lessons learned, or user preferences observed that should be saved to memory

Present all findings to the user before taking action.

## Step 2: Create GitHub Issues for follow-ups

For any follow-up work identified in Step 1 that isn't already tracked:

1. List the items and ask the user which ones to create as GitHub Issues
2. Create issues with `gh issue create` for approved items
3. Use conventional prefixes in titles: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`

## Step 3: Documentation updates

If any documentation updates were identified:

1. List them and ask the user which to do now vs skip
2. Make the approved updates (CLAUDE.md, inline docs, etc.)
3. Commit and push if on a branch, or note as a follow-up issue

## Step 4: Save memories

For any decisions, preferences, or lessons learned worth remembering across sessions, save them to the memory system per the auto-memory rules in CLAUDE.md. Examples:
- User preferences or corrections observed during the session
- Project decisions that affect future work
- Reference information (URLs, account IDs, config values)
- Proxy/account status updates

Skip anything derivable from current code/git state, anything ephemeral, and anything already documented in CLAUDE.md.
