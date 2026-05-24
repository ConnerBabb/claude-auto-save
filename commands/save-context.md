Save context from the current session — review what was done, capture anything important to memory or issue tracking, and update docs that drifted. Does NOT touch uncommitted changes, WSL state, or worktrees — those are out of scope for this skill. Handle them separately if needed.

## Step 0: Is there anything worth saving? (Escape hatch lives here)

Before walking the full flow, quickly assess: did anything *material* happen this session? A short read-only Q&A, a trivial edit, a throwaway debug loop, or a tiny doc tweak usually doesn't warrant a full save-context pass.

**If the answer is genuinely "nothing notable," this is your escape hatch.** Tell the user that explicitly, jump to **Step 5** to mark the session saved (which suppresses further hook nudges), then stop. Don't manufacture artifacts; don't pad the memory dir with empty observations just to look thorough.

**But do not use the escape to dodge real work.** If the session contains *any* of the following, it is NOT nothing-notable and you must continue through the full flow:
- A user correction, preference, or workflow rule you didn't know before
- A non-obvious decision you reached
- A failed approach + the working alternative
- A surprising discovery about the codebase, tooling, or environment
- A reference to an external system you'd want to find again

Otherwise continue to Step 1.

## Step 1: Session review

Review the conversation history and identify:

1. **What was accomplished** — PRs created/merged, features built, bugs fixed
2. **Uncommitted code changes** — run `git status --short` and `git diff --stat`. Skip these if the CWD isn't a git repo. Read-only here — don't act on uncommitted state, surface it so the user knows what's outstanding.
3. **Unpushed commits** — run `git log --oneline @{upstream}..HEAD`. Don't suppress stderr — if there's no upstream tracking branch, the "fatal: no upstream configured" output is actionable info (the branch hasn't been pushed yet). Skip if not in a git repo.
4. **Documentation updates needed** — do CLAUDE.md, README, or inline docs need updating to reflect what changed? Docs should match what's actually in the code.
5. **Follow-up work** — anything deferred, noted as "TODO", "follow-up", or "later" during the session
6. **Failed approaches and dead ends** — what was tried that didn't work, why it didn't work, and the workaround that succeeded (if any). High-value for future-you to avoid re-debugging. Capture as `reference` memories in Step 4.
7. **Things to remember** — decisions made, patterns established, lessons learned, or user preferences observed that should be saved to memory

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

If **every** proposed entry is rejected (or you had nothing to propose in the first place), skip 4b/4c/4d entirely and jump to **Step 5** to mark the session saved.

### 4b. Check for existing entries first

For each approved entry, check `MEMORY.md` and grep the memory directory for an existing entry on the same topic. **Prefer updating an existing memory in place over creating a duplicate.** If an existing entry covers similar ground, edit it rather than adding a new file.

### 4c. Build a plan and call the bundled helper

All memory writes go through the bundled `memory_write.py` helper. It acquires a lockfile on the memory directory, performs all writes atomically (temp file + rename), updates `MEMORY.md` dedup-aware, and (if invoked by a hook nudge) writes the per-session "saved" sentinel so the hook stops nudging.

The helper lives next to the hook script. On Claude Code's default setup that's `~/.claude/hooks/memory_write.py`. If the hook fired for this session, the nudge text in your current context will tell you the exact path plus the `--session-id` and `--transcript-path` values to use.

**Build a plan as JSON** with every entry you got approval to write:

```json
{
  "entries": [
    {
      "name": "user_dev_environment",
      "body": "---\nname: user-dev-environment\ndescription: ...\nmetadata:\n  type: user\n---\n\n..."
    }
  ],
  "index_entries": [
    "- [Dev environment](user_dev_environment.md) - one-line hook"
  ]
}
```

**Pipe the plan into the helper.** Two cases:

1. **Triggered by the hook nudge** (preferred — also marks the session saved so the hook stops nudging):

   ```bash
   python ~/.claude/hooks/memory_write.py \
       --memory-dir ~/.claude/projects/<project-id>/memory \
       --session-id <session_id_from_nudge> \
       --transcript-path <transcript_path_from_nudge> < plan.json
   ```

2. **Run manually outside a hook fire** (omit --session-id/--transcript-path; the helper writes memories only):

   ```bash
   python ~/.claude/hooks/memory_write.py \
       --memory-dir ~/.claude/projects/<project-id>/memory < plan.json
   ```

The `<project-id>` is the encoded CWD path your environment's auto-memory rules specify (typically `C--Users-Owner-<project>` on Windows or similar). The helper creates the directory if it doesn't exist.

### 4d. Memory file format and conventions

Each `entry.body` in the plan must include this frontmatter:

```markdown
---
name: {{short-kebab-case-slug}}
description: {{one-line summary used to decide relevance in future conversations - be specific}}
metadata:
  type: {{user | feedback | project | reference}}
---

{{body. For feedback/project types, lead with the rule/fact, then **Why:** and **How to apply:** lines.}}
```

Link related memories with `[[other-name]]` references, where `other-name` matches another file's `name:` slug. Link liberally — an `[[link]]` to an unwritten memory marks it as something worth writing later.

`index_entries` follow the format `- [Title](filename.md) - one-line hook`. Keep `MEMORY.md` under ~200 lines total (content past that may be truncated when loaded into future sessions). The helper dedups, so re-running with the same line is a no-op.

## Step 5: Mark this session saved

If this skill was triggered by a hook nudge, the nudge text in your context contains `--session-id` and `--transcript-path` values. Use them to write the saved sentinel so the hook stops nudging for the rest of this session (until compaction).

**If Step 4c ran with both `--session-id` and `--transcript-path`, the helper already wrote the saved sentinel — skip this step.**

Otherwise (Step 0 short-circuited, all entries rejected in 4a, or memories written without the helper for some reason), invoke the helper in sentinel-only mode:

```bash
python ~/.claude/hooks/memory_write.py \
    --mark-saved-only \
    --session-id <session_id_from_nudge> \
    --transcript-path <transcript_path_from_nudge>
```

The helper reads the current `context_used` from the transcript, writes the sentinel as `status: saved`, and the hook will stay quiet for the rest of this session unless compaction resets it.

If the skill was invoked manually outside a hook fire (no nudge values in context), skip this step — there's no sentinel to write and the hook isn't pending anything.

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
