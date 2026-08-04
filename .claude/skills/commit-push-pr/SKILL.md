---
name: commit-push-pr
description: >
  Commit, push a branch, and open a pull request in this repository. Use when
  asked to open a PR, push and PR, or ship a change for review. Owns the PR
  title and description rules, which matter more here than commit messages do:
  `main` is squash-only, so the PR title becomes the commit subject on `main`
  and the PR description becomes its body — and the changelog is built from them.
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git switch:*), Bash(git commit:*), Bash(git push:*), Bash(just:*), Bash(gh pr create:*), Bash(gh pr view:*), Read, Write
---

# Commit, push, open a PR

## 1. Commit

Follow the **`commit` skill** for staging and the commit message; load it if it
is not already loaded rather than guessing its rules.

## 2. Branch

`main` is protected (`protect-main`: PR required, no force-push, no deletion).
If `git branch --show-current` is `main`, branch first:

```bash
git switch -c wp-<n>-<short-slug>     # e.g. wp-1-domain-model
```

Check `git branch -vv` before pushing: a branch may already exist on the remote,
or have no upstream at all. Push accordingly — `git push -u origin <branch>` the
first time, plain `git push` after.

**`git push` runs the pre-push hooks**: `backend-pyrefly`,
`backend-import-linter`, `backend-unit-tests`, `frontend-type-check`,
`frontend-unit-tests`. This takes minutes and may exceed a default command
timeout. Run `just check` first so failures surface before the push, and give
the push a generous timeout. If it fails on something unrelated to your change,
report it — do not `--no-verify`.

## 3. PR title — the changelog-critical part

The ruleset allows **squash merges only**, and the repo is set to
`squash_merge_commit_title = PR_TITLE`. A merged PR becomes exactly one commit
on `main` whose subject is the PR title — always, regardless of how many commits
the branch has.

So the title must be a valid Conventional Commit:

```
<type>(<scope>): <subject>
```

- **type**: one of `feat`, `fix`, `perf`, `refactor`, `revert`, `docs`, `chore`,
  `build`, `ci`, `test`, `style`.
- **scope**: the work package (`wp-1`) or an area (`backend`, `frontend`, `mcp`,
  `ci`). Optional.
- **subject**: must **not** start with an uppercase letter and must **not** end
  with a period — `.github/workflows/pr-title.yml` fails the check otherwise.
  Summarise the whole branch, not just the last commit.

This is not cosmetic. `cliff.toml` sets `filter_unconventional = true`, so a
non-conventional subject on `main` is **dropped from `just changelog` with no
error** — the change would simply be missing from the release.

The check is **required**: `protect-main` lists the `pr-title` status check, so
a red check blocks the merge. If it fails, retitle the PR (`gh pr edit --title
"..."`) — the workflow re-runs on `edited`.

For a revert PR, GitHub's auto-generated `Revert "..."` title will fail the
check — retitle it `revert: <what was reverted>`.

## 4. PR body — it becomes the commit body

`squash_merge_commit_message = PR_BODY`, so the PR description is what lands on
`main` and what the changelog draft quotes. Write it as prose a reader outside
the branch can follow.

Fill in `.github/pull_request_template.md`: read it, complete the **What**
section, and tick only the checklist items you actually verified.

Markdown headings in the body (`## What`) are demoted to bold in the changelog
draft, so they are safe to keep.

## 5. Open it

Write the body to a file and pass `--body-file` — the template is full of
backticks and brackets that break inline `--body "..."` quoting:

```bash
gh pr create --base main --title "<conventional title>" --body-file <path>
```

Report the URL. Do not merge, and do not enable auto-merge, unless asked.
