---
name: commit
description: >
  Create a git commit in this repository. Use when asked to commit, stage and
  commit, or "save this work". Covers the Conventional Commits format the
  commit-msg hook checks and `just changelog` parses, the work-package scope
  convention, the pre-commit hooks that rewrite files mid-commit, and the
  companion artifacts a change must ship with.
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git commit:*), Bash(just:*), Read, Edit
---

# Commit

## 1. Gather context

```bash
git status
git diff HEAD
git branch --show-current
git log -5 --format='=== %s%n%b'
```

Read the last one properly. Commit **bodies** here are long-form prose and set
the voice for the PR description and the changelog — `--oneline` is not enough.

## 2. Where this commit ends up

`main` is **squash-only** (`protect-main` ruleset), and the repository is set to
`squash_merge_commit_title = PR_TITLE`, `squash_merge_commit_message = PR_BODY`.
So the branch collapses into one commit on `main` whose subject is the **PR
title** and whose body is the **PR description** — not this commit message.

Branch commits are still the review unit and the raw material for that PR
description, so write them well. But the changelog-critical artifact is the PR
title, which no local hook can see; see the `commit-push-pr` skill.

## 3. Message format

```
<type>(<scope>): <subject>

<body>
```

**Type** — one of these eleven, and nothing else:

| type | changelog section | use for |
|---|---|---|
| `feat` | Added | new capability |
| `fix` | Fixed | bug fix |
| `perf`, `refactor`, `revert` | Changed | behaviour-preserving rework, reverts |
| `docs` | Documentation | README, guides, rules files |
| `chore`, `build`, `ci`, `test`, `style` | Internal | deps, tooling, CI, tests, formatting |

The list is duplicated in `.pre-commit-config.yaml`, `cliff.toml` and
`.github/workflows/pr-title.yml` — adding a type means editing **all three**.

Append `!` before the colon for a breaking change (`feat(mcp)!: ...`), and put
the explanation in a `BREAKING CHANGE:` footer — the changelog renders that
footer, and drops any other trailing `Token: value` footer.

**Scope** — the area or subsystem the change is about (`backend`, `frontend`,
`mcp`, `anchors`, `sessions`, `ci`). Optional (nothing enforces it), but every
commit in this repo's history has one. Historic commits are scoped by work
package (`wp-0`); the WPs ended with the MVP build — never invent a WP number
for new work.

**Subject** — imperative, lowercase start, no trailing period. Keep it short;
existing subjects run to ~87 chars, so there is no hard limit, but the PR title
derived from it is what readers see.

Be aware the local hook is **lax**: it only rejects subjects it cannot parse.
`Feat(WP-1): Add Thing.` passes the hook but **fails** the PR-title check, which
enforces lowercase-start and no-trailing-period. Write to the stricter rule.

**Body** — required for anything beyond a trivial fix. Wrap at ~76 columns. Say
what changed and why; name the setting, route, file or service. Do not list
files — the diff does that.

Do not open the body with a `Word: value` line; the Conventional Commits parser
reads it as a footer.

Do not add a `Co-Authored-By:` trailer or any other AI-attribution footer —
the user does not want this in commits or PRs.

## 4. Companion artifacts

- **Model change** → an Alembic migration in the same PR. `just db-revision
  "msg"` runs `alembic revision --autogenerate`, which needs a live database
  (`just infra && just db-upgrade` first). Read the generated file before
  committing — autogenerate guesses.
- **Backend endpoint or schema change** → `just api-sync`, commit the
  regenerated `frontend/generated/api/`. Enforced by CI.
- **New setting** → `app/core/config.py` **and** `.env.example`. Enforced by
  `backend/tests/unit/test_env_example_completeness.py`.
- **A non-obvious choice** → the reasoning goes in the docstring or comment at
  the code site it binds, and, where a future edit could violate it silently, a
  test that fails when it does. A convention spanning a class of files goes in
  `.claude/rules/`; a machine-catchable mistake goes in a hook. What the change
  is *for* goes in the PR description, which squash-merge makes the commit body
  on `main`.

## 5. Commit

Stage deliberately — `git add <paths>`, not `git add -A`.

**Hooks run via prek and can block the commit before any check runs.** If
`.pre-commit-config.yaml` itself is modified but unstaged, `git commit` aborts
with `Configuration file '.pre-commit-config.yaml' is not staged`. Stage it (or
stash the change) and retry.

These hooks **rewrite files**: `ruff-check --fix`, `ruff-format`,
`end-of-file-fixer`, `trailing-whitespace`, and `api-schema-sync-check` (which
runs `scripts/generate-api-types.sh` and writes `frontend/generated/api/`). When
one of them changes something, re-stage the affected paths and retry **once**.

If it fails a second time, or fails `conventional-pre-commit` (fix the message,
don't retry), stop and report.

Never pass `--no-verify`. Never amend or force-push an already-pushed commit
unless asked.

## 6. When not to act

- Never commit `.env`, or anything matched by `.gitignore`.
- Do not hand-write `CHANGELOG.md` entries as part of a routine commit — it is
  curated separately from `just changelog` drafts.
- One commit. If the tree holds two unrelated changes, say so and ask which to
  commit rather than bundling them.
- Do not commit at all unless asked to.
