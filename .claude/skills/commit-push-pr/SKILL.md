---
name: commit-push-pr
description: >
  Commit, push a branch, and open a pull request that closes its originating
  GitHub issue. Use when asked to open a PR, push and PR, or ship a change for
  review. Owns the PR title and description rules, which matter more here than
  commit messages do: `main` is squash-only, so the PR title becomes the commit
  subject on `main` and the PR description becomes its body — and the changelog
  is built from them. Omits test/validation plans; CI covers that.
argument-hint: "[issue-number]"
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git switch:*), Bash(git commit:*), Bash(git push:*), Bash(just:*), Bash(gh pr create:*), Bash(gh pr view:*), Bash(gh pr edit:*), Bash(gh issue view:*), Bash(gh issue list:*), Bash(gh api:*), Read, Write, Edit
---

# Commit, push, open a PR

## Context

Run these and read the output before acting:

- `git status`
- `git branch --show-current`
- `git log main..HEAD --format='=== %s%n%b'` — the full commit messages, bodies
  included. They are the decision record and the raw material for the PR body;
  `--oneline` is not enough.
- `git diff main...HEAD --stat` and `git diff main...HEAD` — what actually
  shipped across the whole branch, not just the last commit.

The base is `main` unless the user says otherwise.

## 1. Commit

Follow the **`commit` skill** for staging and the commit message; load it if it
is not already loaded rather than guessing its rules.

If the work is already committed, skip straight to step 2. If the tree is dirty
with changes that belong in this PR, commit them first — they will not be in the
PR otherwise.

## 2. Branch and push

`main` is protected (`protect-main`: PR required, no force-push, no deletion).
If `git branch --show-current` is `main`, branch first:

```bash
git switch -c <what-it-achieves>      # e.g. coaching-loop-reads
```

Check `git branch -vv` before pushing: a branch may already exist on the remote,
or have no upstream at all. Push accordingly — `git push -u origin HEAD` the
first time, plain `git push` after.

**`git push` runs the pre-push hooks**: `backend-pyrefly`,
`backend-import-linter`, `backend-unit-tests`, `frontend-type-check`,
`frontend-unit-tests`. This takes minutes and may exceed a default command
timeout. Run `just gate` first so failures surface before the push (it is `just check` plus the
  migration heuristic the pre-push hook runs), and give
the push a generous timeout. If it fails on something unrelated to your change,
report it — do not `--no-verify`.

## 3. Find the originating issue

Work here is tracked as issues, and the PR should close the one it answers.

Use `$ARGUMENTS` if an issue number was passed; otherwise look for a `#N`
reference in the branch name or in the branch's commit messages; otherwise
`gh issue list` and match on subject. Read it with `gh issue view <N>` — you
need both its **intent** (for `## Why`, and to compare against what shipped)
and its **label**, which selects the body template:

| label | template |
|---|---|
| `enhancement` | Feature |
| `bug` | Bug |
| `documentation`, none, or a chore with no issue | Feature template, or just `## Why` / `## What changed` |

Area labels (`api`, `domain`, `mcp`, `data`) are orthogonal — they suggest the
title's scope, not the template.

If there is genuinely no issue, skip the `Closes` line rather than inventing a
number.

## 4. PR title — the changelog-critical part

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
- **scope**: the area or subsystem the change is about (`backend`, `frontend`,
  `mcp`, `anchors`, `sessions`, `ci`). Optional. Historic commits use work
  packages (`wp-1`); new ones do not — the WPs ended with the MVP build.
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

## 5. PR body — it becomes the commit body

`squash_merge_commit_message = PR_BODY`, so the PR description is what lands on
`main` and what the changelog draft quotes. Write it as prose a reader outside
the branch can follow.

Write it to a temp file and pass `--body-file`. Use the template that matches
the issue's label.

### There is no line-length limit — one line per paragraph

**A PR body line has no maximum length. Do not wrap it at 72, 76, 80, or any
other column.** Each paragraph is exactly one line in the file, however long
that line ends up; paragraphs run 400–900 characters on a single line in this
repo's merged PRs, and that is correct.

The rule is easy to break because this skill hands off to the `commit` skill,
where commit bodies *do* wrap at ~76 columns. That rule stops at the commit
message and does not reach the PR body. GitHub renders pull-request
descriptions with GFM *hard line breaks on*, so every newline inside a
paragraph becomes a literal `<br>` and wrapped prose renders as a ragged
column.

So: one line per paragraph, no matter how long, with a blank line between
paragraphs. Lists, headings and fenced blocks are block elements and are
unaffected.

This costs nothing on `main`: when GitHub builds the squash commit from
`PR_BODY` it hard-wraps the body to 72 columns itself, so `git log` gets a
conventionally wrapped message without you wrapping the source. Both ends of
the round trip are handled — write for the web view and let GitHub wrap.

That wrapper is also why the body must stay **flat**: it flattens nested lists
to a single level, breaks table rows mid-row, and splits git trailers across
lines so parsers stop seeing them. Use top-level lists and prose; keep tables
and `Key: value` trailers out of a PR description entirely. `Closes #<N>` is a
short standalone line and survives.

Markdown headings in the body (`## Why`) are demoted to bold in the changelog
draft, so they are safe to keep.

### Templates

Each `<...>` placeholder below is **one unwrapped line** in the file you write,
regardless of how long the paragraph is.

**Feature:**

```markdown
## Why

<The intent: the problem this solves and what we set out to build. Take it from the issue, not from the diff.>

## What changed

<What actually shipped, grounded in the real diff. Name the route, setting, model, service or migration; do not list files.>

## Notes

<Decisions and tradeoffs, or where this deviated from or refined the issue. Omit the section if there is nothing real to say.>

Closes #<N>
```

**Bug:**

```markdown
## Root cause

<What was actually causing the bug.>

## The fix

<How it was fixed, and — where a future edit could reintroduce it silently — the test that now fails when it does.>

## Impact

<What happened while the bug was live: which surface, whose data, how it showed up. Say plainly if stored rows are wrong and whether anything backfills them.>

## Notes

<Decisions and tradeoffs, or anything a reviewer should know that the diff and the issue don't show. Omit if none.>

Closes #<N>
```

Fill `## Notes` by actively comparing the issue's intent against what actually
shipped: pull in any decision or tradeoff recorded in the commit messages, plus
any deviation you spot between the spec and the diff — built X instead of the
specified Y, scope trimmed, migration number shifted, an approach changed. Omit
the section only if that comparison genuinely turns up nothing; do not pad it.

**No "Testing" or "How to validate" section**, and no checklist. `just check`
and CI are the gate, and a hand-ticked list is a claim no one verifies.

Do not add a `Co-Authored-By:` trailer or any other AI-attribution footer — the
user does not want this in commits or PRs.

### Verify the wrapping

Check it rather than eyeballing it, since the raw file looks identical either
way:

```bash
gh api repos/<owner>/<repo>/pulls/<n> -H "Accept: application/vnd.github.html+json" \
  --jq .body_html | grep -c '<br>'
```

Zero is correct. Anything else means a paragraph is hard-wrapped; rewrite the
body file and `gh pr edit <n> --body-file <path>`.

## 6. Open it

```bash
gh pr create --base main --title "<conventional title>" --body-file <path>
```

`--body-file`, never inline `--body "..."` — the body is full of backticks and
brackets that break shell quoting.

If a PR for this branch already exists, report it instead of creating a
duplicate. Report the URL. Do not merge, and do not enable auto-merge, unless
asked.
