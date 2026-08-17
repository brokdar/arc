# Feature plan template

The planner fills this in and writes it to `<slug>-plan.md` in the repository root. A plan is a
local working note and is **never committed**: it never ships, and the reasoning in it does not stay
there — every decision names the docstring or test it lands in.

**The parser reads the marked lines verbatim.** Everything else is prose for a human and for the
developer agent. Trim prose freely; never drop a marked line.

The parser is `scripts/parse-plan.mjs`, and it is strict on purpose: a missing `> **Branch**:`,
`> **Depends**:`, `> **Needs Docker**:`, `**Why this PR**:`, `**Delivers**:`, `**Reuses**:` or
`**Acceptance**` block makes it exit 2 and name the PR and the line, before `implement-plan` spawns a
single agent. Check a new plan with `node scripts/parse-plan.mjs <slug>-plan.md` — it is instant, and
it is the cheapest moment to find a defect. `> **Source**:` is read too, and becomes the push agent's
hint for the issue a PR closes.

---

````markdown
# <Feature name>

> **Source**: <spec path, issue, or "operator brief">
> **Base**: main

## Why

<The problem, in the operator's terms. What is wrong today, who it hurts, and what specifically
changes when this is done. A developer that understands this makes different — better — choices on
the hundred things this plan does not say. Do not write a summary of the solution here; write the
problem the solution is for.>

<Two or three paragraphs. Name the concrete failure being fixed: the file that has to be read
out-of-band, the decision that is made outside the system that owns it, the number that cannot be
queried. If there is a deadline or a clock that makes this urgent, say so.>

## Done means

<A short list of the conditions under which this feature is finished — the operator's exit criteria,
not test names. These are what the feature acceptance criteria at the bottom are derived from.>

## What already exists

<Grounding, so the PRs extend the codebase instead of growing a parallel one.>

| Existing thing | Where | Why it matters here |
| --- | --- | --- |
| <thing> | `path` | <what it teaches or constrains> |

## Open questions

<Anything whose answer changes the work materially, marked **(confirm)**. Resolved with the operator
BEFORE any PR is built — `implement-plan` refuses to start while one remains. Delete if none.>

---

## Pull requests

### feat(wellness): the daily series, on every surface

> **Branch**: `feat/wellness-daily-series`
> **Depends**: —
> **Owns**: `app/domain/wellness.py`, `backend/alembic/versions/0012_*`
> **Needs Docker**: yes — migration chain, so `just test-int` before push
> **Triggers**: `just api-sync`

**Why this PR**: <how this piece serves the feature's Why. What the athlete or the coach can do
after it merges that they could not before. One paragraph, concrete.>

**Delivers**: <the shippable capability, across every surface it belongs on — never a layer>

**Reuses**: <the existing components, helpers and patterns this extends, by path. A PR that creates
a new file names the existing code that was checked and why extending it does not work.>

**Decisions landing in code**

| Decision | Displaces | Lands in |
| --- | --- | --- |
| <what was chosen> | <the alternative> | `app/domain/wellness.py` `WellnessDay` docstring + `test_wellness.py::test_one_row_per_day` |

**Acceptance**

- [ ] **AC-1** Given a wellness day with no reading, when `GET /wellness/days/{date}` is called, the
      response has `hrv_ms: null` and `status: "not_recorded"` — never `0` and never a 404.
      — *unit*, `backend/tests/unit/test_wellness_api.py`
      - Edge: the date is in the future
      - Edge: the date precedes the earliest recorded day
- [ ] **AC-2** An agent write stores `source="agent"`; no request path exists by which an agent write
      stores `source="athlete"`. Asserted on the stored row, not on the response.
      — *unit*, `backend/tests/unit/test_wellness_api.py`
      - Edge: agent write to a day the athlete already wrote
- [ ] **AC-3** `wellness_days.confounders` round-trips a multi-entry list unchanged through Postgres
      JSONB. — *integration*, `backend/tests/integration/test_wellness_persistence.py`
      - Edge: empty list, and a list containing a free-text entry with a quote

### feat(wellness): baselines that state their own maturity

> **Branch**: `feat/wellness-baselines`
> **Depends**: `feat/wellness-daily-series`
> **Owns**: —
> **Needs Docker**: no
> **Triggers**: `just api-sync`

<...>

---

## Concurrency map

<Which PRs can run at once — derived from `Depends`, shown here for a human — and, for each such
group, every file two of them could both touch.>

**Runs together**: `feat/wellness-daily-series` ∥ `feat/strength-per-side-sets`
**Then**: `feat/wellness-baselines` (needs the first merged)

| Shared file | Owner in this group | Everyone else |
| --- | --- | --- |
| `backend/alembic/versions/` | `feat/wellness-daily-series` (`0012`) | no migration in this group |
| `frontend/generated/api/` | — | re-run `just api-sync` after rebase; never hand-merge |
| `frontend/tests/mocks/generated-*.ts` | — | re-run the emitting recipe; never hand-edit |
| `app/mcp/tools.py` | — | append only; do not reorder existing tools |
| `CHANGELOG.md` | nobody | curated by hand once the feature lands |

---

## Feature acceptance

<Criteria no single PR can satisfy — true only once the PRs are merged. Verified against `main` at
the end. Same AC-n numbering, continuing from the per-PR criteria.>

- [ ] **AC-20** `get_coaching_context` returns the wellness block alongside the session block in one
      call, and the block is absent (not empty) when no day has been recorded.
      — *verified against `main`*
````

---

## Rules the planner must satisfy

**Every plan opens with Why.** The developer agent receives it verbatim. A plan that only says what
to build produces a developer that guesses at the intent behind every ambiguity — which is where
features go subtly wrong. Name the actual problem: the unqueryable file, the decision made outside
the system, the number nobody can trend.

**A PR is one shippable capability across every surface it touches** — never a layer. A PR that adds
a column and stops is not independently valuable; a PR that adds a capability to the API but not to
MCP violates the repo's own standing rule. If the diff would exceed what fits in one sitting of
review, split by capability.

**The heading is the PR title, and the title is the identity.** It must parse as
`<type>(<scope>): <subject>`, start lowercase, and carry no trailing period — `pr-title` is a
required status check, and squash-merge makes this the commit subject on `main`. It must describe
what the change *does*. **Never `WP-1`, `PR 1`, `Phase 2`, `Step 3` or any positional label**, in a
title or a branch name; the executor refuses the plan outright if one appears.

**`Depends:` lists branch names, and it is the only ordering you write.** Concurrency is derived
from it — do not number groups by hand, because a hand-written number and a `Depends:` line
eventually disagree and the number wins silently. A dependency means "needs that PR **merged**".

**One migration per concurrent group.** Two branches cut from the same head both writing
`alembic/versions/` produce two Alembic heads and a broken chain. The executor verifies this and
refuses.

**`Needs Docker:` marks what must run alone.** `test-int`, `smoke` and `up` bind fixed host ports and
`test-int` reuses one compose project name across every checkout. Set it for any PR touching
migrations, dialect-specific SQL, or full-stack wiring.

**Acceptance criteria are unambiguous and testable.** Each states a specific observable claim — the
input, the action, and the exact expected result — so it can be read only one way. **Banned**:
"handles", "supports", "works correctly", "properly", "as expected", "gracefully". Say what the
value *is*, on which artifact it is asserted (the stored row, the response body, the rendered text),
and what the wrong answer would look like. Each names its **level** and its **test file**, at the
cheapest layer that catches the bug — anything about JSONB, arrays, upserts, constraints or the
migration chain is *integration*, not an in-memory-SQLite unit test.

**Every AC carries its edge cases.** Under each AC, list the boundary, absence and error cases its
tests must also cover — bound to the criterion they stress, so they cannot float off into a separate
list and get skipped. The reviewer checks them per-AC. Think about: empty, zero, null, one, the
boundary and one past it, duplicate, out of order, the future date, the concurrent write, the value
that is legal for the type but illegal for the domain.

**Every non-obvious decision names where it lands in code.** The plan is scaffolding and gets
deleted; the reasoning survives only if it goes to the docstring, comment or failing test it
governs. A decision with no landing site is either obvious enough to omit, or not yet decided.

**No status markers, no progress tables, no dependency diagrams.** `gh pr list` and `git log` are
the truth. The AC checkboxes are for the reviewer's report, not for tracking.
