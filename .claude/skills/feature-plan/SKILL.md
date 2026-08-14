---
name: feature-plan
description: >
  Turn a feature brief into a plan for this repository — the problem it solves,
  the pull requests that deliver it, their dependencies and concurrency map, and
  acceptance criteria precise enough to build against. Use when asked to plan a
  feature, break work into PRs, or design how to build something here before
  writing it. Produces the plan `implement-plan` executes.
argument-hint: "[brief, or path to a spec]"
allowed-tools: Read, Write, Glob, Grep, Bash(git log:*), Bash(git diff:*), Bash(gh pr list:*), Bash(rg:*), Task
---

# Plan a feature

The unit of a plan here is a **pull request**, because `main` is squash-only: a branch with twelve
tidy commits collapses into one commit on merge, so per-commit granularity is thrown away and the
reviewable unit is the PR. Splitting a feature into PRs is what turns a big-bang diff into something
a human can actually read.

**Input**: `$ARGUMENTS` — a brief or a spec path. If nothing was given, ask for it.

**Output**: `<slug>-plan.md` in the repository root, following [plan-template.md](plan-template.md). Read that file
before writing; it carries the format the executor parses and the rules the plan must satisfy.

## Do not rediscover the repo

The gates, the layering, the collision points and the testing ladder are stated in `CLAUDE.md`,
`.claude/rules/` and the `justfile`, and each is enforced by a test, a hook or an import-linter
contract. Take them as facts:

| | |
| --- | --- |
| Local gate per PR | `just check` — ruff · pyrefly · import-linter · backend+frontend unit tests · production build · api-contract drift |
| Needs Docker (runs alone) | `just test-int` (the only place `alembic check` runs) · `just smoke` · `just up` |
| CI adds beyond `just check` | integration on real Postgres · Playwright e2e · fullstack smoke · schemathesis · `pr-title` |
| Build steps an edit triggers | backend schema → `just api-sync` · domain metrics/scoring/matching → the matching `*-fixture` recipe · model change → an Alembic migration in the same PR · new setting → `app/core/config.py` **and** `.env.example` |
| Never touched by a PR | `CHANGELOG.md` — hand-curated once the feature lands |

Your exploration budget goes on the **feature**, not on the repo's shape.

## How to run it

**1 — Establish the Why before anything else.** What is wrong today, concretely: the file read
out-of-band, the decision made outside the system that owns it, the number nobody can trend. Who it
hurts and what changes when it is done. If the brief does not make this clear, ask — this is the one
thing you cannot infer from the codebase, and it is what the developer agent reasons from when the
plan runs out of instructions. A plan that only says *what* to build produces guesses at every
ambiguity.

**2 — Scout narrowly.** Identify which surfaces the feature touches (domain / persistence / services
/ api / mcp / frontend / ingest / scheduler). Spawn **one read-only scout per touched surface, all in
a single message** — and no scouts at all for a feature touching one surface; read it yourself. One
round; there is no standing room and no cross-agent debate, because the conventions that would be
debated are already written down.

Each scout returns four things, terse:

- where the change lands (files, and the existing seams it hangs off);
- what it **reuses** — the components, helpers and patterns to extend. A proposal to create a new
  file must name the existing code checked and why extending it does not work;
- what it **collides with** — files another PR would also touch (migrations, generated API types,
  generated fixtures, `app/mcp/tools.py`, route registration, `config.py`/`.env.example`);
- its proposed PRs, each one shippable capability, with what each depends on.

**3 — Resolve ambiguity with the operator, do not arbitrate it.** If two readings of the brief lead
to materially different designs, stop and ask. Below that bar you decide and record it in the PR's
decisions table. Anything needing an answer before code runs goes in **Open questions** marked
**(confirm)** — `implement-plan` refuses to start while one remains.

**4 — Cut the PRs.** Merge scout proposals into capabilities, not layers. Title each as a
Conventional Commit that says what it does — never a positional label. Give each a branch, and set
`Depends:` to the branches it needs **merged**. Do not number groups: concurrency is derived from
`Depends`. Then check the two hard constraints:

- **at most one PR per concurrent group owns a migration** — parallel branches off the same head
  produce two Alembic heads and a broken chain;
- any PR touching migrations, dialect-specific SQL or full-stack wiring gets **`Needs Docker: yes`**,
  so it runs alone rather than racing another worktree for the fixed compose ports.

**5 — Write the concurrency map.** For each group that runs together, name every file two of its PRs
could both touch, who owns it, and what everyone else does instead. This is the one artifact that
makes parallel worktrees safe rather than a merge nightmare. A generated file gets "re-run the recipe
after rebase, never hand-merge" — not an owner.

**6 — Write the acceptance criteria, and this is where the plan earns its keep.** Stable `AC-n`
numbering across the whole plan. Each AC states input, action and the exact expected result, on a
named artifact (the stored row, the response body, the rendered text), so it can be read only one
way. If you have written "handles", "supports", "works correctly", "properly" or "gracefully", the
criterion is not yet written — say what the value *is* and what the wrong answer would look like.
Name the level and the test file, at the cheapest layer that catches the bug.

Then, under each AC, derive its **edge cases** — the boundary, absence and error cases its tests must
also cover. Work through: empty, zero, null, one, the boundary and one past it, duplicate, out of
order, a future date, a concurrent write, a value legal for the type but illegal for the domain. The
developer writes these as failing tests too; the reviewer checks them per-AC. Criteria that no single
PR can satisfy go under **Feature acceptance** and are verified against `main` at the end.

**7 — Write the plan yourself.** Do not delegate it. Then run four checks and fix any violation:

- every criterion in "Done means" maps to an AC, and every AC to a PR or to feature acceptance;
- every new file a PR creates has a recorded reason why extending existing code does not work;
- every title parses as `<type>(<scope>): <subject>`, starts lowercase, has no trailing period, and
  contains no positional label (`WP-1`, `PR 1`, `Phase 2`, `Step 3`) — in the title or the branch;
- every AC names a level, a test file, and at least one edge case, or says why it has none.

**8 — Report.** Print the path, the concurrency shape by branch name, and the three-to-five decisions
the operator never explicitly made and would most want to veto. Then stop — do not start building.

## Right-sizing

A one-PR feature gets a one-PR plan: Why, title, branch, delivers, reuses, acceptance with edge
cases. No concurrency map, no scouts. The dependency machinery appears only when there is
parallelism to win. Over-planning a small change is as much a failure as under-planning a large one —
but the Why and the edge cases are never the thing you trim.
