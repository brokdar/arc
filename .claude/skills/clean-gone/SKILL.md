---
name: clean-gone
description: >
  Delete local git branches whose upstream is gone (the remote branch was
  deleted, usually after a squash merge), including any worktrees attached to
  them. Use when asked to clean up merged, stale, or "[gone]" branches. Always
  requires explicit user confirmation before deleting anything.
allowed-tools: Bash(git branch:*), Bash(git fetch:*), Bash(git worktree:*), Bash(git rev-parse:*), Bash(git for-each-ref:*), Bash(git log:*), Bash(git status:*)
---

# Clean up branches whose upstream is gone

`delete_branch_on_merge` is on, so a merged PR's remote branch disappears and
the local copy is left with a gone upstream.

**This skill deletes work. Two of its steps are irreversible in practice.**
Never run step 4 without the user explicitly approving the exact list from
step 3.

## 1. Refresh

A gone upstream is only visible after remote-tracking refs are pruned:

```bash
git fetch --prune
```

## 2. Build the candidate list — do NOT parse `git branch -vv`

`git branch -vv` puts the commit subject on the same line, so grepping it for
`gone]` also matches a branch whose *commit message* mentions a gone branch —
which then gets deleted. Ask git for the fields directly instead:

```bash
git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads/ \
  | awk '$2 == "[gone]" {print $1}'
```

That is the only list to work from. A branch with no upstream at all prints an
empty second field and is correctly excluded.

## 3. Inspect each candidate, then stop and ask

`[gone]` means "the upstream ref vanished". It does **not** mean merged, and it
carries no signal about unpushed work — a branch that was pushed, remote-deleted,
then committed to locally still shows exactly `[gone]`. `git branch --merged` is
no help either: a squash-merged branch is not an ancestor of `main`.

For every candidate, report:

```bash
# commits not in main — anything here is work that would be lost
git log --oneline origin/main..<branch>
# is a worktree attached, and is it dirty?
git worktree list
git -C <worktree-path> status --porcelain    # only if one is attached
```

Then **present the list to the user with those results and wait for approval.**
Do not proceed on your own judgement. Call out explicitly:

- any branch with commits not in `origin/main`;
- any attached worktree whose `status --porcelain` is non-empty — `git worktree
  remove --force` deletes **untracked** files too, which are in no object
  database and are gone for good. (Claude Code's `Agent` tool can create
  worktrees, so these may hold another agent's in-flight work.)
- the current branch, if it appears — it cannot be deleted while checked out.

By contrast, deleting a branch is recoverable: `git branch -D` prints the sha,
and `git reflog` still has it. Worktree removal is the step to be careful about.

## 4. Delete — only the approved names, one at a time

Use the exact names the user approved. Do not re-derive the list here; state
may have changed since step 3.

```bash
git worktree prune                       # drop stale registrations first
git worktree list --porcelain            # locate a worktree by path, space-safe
git worktree remove <path>               # NO --force unless the user approved a dirty worktree
git branch -D <branch>
```

`-D` rather than `-d` is required: a squash-merged branch is not an ancestor of
`main`, so `-d` refuses it. That also means `-D` gives you no safety net — step 3
is the safety net.

Do not use `awk '{print $1}'` on `git worktree list` to get paths: it truncates
at the first space. Use `git worktree list --porcelain` and read the `worktree`
lines.

If the step-2 list is empty, say no cleanup was needed and stop — do not run
steps 3 or 4.
