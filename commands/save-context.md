Save context from the current session — review what was done, capture anything important to memory or issue tracking, and update docs that drifted. Does NOT touch uncommitted changes, WSL state, or worktrees — those are out of scope for this skill. Handle them separately if needed.

## Step 0: Is there anything worth saving?

Before walking the full flow, quickly assess: did anything *material* happen this session? A short read-only Q&A, a single trivial edit, or a tiny doc tweak usually doesn't warrant a full save-context pass.

If the answer is "nothing notable," tell the user that explicitly and stop. Don't manufacture artifacts. Otherwise continue.

## Step 1: Session review

Review the conversation history and identify:

1. **What was accomplished** — PRs created/merged, features built, bugs fixed
2. **Uncommitted code changes** — run `git status --short` and `git diff --stat`. Skip these if the CWD isn't a git repo. Read-only here — don't act on uncommitted state, surface it so the user knows what's outstanding.
3. **Unpushed commits** — run `git log --oneline @{upstream}..HEAD`. Don't suppress stderr — if there's no upstream tracking branch, the "fatal: no upstream configured" output is actionable info (the branch hasn't been pushed yet). Skip if not in a git repo.
4. **Documentation updates needed** — do CLAUDE.md, README, or inline docs need updating to reflect what changed? Docs should match what's actually in the code.
5. **Follow-up work** — anything deferred, noted as "TODO", "follow-up", or "later" during the session
6. **Things to remember** — decisions made, patterns established, lessons learned, or user preferences observed that should be saved to memory

Present all findings to the user before taking action in any later step.

## Step 2: Create GitHub Issues for follow-ups

For any follow-up work identified in Step 1 that isn't already tracked:

1. List the items and ask the user which ones to create as GitHub Issues.
2. Create approved issues with `gh issue create`. **Requires the `gh` CLI** — skip this step if it isn't installed.
3. Use conventional prefixes in titles: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.

## Step 3: Documentation updates

If any documentation updates were identified:

1. List them and ask the user which to do now vs skip.

2. **For every CLAUDE.md edit, always choose the minimal scope.** Write each fact, rule, or convention to the most narrowly-scoped `CLAUDE.md` that fully covers it:
   - If the rule applies repo-wide, edit the root `CLAUDE.md`.
   - If it only applies to one component (a backend, a webapp, an extension, a specific package, a specific subdirectory), edit *that directory's* `CLAUDE.md`.
   - If the relevant directory genuinely needs documentation but doesn't have a `CLAUDE.md` yet, **create one there** rather than pushing the content up to a wider scope.
   - Never write directory-specific guidance into a parent `CLAUDE.md` when a narrower file is appropriate. The root `CLAUDE.md` should only carry repo-wide facts.

3. Make the approved updates (CLAUDE.md files at their correct scope, inline docs, README sections, etc.).

4. Commit and push if on a branch, or note as a follow-up issue.

## Step 4: Save memories

For any decisions, preferences, or lessons learned worth remembering across future sessions, capture them to the memory system. The four-substep flow is **propose → confirm → check → write**.

### 4a. Propose entries and wait for approval

Before writing anything to disk, list each proposed memory entry with:

- Proposed filename (kebab-case)
- Memory type (user / feedback / project / reference)
- One-line description
- The full body content

Ask the user to confirm — per-entry or batch ("approve all"). Do **not** skip this confirmation, even if the user said "save memories" earlier in the session — they may want to edit or drop specific entries before they hit disk. If the user rejects an entry, drop it.

### 4b. Check for existing entries first

For each approved entry, check `MEMORY.md` and grep the memory directory for an existing entry on the same topic. **Prefer updating an existing memory in place over creating a duplicate.** If an existing entry covers similar ground, edit it rather than adding a new file.

### 4c. Write the files

The memory directory is whatever your environment's auto-memory rules specify. On Claude Code's default setup, this is `~/.claude/projects/<project-id>/memory/` where `<project-id>` is the encoded CWD path (e.g., `C--Users-Owner-myproject`). Create the directory if it doesn't exist.

Each memory file uses this frontmatter:

```markdown
---
name: {{short-kebab-case-slug}}
description: {{one-line summary used to decide relevance in future conversations — be specific}}
metadata:
  type: {{user | feedback | project | reference}}
---

{{body — for feedback/project types, lead with the rule/fact, then **Why:** and **How to apply:** lines}}
```

Link related memories with `[[other-name]]` references, where `other-name` matches another file's `name:` slug. Link liberally — a `[[link]]` to an unwritten memory marks it as something worth writing later.

### 4d. Update the index

`MEMORY.md` (in the same directory) is a one-line-per-entry index, no frontmatter. Keep it under ~200 lines (content past that may be truncated when loaded into future sessions).

```markdown
- [Title](filename.md) — one-line hook
```

Add or update entries as needed.

### Memory types — what goes where

| Type | Purpose |
|---|---|
| `user` | Role, expertise, preferences, what the user already knows vs. what's new to them |
| `feedback` | Corrections AND confirmations about how to approach work. Include `**Why:**` and `**How to apply:**` lines so future-you can judge edge cases |
| `project` | Who/what/why/by-when for current work — incidents, deadlines, motivations, decisions |
| `reference` | Pointers to external systems — Linear projects, dashboards, runbooks, GitHub repos, channels |

### Skip these

- Anything derivable from code or git state — file paths, function signatures, architecture, who-changed-what (read the code; check `git log`)
- Anything already documented in CLAUDE.md
- Ephemeral task state — current conversation, in-progress work, anything that will be stale within a day
- Activity logs and dated commit lists — they rot fast without delivering value
