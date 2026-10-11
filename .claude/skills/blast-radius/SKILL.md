---
name: blast-radius
description: >
  Find what a change could break somewhere else before it ships, beyond the
  diff, and prove the one fact it's safe because of by running real code
  instead of writing it up. Use for "blast radius of X", "what could this
  break", or reviewing a diff you don't trust — including one that merged
  clean, which is not by itself evidence of anything.
argument-hint: "[what changed — a diff, branch, PR number, or file/symbol]"
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Write, Agent
---

# Blast radius

Find what a change breaks somewhere else, before it ships. Use for "blast
radius of X", "what could this break", or reviewing a diff you don't trust
yet.

Companion to `code-review`: that skill finds bugs *in* the diff. This one
finds what the diff breaks *outside itself* — the other adapter, the
generated contract, the migration, the fixture nobody regenerated.

Listing the callers is not the job. Grep does that in a second. The job is
the breakage grep won't show you: a rename that only one caller followed, a
percentage that's `88` where everything else expects `0.88`, a service
method that only one of arc's two adapters learned to call.

## Don't trust your own writeup

A blast-radius writeup that sounds right is worthless. It reads as convincing
whether or not it's true, and that is the trap you are walking into. So don't
hand back the writeup. Find the one or two facts the whole thing depends on
and prove them by running code. Words are where you start, not what you ship.

**A clean merge is not evidence. "No conflict" is rung 1 at best.** On 18 Aug
2026 an auto-merge went through textually clean: `formatUtcStamp` had been
renamed to `formatAthleteStamp` in `frontend/lib/format.ts`, and
`frontend/components/settings/dropbox-panel.tsx` still called the old name.
Git had no conflict to raise — the two edits touched different lines. Only
`tsgo` (`bun run type-check`, also inside `just gate` and `just build`)
caught it, at the next type-check. Separately, `main` moved under in-flight
PRs three times in one day. Two lessons, both load-bearing here: a merge
succeeding says nothing about whether the result compiles, and a blast-radius
pass diffs against **current** `origin/main`, not the branch's original fork
point — the base has probably moved since you branched.

### How sure are you

For each fact the change's safety depends on, get it as far down this list as
is cheap, and say where it stopped.

1. You said so. Worthless on its own.
2. You pointed at the line — a real `file:line`, the library's own source, or
   the contract that names it explicitly (an import-linter rule in
   `backend/pyproject.toml`, a line in `.claude/rules/`).
3. You showed the bad case can't happen. You walked the failure step by step
   and it doesn't reach.
4. You ran it. A script, a targeted test (`uv run pytest -k`, `bun run test
   -- <path>`), or a `uv run python -c` one-liner that calls the real code
   and fails loud if you're wrong.
5. You reproduced it in the running app (`just up`, `just smoke`).

Any safety fact you can't get to step 4, say so out loud. Don't write it up
as settled. Step 4 is usually one small script or one targeted test run
against the real module — not a new integration suite.

## Steps

1. **Read the change.** The diff, the symbols it adds, changes and deletes,
   and what it now does differently — including the part the diff doesn't
   spell out. `git diff origin/main...HEAD` (or `gh pr diff <n>` for a PR);
   `git log origin/main..HEAD --format='=== %s%n%b'` for the commit bodies —
   they're the decision record, not `--oneline`. Diff against **current**
   `origin/main`, fetched fresh — see the 18 Aug incident above.

2. **Find the one fact it's safe because of.** Most changes that look scary
   are safe because of a single fact. Find that fact — spend your time here,
   not on a long list of maybes. Check the seams this repo actually has:

   - **The committed API contract.** Touches `backend/app/api/schemas/` or a
     route? `frontend/generated/api/` is downstream and committed, never
     hand-edited — a schema change that isn't followed by `just api-sync`
     compiles on the backend and breaks every typed caller on the frontend.
     CI fails on drift; so can you, locally, before it does.
   - **Layering.** Adds or moves an import? `app/domain/` is pure — no
     SQLAlchemy, FastAPI, Starlette, httpx, structlog, pydantic-settings, or
     any outer layer, per the forbidden-imports contract in
     `backend/pyproject.toml` — and imports point inward only
     (`api | mcp` → `ingest` → `services` → `connectors` → `persistence` →
     `domain`), per the same file's layers contract. `app.api` and `app.mcp`
     may not import each other either (the independence contract).
     `uv run lint-imports` is the ground truth; `test_domain_purity_contract`
     is the second-order check that the deny-list itself hasn't gone stale
     against `[project].dependencies`.
   - **Two adapters, one capability.** Changes or adds a service method?
     `app/api/` and `app/mcp/` are separate adapters over the same
     `app/services/` layer. A capability that lands on only one of them is a
     named failure mode — arc-reviewer checks for exactly this. If the
     change is meant to be usable from the coaching agent, confirm
     `app/mcp/tools.py` calls it, not just a route.
   - **Migrations.** Touches `app/persistence/`? A model change ships an
     Alembic migration in the same PR. Nothing in `just check` catches a
     missing one — lint, typecheck and the SQLite-backed unit suite all pass
     — only `alembic check` in `just test-int` does.
     `scripts/check-migration-required.sh` (a pre-push heuristic reading a
     commit range) narrows the obvious case but is not the authority.
   - **Units and ranges.** Touches `app/domain/`, `app/services/`, or
     `frontend/lib/`? `.claude/rules/backend-domain-units.md`: a percentage
     is a fraction (`0.88`, never `88`) everywhere; a range is half-open
     `[start, end)` with no `+ 1`. Both are invisible when broken — the wrong
     value is still a plausible-looking number — so nothing but the
     convention itself catches a slip, and it propagates through every
     consumer down the chain (a chart, a zone band, a coach-facing number).
   - **Nullability.** Touches `backend/app/api/routes/` or
     `backend/app/api/schemas/`? `.claude/rules/api-nullability.md`: an
     optional query parameter is `X | SkipJsonSchema[None]` (optional by
     omission), never `X | None` (which advertises a `null` the query string
     can't actually deliver); an update-payload field is `X | None` only
     where `null` genuinely means "clear this". Schemathesis catches the
     mismatch in CI; a local check is cheaper.
   - **Route shadowing.** Adds a route under an existing collection?
     `.claude/rules/api-collection-facets.md`: a facet (`/workouts/labels`)
     that shares a path prefix with an id route (`/workouts/{id}`) is matched
     by declaration order, and every method the facet doesn't declare falls
     through to the id route and answers 422 where 405 is correct — a
     collision Schemathesis' `unsupported_method` check catches.

3. **Look where grep stops.** Read the source of the library you call and
   its pinned version. Work out when things run — an APScheduler job in
   `app/core/scheduler.py`, a FastAPI dependency, a React effect versus a
   render. Follow what a symbol search misses:
   - a `JSONColumn` (JSONB on Postgres) whose shape lives nowhere but the
     code that writes and reads it;
   - an `enum_column` (non-native enum) where SQLite and Postgres can diverge
     — only `just test-int` runs against the real dialect;
   - a generated frontend fixture (`tests/mocks/generated-metrics.ts`,
     `generated-scoring.ts`, `generated-matching.ts`,
     `generated-wellness-trend.ts`) that mirrors a domain function you just
     changed and is now stale until its `just *-fixture` recipe reruns;
   - code that reads the wall clock directly (`dt.date.today()`, a naive
     `datetime.now()`) instead of going through `app.core.clock` — flagged by
     the `DTZ` ruff rules, but only where ruff can see it;
   - a wire format another process reads: the OpenAPI schema, a stored
     originals file under `data/originals/` that `just rebuild-streams`
     re-parses later.
   Do not rediscover the repo's shape from scratch — the layering, the
   contracts and the testing ladder are already written down in `CLAUDE.md`,
   `.claude/rules/` and `backend/pyproject.toml`; spend your reading budget on
   the change, not on re-deriving what a test already enforces.

4. **Be honest about each risk.** Give it a real chance of happening and a
   real cost if it does. Keep the risks you confirmed; list the ones you
   checked and cleared separately. Cite a real `file:line`; a search that
   finds nothing is still an answer; never invent a caller or an API.

5. **Prove the one fact.** Write a script or run a targeted test against the
   real code, run it, and paste what happened. If you can't prove it cheaply,
   mark it unproven. Don't round up. See the command table below.

6. **For a wide or genuinely uncertain change, parallelize instead of
   guessing alone.** There's no `arena` here. The repo's own equivalent is
   `feature-plan`'s scout pattern: launch one read-only `Agent` call per
   independent question — `subagent_type: Explore` for a pure "does X call
   Y" lookup, `general-purpose` for anything needing judgment — all in a
   single message, all pointed at the same diff, and reconcile disagreements
   yourself. Different agents miss different things; don't trust whichever
   answer lands first. Do not use this for a change small enough that one
   pass already found the load-bearing fact.

## Real proof, by rung

Rung 4 — one command, real code, no Docker:

| Check | Command |
|---|---|
| API contract drift | `just api-check` (or `bash scripts/check-api-schema-sync.sh`) |
| Layering / adapter independence | `cd backend && uv run lint-imports` |
| Domain-purity deny-list is complete | `cd backend && uv run pytest tests/unit/test_domain_purity_contract.py` |
| Migration presence (heuristic) | `bash scripts/check-migration-required.sh` |
| One backend criterion | `cd backend && uv run pytest tests/unit/test_x.py -k name` |
| One frontend criterion | `cd frontend && bun run test -- <path>` |
| Types across the whole app | `cd frontend && bun run type-check` (`tsgo`) |
| A one-off repro | `cd backend && uv run python -c "..."` importing the real module, or a scratch script run with `uv run python <script>` — never a bare `python`, which can pick up the wrong checkout's venv in a worktree |
| Everything `just check` covers, one exit code | `just gate` (adds the migration heuristic on top of `just check`) |

Rung 5 — the running app. **Docker is not per-worktree**: `up`, `infra`,
`smoke` and `test-int` bind fixed host ports and `test-int` reuses one
compose project name across every checkout. Take the lock first:

```bash
bash scripts/docker-lock.sh acquire <label>
# just up | just test-int | E2E_PASSWORD=... just smoke
bash scripts/docker-lock.sh release <label>
```

Skipping the lock is exactly how two runs collided for an hour on 16 Aug
2026 (see the script's own header in `scripts/docker-lock.sh`) — one held it,
told itself it had exclusive use of Docker, and did not.

- `just up` then exercise the change for real through the app.
- `E2E_PASSWORD=... just smoke` for the full-stack wiring tier — CORS, env
  baking, Docker networking, migrations-on-boot, login through the real UI.
  It is the only tier that proves the reverse proxy, the built frontend and
  the live backend agree; nothing lower does.
- `just test-int` is the only place `alembic check` runs — the actual
  authority on migration drift, not the pre-push heuristic.

Note what `just gate` does *not* prove: on a branch's first commit its
migration heuristic has no commit range yet and exits 0 without looking at
anything, so a green gate that early says nothing about model/migration
drift.

## What to hand back

- **What it does.** What changed, including the part that isn't obvious.
- **The one fact it's safe because of.** State it, say which rung you got it
  to, and show the proof. If you couldn't prove it, write unproven.
- **Risks.** Only the real ones. Each names how it breaks, the `file:line`,
  how likely and how bad, and how to check. Paste the proof for the ones that
  matter.
- **Cleared.** What you checked and why it's fine.
- **Before you merge.** The cheapest test or repro that catches the real bug
  — name the layer per the testing ladder in `CLAUDE.md` (unit before
  integration, integration before smoke) — including the script you wrote.

**Reply:** the writeup above, with the one safety fact either proven or
marked unproven.
