---
name: diagnose
description: >
  Diagnose a hard bug, a flaky test or a performance regression in this
  repository — establish that the observation is about the code at all, build a
  tight red-capable loop before hypothesising, then fix at the cheapest rung of
  the testing ladder that catches it and pin the regression there. Use when
  asked to diagnose or debug something, when a test fails intermittently, or
  when a symptom is reported and the cause is not yet known.
argument-hint: "[symptom, failing test, or issue number]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# Diagnose a bug

A discipline for bugs whose cause is not obvious. Skip a phase only by saying which and why.

The failure this prevents is jumping to a plausible story about the code. Here that failure has a
second, local form: **attributing an environment fault to the change under test, or a real defect to
the environment.** Both have happened, and once they were the same observation (PR #66). So Phase 0
comes before the loop, and it never closes an investigation on its own.

## Redact first

This skill has you show commands and their output. **Replace every secret with `<REDACTED>` before
showing it.** The ones that turn up in this repo's output and files: `AUTH__PASSWORD_HASH` (a bcrypt
hash — identifying), `AUTH__SESSION__SECRET_KEY`, `MCP__API_KEYS` (`label:scope:key`, the key is the
credential), `SECRETS__ENCRYPTION_KEY`, `POSTGRES__PASSWORD`, the session cookie any logged-in curl
carries, and the OAuth tokens in stored connections. Build loops against `$VAR`, never against a
pasted literal, so the credential stays in the environment rather than in the transcript. If the
redacted output is not enough to diagnose the bug, say so and ask.

Health data is the other sensitive class: quote the rows that carry the signal, not the athlete's
history.

## Phase 0 — is the observation about the code?

Cheap, mechanical, and each check has a definite answer. Run the ones that could explain *this*
symptom, then state which you ruled out.

| Symptom | What it may be | The check |
| --- | --- | --- |
| Intermittent failures in time-dependent backend tests; "no ftp anchor is in force" from code that just wrote one | This host's `CLOCK_REALTIME` steps backwards 85–180 ms about twice a minute | `python3 .claude/skills/diagnose/clock-check.py` (exits 1 if it catches a step) |
| A different backend test fails on each run under `pytest -n auto`, passes in isolation | Open issue **#61** — order-dependent unit failures, no fixed offender, mechanism unconfirmed | Re-run the named test with `-n 0`. Passing alone and failing in parallel is #61's pattern |
| A bare `python` / `pytest` / `alembic` imports code you did not edit | `VIRTUAL_ENV` leaks into a worktree from the checkout the VS Code terminal was opened in | `echo $VIRTUAL_ENV` against `git rev-parse --show-toplevel`. Fix by always using `uv run` / `bun run` |
| The e2e or fullstack suite tests a build you did not make; Playwright reports "port is already used" | Docker ports and the compose project name are **not** per-worktree. Playwright once attached to a 22-minute-old dev server from another checkout and reported failures about code nobody had written that day | `bash scripts/docker-lock.sh status`. `playwright.config.ts` now pins `reuseExistingServer: false`, so the port clash is the honest answer — set `E2E_PORT`, do not kill the other tree's server |
| `bun run type-check` names routes or types you never touched | It is `next typegen && tsgo --noEmit`, so diagnostics are only as fresh as `.next/types` | `rm -rf frontend/.next` and re-run before believing it |
| Login fails, or a bcrypt comparison never matches | `.env` parsers and shells expand the `$` in a bcrypt hash | `AUTH__PASSWORD_HASH` must be **single-quoted** in `.env`; `just hash-password` prints it that way |
| Playwright `locator.click` times out at 30 s on an element it *found* | A CPU/IO-starved runner, not a product defect — open issue **#58**, where two runners disagreed deterministically on the same commit | Check what else is running (a 34-minute hung `docker pull` was concurrent with run 1). Locally `retries: 0`; CI retries twice, so local is stricter |
| A PR shows no CI at all, and `scripts/ci-status.mjs` exits 2 (`NO_RUNS`) | Either the Actions budget is exhausted **or** the PR is `CONFLICTING` — GitHub computes no merge commit, so no workflow ever registers. Zero checks is not pending checks; this has been misread as "CI still running" twice | `gh pr view <n> --json mergeable,mergeStateStatus` |

### The Phase-0 rule, which is the hard part

**Naming an environment fault never ends the investigation.** It changes the next question from "what
is wrong with my change" to: *is the code entitled to the assumption the environment broke?*

`AnchorService.current` asked `anchor_as_of(now)`, whose `created_at <= moment` clause is a
reproducibility guard for historical questions. Against "now" it assumed wall time moves forwards.
When the host clock stepped back between an append and the read, the only anchor version there was
got filtered out and every percentage-of-FTP prescription 422'd. The clock was an environment fault
*and* the code was wrong, and "it's just the machine" would have shipped the defect. PR #66 is the
record.

So: re-run the loop with the environment fault corrected or excluded. If the symptom survives, it was
never the environment. If it vanishes, ask the entitlement question before closing — comparing a
freshly written `created_at` against a later `now()`, ordering by wall time, or asserting that two
timestamps taken in sequence increase are all unentitled, and each is a real defect on a host that is
merely honest about it.

And never re-run until green. A suite that fails on a different file each time teaches everyone to
retry, which is precisely the habit that lets a real regression through: a legitimately broken test
then looks the same as a flaky one until someone stops to check. If you hit #61's pattern, add the
sighting to the issue (which command, which worker count, which tests) rather than shrugging.

## Phase 1 — build a feedback loop

**This is the skill.** Everything after it is mechanical. With a tight pass/fail signal that goes red
on *this* bug, bisection, hypothesis-testing and instrumentation all just consume it; without one, no
amount of reading code will save you. Spend disproportionate effort here: an hour spent getting the
loop down to two seconds is repaid by the fortieth run, and there is always a fortieth run.

### Pick the cheapest rung that can go red

`CLAUDE.md` states the ladder as a testing strategy; it is equally the diagnosis strategy, for the
same reason — a loop you will run forty times must cost seconds, not minutes.

| Where the symptom lives | Rung | The loop |
| --- | --- | --- |
| A domain answer, a service outcome, an HTTP status, a response field, an MCP tool's reply | 1 · backend unit (in-memory SQLite) | `cd backend && uv run pytest tests/unit/test_x.py::test_y -n 0 -q` |
| Pure domain logic whose invariant is stated better than its outputs are enumerated | 7 · hypothesis property test | a property in `tests/unit/test_domain_*.py` — see `test_domain_zones.py` |
| JSONB, arrays, upserts, named constraints, or the migration chain | 2 · backend integration (real Postgres) | `bash scripts/run-integration-tests.sh -k <expr>` — it forwards arguments to pytest, where `just test-int` runs the lot. Docker: `bash scripts/docker-lock.sh acquire diagnose` first |
| A component rendering the wrong number, dropping a field, or mishandling a response | 3 · frontend unit (Vitest + MSW) | `cd frontend && bun run test components/wellness/wellness-view.test.tsx` |
| A user flow through the built UI | 4 · Playwright e2e, no backend | `cd frontend && bun run test:e2e e2e/x.spec.ts` |
| CORS, env baking, Docker networking, migrations-on-boot, login through the real UI | 5 · fullstack smoke | `E2E_PASSWORD=<the .env password> just smoke` — Docker, take the lock |
| The contract between the two halves | — | `just api-check` (regenerates `frontend/generated/api/` and diffs it) |
| Schema-valid input the API refuses | 6 · Schemathesis | reproduce per the recipe in `CLAUDE.md`, from `backend/` so `schemathesis.toml` is picked up |

Other loops, when no test seam reaches it: curl against `just dev-api` (log in first, keep the cookie
in a variable); `cd backend && uv run python -c ...` over a domain function with a fixture; replaying
a stored original through the parser (`data/originals/` is read-only — never move a file there);
`bash scripts/…` for anything already scripted. A throwaway harness goes in the scratchpad, not in
`backend/` or `frontend/`.

### Loops that lie in this repo

- **The `client` fixture is logged in; `anon_client` is not.** A loop that 401s where you expected a
  404 is using the wrong one.
- **`filterwarnings = ["error"]`** in `backend/pyproject.toml`: a new warning surfaces as a test
  failure with an unrelated-looking traceback. Read the exception type before hunting the assertion.
- **A frontend fixture can be the liar.** `tests/mocks/generated-*.ts` are emitted from the real
  domain by `just metrics-fixture` / `matching-fixture` / `scoring-fixture` /
  `wellness-trend-fixture`. If a component test disagrees with the app about a number, re-run the
  matching recipe before touching the expected value — a test written against an impossible fixture
  agrees with the fixture instead of the application.
- **A handler must honour its request.** A canned MSW reply cannot fail when the form drops a field,
  so a mutating handler that echoes what it was sent is a redder loop than one that returns a
  constant.
- **`just gate` is not read-only.** It regenerates the API types and writes `frontend/.next/`, so a
  dirty tree after a gate is expected, not evidence.

### Tighten it

Treat the loop as a product. Faster (narrow the scope, `-n 0`, one spec file). Sharper (assert the
user's exact symptom, not "did not crash"). More deterministic (`-n 0` removes xdist ordering;
`MATCHING__TIMEZONE` and `TZ` are already pinned by `backend/tests/conftest.py`,
`vitest.config.mts` and `playwright.config.ts`, so do not re-pin them per test; seed any RNG; prefer
**stamping a row into the future over monkeypatching the clock** — same state, deterministic, and it
is what PR #66's regression test does). A 30-second flaky loop is barely better than none; a
2-second deterministic one turns the rest of this into bookkeeping.

For a non-deterministic bug the goal is not a clean repro but a **higher reproduction rate**. Loop
the trigger 100×, vary the xdist worker count (`-n 2`, `-n 4`, `-n 8`), pin the arrangement with
`--dist loadfile` to get a reproducible ordering, add load. 50 % is debuggable; 1 % is not.

### Completion criterion

Phase 1 is done when you can name **one command you have already run**, shown with its output
(redacted), that is:

- [ ] **Red-capable** — it drives the actual code path and asserts the reported symptom, so it goes
      red now and green when fixed.
- [ ] **Deterministic**, or pinned at a high enough reproduction rate to debug against.
- [ ] **Fast** — seconds. If the only loop you have is rung 4 or 5, spend more effort finding a lower
      one before accepting it.
- [ ] **Agent-runnable** unattended. If a human genuinely must click, script the prompts so the loop
      is still structured and the captured answers come back to you.

If you cannot build one, **stop and say so.** List what you tried and ask for the environment that
reproduces it, a redacted artifact, or permission to instrument. Do not proceed to hypothesise.

If you catch yourself reading code to build a theory before this command exists — **stop. Jumping
straight to a hypothesis is the exact failure this skill prevents.**

## Phase 2 — reproduce and minimise

Run the loop; watch it go red. Confirm it produces the failure **the report described**, not a
different one nearby — wrong bug, wrong fix. Capture the exact symptom (message, wrong value, timing)
so Phase 5 can prove the fix addressed it.

Then shrink to the smallest scenario still red. Cut inputs, callers, config, rows and steps **one at
a time**, re-running after each cut. Done when every remaining element is load-bearing: remove any
one and it goes green. A minimal repro shrinks the hypothesis space and becomes the regression test.

Do not proceed until you have both reproduced and minimised.

## Phase 3 — hypothesise

Generate **3–5 ranked hypotheses before testing any of them**; single-hypothesis generation anchors
on the first plausible idea. Each must be falsifiable — state its prediction:

> If `<X>` is the cause, then `<changing Y>` makes the bug disappear / `<changing Z>` makes it worse.

No prediction means it is a vibe: sharpen it or discard it. Layering is a productive source of
candidates here — a value can be wrong in the domain, lost in a repository, reshaped by a service,
mangled by a schema, stale in a generated type, or misrendered by a component, and each of those is
a different falsifiable claim with a different rung to test it at.

**Show the ranked list before instrumenting.** The operator often re-ranks it instantly ("that
shipped yesterday") or has already ruled one out. Do not block on it — proceed with your ranking if
nobody answers.

## Phase 4 — instrument

Each probe maps to a specific prediction. **Change one variable at a time.** One breakpoint or one
`uv run python -c` inspection beats ten logs; targeted logs at the boundary that *distinguishes* two
hypotheses beat logs everywhere. Never "log everything and grep".

**Tag every debug log with a unique prefix** — `[DEBUG-a4f2]` — so cleanup is one grep. Untagged
logs survive; tagged ones die.

Two arc-specific constraints on where a probe may go:

- **`app/domain/` is pure.** Adding `structlog` (or anything else on the forbidden list) to a domain
  module is an import-linter failure, not a review comment — `uv run lint-imports` fails, and so does
  `just gate`. Probe a domain value from the calling service, or with a bare `print()` you delete.
- **Adapters are thin by contract.** If the only place a probe fits is inside a route or an MCP tool,
  that is itself a finding: logic has leaked out of `app/services/`.

**Performance branch.** For a regression, logs are usually the wrong instrument. Establish a baseline
measurement first — a timing harness, a profile, `EXPLAIN` on the query, the SQLAlchemy echo — then
bisect against it. Measure first, fix second.

## Phase 5 — fix, at the rung that catches it

Write the regression test **before the fix**, at the **cheapest rung that can actually catch this
bug** — the same rule that governs new tests here. Cheapest means lowest cost, not lowest rung: a
dialect bug pinned in a SQLite unit test is false confidence, and so is a wiring bug pinned in a unit
test that mocks the wiring.

| The bug was | Pin it at |
| --- | --- |
| A domain rule, a service outcome, a status code, a payload shape | rung 1, through HTTP, not internals |
| An invariant over a range of inputs | rung 7, a hypothesis property beside the example |
| Dialect behaviour (JSONB, arrays, upserts, constraints) or migration drift | rung 2 |
| A component's arithmetic, formatting or request handling | rung 3, against a generated or request-honouring fixture |
| A flow that only breaks when several pages compose | rung 4 |
| Wiring — CORS, env baking, container networking, migrations-on-boot | rung 5, and keep that suite under ~5 tests |
| Something Schemathesis found | rung 1 **as well** — pin the case as a unit test; narrow the operation in `backend/schemathesis.toml` only when the refusal is by design, never by excluding a check globally |

**If no rung can hold it, that is the finding.** Say so rather than pinning it somewhere that gives
false confidence, and name what about the architecture prevents it — that is a defect worth its own
issue.

Then: turn the minimised repro into a failing test, watch it fail, apply the fix, watch it pass, and
re-run the Phase 1 loop against the **original un-minimised** scenario. A model change ships its
Alembic migration in the same commit; a backend schema change ships `just api-sync`; a domain change
to metrics, matching, scoring or wellness ships the matching `*-fixture` recipe's output.

## Phase 6 — clean up and record it where it binds

- [ ] The original repro no longer reproduces (re-run the Phase 1 loop, not a smaller one).
- [ ] The regression test passes, or the absence of a rung that could hold it is written down.
- [ ] Every `[DEBUG-…]` probe removed — `grep -rn "DEBUG-" backend/ frontend/` before you believe it.
- [ ] Throwaway harnesses deleted from the tree (the scratchpad may keep them).
- [ ] `just gate` green. Expect it to leave `frontend/.next/` and possibly regenerated API types
      behind; that is the recipe, not your change.

**The reasoning goes where it binds, never in a document that can drift from the code.** This repo
has no ADR directory and no design docs, deliberately. Choose:

- a **docstring or comment at the code site** the decision governs — what was chosen, what it
  displaced, why. `app/core/clock.py` and `AnchorService.current` are the worked examples;
- a **test that fails when the rule is violated again**, where a later edit could break it silently;
- `.claude/rules/<name>.md` with a `paths:` key, when the convention spans a class of files rather
  than one site;
- a **hook** in `.claude/hooks/` or pre-commit, when the mistake is machine-catchable;
- and the **PR description**, which squash-merge makes the commit body on `main`. The `bug` template
  in the `commit-push-pr` skill is `## Root cause` / `## The fix` / `## Impact` / `## Notes` — state
  the hypothesis that turned out correct, and say plainly whether any stored rows are wrong and
  whether anything backfills them.

If the diagnosis ended at an environment fault nobody can fix today, it still ends in writing: add
the sighting to the open issue (#61, #58) with the command, the tree and the worker count, or add a
line to the project memory if it is a standing property of this host.

## Delegating

Use the `Agent` tool when a phase genuinely fans out, and only then:

- **`subagent_type: "Explore"`** to locate where a symptom's code path lives when you do not know —
  it returns the conclusion, not file dumps.
- **A fresh `general-purpose` agent per hypothesis**, all in one message, when three or more
  independent hypotheses each need their own instrumentation run. Give each the loop command and one
  prediction to falsify; they must not edit shared files.

Do not delegate Phase 1. Building the loop is where the judgement is, and it is the one thing a
summary cannot carry back.
