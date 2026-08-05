# MVP Build Plan — "The Coached Week"

**Audience: Claude Opus 5, executing this plan end-to-end in an agentic coding session.** The plan is self-contained: all product semantics needed for the MVP are stated inline. Where a decision is genuinely open, it is marked `DECIDE:` with a default — take the default unless the operator says otherwise. Do not invent features beyond this plan; the spec deliberately defers most of the product to later phases.

---

## 0. Product context (read first)

Single-athlete, self-hosted training application (cycling + strength/core). The MVP validates one loop:

> The athlete (or coaching agent) plans a week of sessions, each with a recorded **intent** (purpose, free-text intent, coach notes, machine-checkable success criteria). Completed activities are ingested from FIT files, **matched** to planned sessions, **scored** against the frozen intent, given a **verdict** with **reasons**, and the agent reviews results and makes plan **proposals** through a guarded MCP surface.

Non-negotiable invariants (these shape the schema and cannot be retrofitted):

1. **Versioning doctrine.** Every derived artefact (score, metric, alignment) carries an `as_of` stamp and a version chain. Originals are immutable. Recomputation creates a new version; it never overwrites. Athlete testimony (verdicts, reasons) is never auto-rewritten — if a recomputation changes a score under a confirmed verdict, the verdict is flagged `contested`, not changed.
2. **Provenance everywhere.** Anchors carry provenance (`tested | estimated | assumed | athlete_reported`), protocol, effective date, confidence interval. Derived values record which anchor version they used. Streams/channels record their source.
3. **Append-only anchors.** Anchor history is never edited, only appended. Zones derive from a declared zone model + a specific anchor version.
4. **Prescriptions freeze at planning time.** A planned session pins the anchor version its targets derive from. Scoring uses the frozen prescription. Post-execution edits to intent create a new intent version, force a rescore, and are flagged `edited_post_hoc`.
5. **Matching is a proposal, never a silent commitment.** See §7.
6. **Proposal lifecycle.** Every agent proposal has rationale, diff, expiry, default-on-expiry (= committed plan stands). One open proposal per plan entity. A recorded activity contradicting a pending proposal auto-resolves it (logged).
7. **Deterministic vs. interpretive.** Everything computed must work with no LLM available. Agent-written text is attributed, distinguishable, and stored separately from computed findings.
8. **Original files are kept forever, unmodified, and sufficient to rebuild every derived artefact.**

Out of MVP scope (do not build): vendor API adapters (Wahoo/Zwift/Strava/HealthKit), multi-recording reconciliation, power curves/CP models/PMC, availability model, constraint engine, scheduled inference, weather, routes/maps, wellness, PWA/offline, notifications, beginner mode. The schema may reserve fields for these (noted where relevant) but no behavior.

---

## 1. Stack (as built in WP-0, verified 2026-08-04)

Pins are floors (`>=`) in `backend/pyproject.toml` and `frontend/package.json`; the exact resolution lives in `backend/uv.lock` and `frontend/bun.lock`. **The lockfile is the pin** — the versions below are what is installed today, and `uv sync --frozen` / `bun install --frozen-lockfile` reproduce them exactly.

Backend: Python 3.14 (`backend/.python-version` pins the *minor*, so the patch floats: 3.14.4 in the devcontainer, 3.14.6 in the `python:3.14-alpine` runtime image — nothing depends on a specific patch), FastAPI 0.141.1 (`fastapi[standard]`), Pydantic 2.13.4 + pydantic-settings 2.14.2, SQLAlchemy 2.0.51 async (2.1-clean style: typed `Mapped[]` only) + Alembic 1.18.5 over asyncpg 0.31, structlog 26.1 (structured logging), APScheduler 3.11.3 (in-process, started by the API lifespan), FastMCP 3.4.5, bcrypt 5.0 + itsdangerous 2.2 (single-user session cookie), uv (project + toolchain manager), ruff 0.16.1 (lint + format, line length 88), pyrefly 1.2 (type checking), import-linter 2.13 (architecture boundaries), pytest 9.1.1 + pytest-asyncio + pytest-xdist + pytest-cov + pytest-mock + asgi-lifespan, httpx 0.28.1, aiosqlite (in-memory unit-test database).

Not installed yet — each arrives with the work package that first needs it: `hypothesis` (**WP-1**, with the first property-tested pure domain code), polars + pyarrow (WP-5), garmin-fit-sdk / fitdecode / gpxpy / tcxreader (WP-4), the Anthropic SDK (WP-8). Add them then, not now.

Frontend: Next.js 16.2.12 (App Router, Turbopack), React 19.2.8, TypeScript 5.9 type-checked by `tsgo` (`@typescript/native-preview` 7.0.0-dev — the native TS 7 compiler; see D2), Tailwind 4, shadcn 4.16 over Base UI (`@base-ui/react` 1.6 — components take `render={...}`, **not** Radix `asChild`), TanStack Query 5.101, the typed API contract chain `openapi-typescript` 7.13 → `openapi-fetch` 0.17 + `openapi-react-query` 0.5 (+ `openapi-msw` 2.0 for typed test handlers), zod 4 + `@t3-oss/env-nextjs` (validated build-time env), bun (package manager and runner), Biome 2.5 (lint + format), Vitest 4.1 + MSW 2.15, Playwright 1.62. uPlot and ECharts arrive with WP-5.

Infra: Docker Compose (`docker-compose.yml`: `db`, `data-init`, `api`, `mcp`, `frontend`, `caddy`), PostgreSQL 18 (`postgres:18-alpine`), Caddy 2.11 (`caddy:2.11-alpine`) as the one public origin. No Redis/Valkey, no Celery, no separate worker process — scheduling is in-process APScheduler.

Conventions: ruff format + lint (line length 88), `uv run pyrefly check` clean, `from __future__` never needed (3.14), all functions typed, the domain layer has no I/O or framework imports — enforced mechanically by import-linter, not by review. Frontend: strict TS, Biome, no `any`. Where these differ from this plan's original preferences (pyright strict, line length 100, ESLint, `Makefile`, `/healthz`, `APP_`-prefixed env), the repo's choice and its reasoning are recorded in `docs/decisions.md` (D2, D3, D7).

---

## 2. Repository layout

Two standalone projects — one uv project, one bun project — rather than a uv/pnpm workspace monorepo. The layer boundaries this plan asks for are kept, as modules inside `backend/app/` with the dependency direction enforced by import-linter. See `docs/decisions.md` D1.

```
arc/
├── backend/                # standalone uv project: the API *and* the MCP server
│   ├── app/
│   │   ├── main.py         # app factory + lifespan (scheduler, data dirs, middleware)
│   │   ├── core/           # cross-cutting, any layer may use: config, logging,
│   │   │                   # exceptions, scheduler
│   │   ├── domain/         # PURE Python: entities, value objects, scoring, matching,
│   │   │                   # metrics, workout model, zone math. No SQLAlchemy, no
│   │   │                   # frameworks, no I/O. (Filled in from WP-1.)
│   │   ├── persistence/    # db.py (engine/session), ORM models, repositories,
│   │   │                   # alembic/ migrations (async env)
│   │   ├── services/       # use-cases — the layer api/ and mcp/ both consume
│   │   ├── ingest/         # file watching, FIT/TCX/GPX parsing, quarantine, parquet
│   │   ├── api/            # HTTP adapter: routes/, schemas/, deps (session guard),
│   │   │                   # pagination, validation
│   │   └── mcp/            # FastMCP server (tools → services); sibling of api/
│   ├── tests/unit/         # in-memory SQLite, no external services
│   ├── tests/integration/  # real Postgres + the migration chain
│   ├── Dockerfile          # one image, two entrypoints (api / mcp)
│   ├── docker-compose.test.yml  # throwaway Postgres for the integration suite
│   └── pyproject.toml      # deps, ruff, pytest, coverage, import-linter contracts
├── frontend/               # standalone bun project: Next.js 16 App Router
│   ├── app/ components/ lib/
│   ├── env.ts              # zod + @t3-oss/env-nextjs: validated build-time env
│   ├── generated/api/      # openapi.json + schema.d.ts — committed, never hand-edited
│   ├── tests/mocks/        # typed MSW handlers (openapi-msw)
│   └── e2e/                # Playwright: UI-only specs + @fullstack specs
├── caddy/Caddyfile         # /api/* + /health → api, /mcp* → mcp, everything else → frontend
├── scripts/                # bootstrap-env, API type generation + drift check,
│                           # integration-test runner, setup-repo (GitHub ruleset
│                           # + merge settings that a template cannot copy)
├── data/                   # runtime tree (gitignored): inbox/, originals/, streams/,
│                           # quarantine/ — bind-mounted into the api container
├── docs/                   # this plan, tech-stack, decisions log, product description
├── .devcontainer/          # uv, bun, just, prek, git-cliff, Playwright browsers
├── .github/workflows/      # path-filtered CI (see WP-0 §9)
├── docker-compose.yml      # db, data-init, api, mcp, frontend, caddy
├── .env.example            # every setting, kept in sync by a backend test
├── .pre-commit-config.yaml # prek hooks: pre-commit, commit-msg, pre-push
├── cliff.toml              # git-cliff config — drafts changelog entries, writes nothing
├── CHANGELOG.md            # hand-curated Keep a Changelog
└── justfile                # init/hash-password, dev-*, infra, up/down,
                            # format/lint/typecheck/test/test-int/e2e,
                            # db-*, api-*, check, smoke, changelog-* — single entry points
```

Dependency rule, enforced by the import-linter contracts in `backend/pyproject.toml` (`uv run lint-imports`, run in CI, `just lint` and the pre-push hook):

- **`app.domain` is pure** — it may not import SQLAlchemy, Alembic, asyncpg, FastAPI, Starlette, httpx, pydantic-settings, APScheduler, structlog, or any other layer (including `app.core`).
- **Layer stack:** `app.api | app.mcp` → `app.ingest` → `app.services` → `app.persistence` → `app.domain`. Imports point inward only.
- **`app.api` and `app.mcp` are independent** — neither imports the other; both consume the same `app.services`. Nothing that matters lives in an adapter.
- **`app.core` is deliberately outside the stack**: config, logging, exceptions and the scheduler are cross-cutting and any layer above the domain may use them.

---

## 3. Work packages

Execute in order. Each WP ends with: tests green, `just check` clean, a short CHANGELOG entry, and a git commit. Do not start a WP with the previous one red.

### WP-0: Scaffold + infrastructure (day 0) — ✅ delivered

Built by adapting an existing, fully verified full-stack template rather than scaffolding a workspace monorepo from scratch (D1). What exists:

1. **Projects.** `backend/` — a standalone uv project on Python 3.14.6 (D4) holding both the API and the MCP server; `frontend/` — a standalone bun project (Next.js 16.2 App Router, TS 5.9 + `tsgo`, Tailwind 4, shadcn over Base UI, Biome, Vitest + MSW, Playwright) (D2).
2. **Layers.** `app/{core,domain,persistence,services,api,mcp,ingest}` with the §2 dependency rule enforced by import-linter contracts in CI, `just lint` and pre-push. `domain/` and `ingest/` are deliberately empty shells — WP-1 and WP-4 fill them.
3. **API.** FastAPI skeleton: `/health` (open, used by every container healthcheck) and the `/api/v1` router mount, settings via pydantic-settings with unprefixed nested `__` keys (D7), structlog structured logging. `items` is a temporary worked example threaded through persistence → services → api, plus its tests and frontend page; **WP-1 deletes it** once real entities land.
4. **Scheduling.** In-process APScheduler started by the API lifespan (`app/core/scheduler.py`) — no Redis, no worker service, no queue (D5).
5. **Auth.** Single-user session cookie (D6): the credential store is one bcrypt hash, `AUTH__PASSWORD_HASH` (there is no user table). `POST /api/v1/auth/login` swaps it for a signed cookie from Starlette's `SessionMiddleware` (`arc_session`, `SameSite=Lax`, 14 days, `Secure` once `AUTH__SESSION__HTTPS_ONLY` is on); `/auth/logout` and `/auth/session` sit beside it, always open. **Everything else under `/api/v1` hangs off a router carrying `Depends(require_session)`** — new routers are protected by default, not by remembering to protect them. Failed logins sleep ~0.3 s; production refuses to boot without `AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY`. The frontend has a `/login` page and an `AuthGuard`.
6. **MCP.** A FastMCP 3 server (`backend/app/mcp/`) run from the same image as the API (`python -m app.mcp.main`, streamable HTTP on :8001, loopback-published, public path is Caddy's `/mcp*`). Every request presents a bearer key from `MCP__API_KEYS` — comma-separated `label:scope:key` entries, scope `read` or `write` — parsed by the framework-free `app/mcp/auth.py` and compared in constant time. The server exits 1 rather than serve an unauthenticated tool surface with no keys. Surface today: one `ping` tool plus an unauthenticated `/health`. WP-8 adds the real tools and per-tool scope checks.
7. **Compose.** `db` (`postgres:18-alpine`, volume at `/var/lib/postgresql`, healthcheck), `data-init` (one-shot: hands the root-owned `./data` bind mount to the api's non-root uid), `api` (migrations on boot, then `fastapi run`), `mcp`, `frontend` (standalone Next build, built with an empty `NEXT_PUBLIC_API_BASE_URL` so the browser calls the API same-origin), `caddy` (`caddy:2.11-alpine`, `/api/*` + `/health` → api, `/mcp*` → mcp, everything else → frontend; `CADDY_SITE_ADDRESS` defaults to `:80` plain HTTP, set a hostname for automatic HTTPS).
8. **Runtime data.** `DATA__ROOT` (default `data`) with `inbox/`, `originals/`, `streams/`, `quarantine/` created on API startup and bind-mounted at `/app/data`. WP-4 ingests from it.
9. **CI (GitHub Actions), path-filtered:** backend lint/typecheck/import-linter/unit tests; backend integration tests against a throwaway Postgres started from `backend/docker-compose.test.yml` by `scripts/run-integration-tests.sh`, including `alembic upgrade head`, `alembic check` and a head→base→head round-trip; frontend lint/typecheck/test/build; Playwright e2e (sharded, reports merged); OpenAPI↔generated-types drift check; Schemathesis fuzzing; a full-stack smoke job (`docker-compose-check.yml`) that validates the compose config, boots the whole stack and runs the `@fullstack` Playwright suite through Caddy, asserting 401s on `/api/v1/*` and `/mcp`; a `pr-title` lint on the PR title (required by the `protect-main` ruleset); zizmor workflow linting; and a tagged release that publishes three images to GHCR — `api`, `mcp` (same Dockerfile, different entrypoint) and `frontend`.
10. **Entry points.** `just init` writes a ready-to-run `.env` (random `POSTGRES__PASSWORD`, `AUTH__SESSION__SECRET_KEY`, both `MCP__API_KEYS`, plus a bcrypt hash of the password you type — or a placeholder plus instructions when there is no TTY, D15); `just up` brings the stack up on http://localhost; `just check` = lint + typecheck + unit tests + API-contract drift; `just smoke` boots the full stack and runs the `@fullstack` Playwright suite through Caddy. Note `just init` mints a *new* random `POSTGRES__PASSWORD`, and Postgres only applies it when the cluster is first created — so `rm .env && just init` on a machine that has already run the stack needs `docker compose down -v` too, or the api crash-loops on `InvalidPasswordError`.
11. **Repo governance and dev workflow.** The devcontainer (`.devcontainer/`) installs uv, bun, `just` (`uv tool install rust-just`, D16), prek, git-cliff and both projects' dependencies. Git hooks run through prek: cheap checks plus a `commit-msg` conventional-commit check on commit, typecheck and unit tests on push. `main` is squash-only with PRs required (the `protect-main` ruleset, applied by `scripts/setup-repo.sh`), `squash_merge_commit_title = PR_TITLE` / `..._message = PR_BODY`, so a merged PR becomes one commit whose subject is the PR title — which `.github/workflows/pr-title.yml` lints as a required status check (D18, D19). `CHANGELOG.md` stays hand-curated; `just changelog` (git-cliff, `cliff.toml`) prints a draft from conventional commits. The eleven-type list is duplicated in `.pre-commit-config.yaml`, `cliff.toml` and `pr-title.yml` — change all three together.

**DoD (met, re-verified 2026-08-04):** fresh clone → `just init && just up` → a login page on http://localhost with every service healthy; `just check` and `just smoke` green; CI green.

### WP-1: Domain core — athlete, anchors, zones, versioning primitives

Add `hypothesis` to the backend dev dependencies here (the first property-tested code). Delete WP-0's `items` worked example — persistence, service, schemas, routes, tests and the frontend page — once the real entities exist.

`backend/app/domain`:

1. **Versioning primitives:** `Versioned[T]` pattern — every derived artefact type carries `artefact_id` (stable identity), `version` (int), `as_of` (UTC), `superseded_by | None`, `recompute_reason | None`. Helper for "current version" and "version as seen at time T".
2. **Athlete:** profile (name, dob, sex, height), discipline capability stubs (free-form per-discipline dict for MVP).
3. **Anchors:** `AnchorType` (FTP, LTHR, MAX_HR — CP/W′ reserved as enum values, unused), `AnchorVersion` (value, unit, provenance enum, protocol str, effective_date, ci_low/ci_high, created_at, source: athlete|agent). Append-only list per type. `staleness_state` field present (`fresh` hardcoded in MVP — the model is deferred, the column is not).
4. **Zones:** declared zone model enum (`coggan_7` power, `lthr_5` HR for MVP), pure derivation `zones_for(anchor_version, model) -> list[Zone]`. Zones are always computed, never stored.
5. Postgres schema + Alembic migration in `backend/app/persistence`; repository functions; API routes: athlete get/update, anchor list/append (no update/delete — 405 with explanatory message), zones get (query param: anchor version, default current).
6. Audit log table from day one: `audit_log(actor{athlete|agent:<key-label>|system}, action, entity_type, entity_id, payload_json, at)`. Every write path appends. Reserved, used by all later WPs.

**Tests:** hypothesis round-trips on zone derivation; append-only enforcement; audit rows on every mutation.

### WP-2: Workout model + library + purpose templates

1. **Structured workout** (domain): recursive `Step` tree — `SteadyStep(duration|distance, targets)`, `RampStep`, `RepeatBlock(times, children)`. Targets: per-channel (`power`, `hr`, `cadence`), each `%_of_anchor(anchor_type, pct_low, pct_high)` or `absolute(low, high, unit)`. Flattening function for display/scoring.
2. **Strength workout** (domain): `Exercise` (id, name, category, unilateral flag), `StrengthSet` prescription (exercise, sets, reps, load {kg | %e1RM | RPE-target | bodyweight}, RIR target, rest, tempo optional), supersets as grouping. Exercise catalogue table seeded from a bundled JSON (~80 common exercises: squat/hinge/press/pull/core families). `DECIDE:` catalogue seed source — default: hand-curated JSON in repo (wger import is a later phase).
3. **Purpose vocabulary** (enum): endurance: `recovery, endurance, tempo, sweet_spot, threshold, vo2max, anaerobic, neuromuscular, unstructured, technique, test`; strength: `max_strength, strength_endurance, hypertrophy, power, core, mobility, conditioning`.
4. **Purpose templates:** per purpose — default success-criteria set and **which scoring axes apply** (see WP-6 axis table). Stored as data (JSON in repo, loaded at startup), not code.
5. **Planned session** (domain + persistence): date, discipline, workout ref (library or inline), `purpose`, `intent_text`, `coach_notes`, `success_criteria` (list of criterion objects, auto-derived from template then editable), **pinned anchor versions** (map anchor_type → anchor_version_id, frozen at creation/last pre-execution edit), status (`planned | completed | missed | displaced`), intent version fields per invariant 4.
6. Workout library: CRUD, folders, tags, search (ILIKE is fine). Planned-session CRUD with the freeze rule: edits after a match exists → new intent version + `edited_post_hoc` flag + rescore trigger.
7. Success criteria types (MVP set): `time_in_band(step_selector, band, min_fraction)`, `duration_floor(min)`, `ceiling(channel, max, max_time_above)`, `sets_completed(min_fraction)`, `load_within(pct_tolerance)`. Machine-evaluable, serialized as tagged-union JSON.

**Tests:** step-tree flatten round-trip (hypothesis: random trees); template → criteria derivation; freeze semantics (edit after match → new version, flag, old version retrievable).

### WP-3: Calendar & plan API + minimal week UI

1. API: week view endpoint (`GET /api/v1/plan/week?start=`) returning planned sessions + completion status; move/copy session; plan states `active|paused` on athlete (paused = ingestion continues, no missed-session marking).
2. Web: **Calendar (week) page** — 7-day grid, session cards (discipline icon, purpose color, duration, one-line intent), drag to move (mutate via API), click → session sheet: workout structure rendering (flattened steps as horizontal bar profile), intent, notes, criteria list, edit.
3. Web: **Workout creator** — form-based builder for step trees (add steady/ramp/repeat, nest repeats), per-channel targets with %/absolute toggle, live profile preview (simple SVG bars — uPlot not needed here); strength builder (exercise picker from catalogue, sets×reps×load×RIR rows, superset grouping); purpose picker; intent/notes textareas; criteria editor pre-filled from template.
4. Web: **Today view** — today's session(s): what/how long/how hard in one sentence (compose from purpose + duration + dominant target), intent, coach notes.

**DoD:** plan a realistic week (3 rides, 2 strength, 1 rest) entirely in the UI in <10 minutes.

### WP-4: Ingestion — watched folder, FIT parsing, sessions & recordings, streams

1. `backend/app/ingest`: APScheduler job (30s interval) scanning `data/inbox/`; also `POST /api/v1/ingest/upload`. Pipeline per file: hash (sha256) → duplicate check (by hash) → parse (garmin-fit-sdk; fitdecode fallback; gpxpy/tcxreader for GPX/TCX) → validate (monotonic timestamps, plausible ranges: power 0–2500W, HR 25–230, speed <35 m/s; total duration >2min) → on failure move to `data/quarantine/` + create quarantine record with reason → on success: move original to `data/originals/YYYY/MM/<hash>.<ext>` (never modified again), create `recording` row, write per-second channels to `data/streams/<recording_id>.parquet` (schema: `t` (UTC), `power`, `hr`, `cadence`, `speed`, `elevation`, `temp`, `lat`, `lon`; nullable columns; source label per channel in parquet metadata).
2. **Session vs recording:** MVP is single-recording, but the schema separates `session` (real-world event: start, end, discipline guess, timezone at start) from `recording` (device account, FK → session) 1:1 for now. Dedup: a new file whose time range overlaps an existing session >70% → quarantine as `suspected_duplicate` with a confirm/reject UI action (confirm = discard file with log; reject = create separate session). No channel merging in MVP.
3. Discipline classification: FIT sport field; fallback heuristics (has power/speed → ride; short + no GPS + no power → strength candidate); always athlete-overridable.
4. Session date = start time in athlete's local timezone at start (store tz name on session). Midnight-crossers belong to start date.
5. Manual session entry (for strength without a device): logged sets (exercise, reps, load, RIR per set), RPE, duration, notes → creates a session with `recording_kind=manual`.
6. Web: **Inbox/quarantine page** — pending duplicates and quarantined files with reasons and actions; ingest log list. **Session list page** (date, discipline, duration, load once WP-5 lands, matched/unmatched badge).

**Tests:** golden FIT files (commit 3–5 real anonymized files: outdoor ride, indoor trainer, strength-watch recording) → snapshot parsed summaries; quarantine paths (corrupt file, absurd values, duplicate); timezone/midnight property tests. **Never delete anything under `data/originals`.**

### WP-5: Metrics + session analysis (minimal) + stream charts

1. `backend/app/domain/metrics.py` — pure functions over polars frames: normalized power (30s rolling 4th-power mean), IF (NP/FTP using pinned-or-current anchor per invariant), training load (TSS-style: `(dur_s × NP × IF)/(FTP×3600)×100`), average/max per channel, work (kJ), time-in-zone per zone model, simple elevation gain. Strength: volume load (Σ sets×reps×load), sets completed. Every metric result stored as a versioned artefact recording `anchor_version_id` inputs and `computed_at`. Reference values cross-checked in tests against hand-computed fixtures (document formulas in docstrings; GoldenCheetah is the reference implementation for NP).
2. **Structure alignment** (`backend/app/domain/alignment.py`): map planned flattened steps onto the recording timeline. MVP algorithm: work-interval detection via power/HR threshold crossing smoothed at 10s, then order-preserving assignment to planned work steps (dynamic programming on duration similarity); each aligned step gets `alignment_confidence` (0–1, from duration + intensity mismatch); steps below 0.5 confidence are excluded from adherence scoring with reason `alignment_low_confidence`. For strength: alignment unit is the logged set list vs. prescription (no timeline).
3. Web: **Session detail page** — uPlot stacked channel charts (power/HR/cadence/speed/elevation) with synced cursors, zoom/pan; zone-distribution bar (ECharts); laps/detected-intervals table; metric summary header (duration, NP, IF, load, work, TiZ); planned-vs-actual overlay when matched (planned step bands rendered behind the power trace).

**Tests:** NP/IF/TSS fixtures; alignment on synthetic recordings (hypothesis: generate plan + noisy execution, assert assignment); alignment confidence monotonicity.

### WP-6: Matching engine

Semantics (from spec — implement exactly):

1. Candidate window: planned sessions of same discipline within ±1 day of session start (athlete-local dates).
2. Similarity score ∈ [0,1]: duration ratio (40%), intensity profile (NP or avg HR vs. prescribed dominant target, 30%), structure hint (detected work-interval count vs. planned, 30%). Weights constant, documented.
3. Behavior: similarity ≥ 0.75 → auto-link `auto_high` (still shown as revocable); 0.4–0.75 → `pending` match proposal (athlete confirms in UI); < 0.4 → no link proposed; activity stands as `unplanned`.
4. **Executed-instead-of:** athlete (or agent proposal) can link a low-similarity activity to a planned session as `displaced` — planned session status becomes `displaced` (not missed, not completed), activity is scored standalone only (no adherence axes).
5. One-to-many/many-to-one: MVP supports **two recordings → one planned session** (e.g. garage-door stop) via manual "merge into one session" action (concatenates for scoring, keeps both recordings); everything else is 1:1. Set-to-set generality is schema-level only (link table, not FK on session).
6. Manual operations, always available: link, unlink, swap (retarget), mark-unplanned, merge. Manual links are `confirmed` and sticky — re-runs of matching never touch them.
7. Missed: a planned session with no link at end of day+1 (athlete-local) → status `missed`, evening-prompt record created (WP-7 consumes).
8. Every match state change is audited and reversible (unlink restores prior states).

**Tests:** the full case table as parametrized tests — done-a-day-late, two-planned-one-done, swap, unplanned group ride, merge two files, low-similarity displacement, sticky confirmed links across re-match.

### WP-7: Scoring engine + verdicts + reasons

1. **Axes** (each returns score ∈ [0,1] | `not_assessed(reason)`), applicability from purpose template:
   - `completion`: fraction of planned duration/sets done (all purposes).
   - `adherence`: time-in-band per aligned work step, criterion-weighted (endurance structured purposes; suppressed for `unstructured`, `recovery` optional per template).
   - `discipline`: time above ceiling criteria (endurance; e.g. recovery rides cap).
   - `pacing`: fade across repeat blocks — ratio of last-rep NP to first-rep NP vs. allowed drift (interval purposes only).
   - strength: `sets_load`: sets completed × load-within-tolerance composite.
   - `response` and `fuelling`: **not in MVP** — return `not_assessed(deferred)` so the shape exists.
2. **Verdict:** machine-suggested from axes (rule table: e.g. completion<0.5 → `abandoned` unless displaced; adherence≥0.8 & discipline ok → `as_intended`; systematic under-target → `under`; over → `over`; displaced link → `different_session`) — **athlete-declared**: UI shows suggestion, athlete confirms or overrides; both stored (`suggested_verdict`, `declared_verdict`, `declared_at`). Agent may never write `declared_verdict`.
3. **Reasons:** on any non-`as_intended` declaration, prompt for 1–3 reasons ordered by primacy from: `time, weather, heat, traffic, terrain, fatigue, sleep, fuelling, illness, equipment, group_ride, felt_good, not_provided` + optional free text. Evening-prompt records expire after 72h → auto-reason `not_provided`. Reasons revisable append-only.
4. Scores are versioned artefacts (invariant 1): store axis results + criteria evaluations + `anchor_version_id` + alignment version. Rescore (from intent edit or manual recompute) creates version n+1; if a `declared_verdict` exists and the new suggested verdict differs → set `contested=true`, surface in UI, never change the declaration.
5. Web: **Verdict flow** — after ingest+match, session detail shows axis results (with per-criterion pass/fail detail), suggested verdict, one-tap confirm/override + reason picker. **Week strip** on calendar shows per-day completion state (planned/completed-as-intended/under/over/missed/displaced color coding).

**Tests:** axis math on fixtures; verdict rule table exhaustive; contested-flag flow; reason expiry job.

### WP-8: Agent layer v0 — MCP server + proposals + guardrails

1. `backend/app/mcp` (FastMCP 3, scaffolded in WP-0): tools, all delegating to the same `app.services` layer as the API — no separate logic. Enforce the key's scope (`read`/`write`, already on the authenticated identity) per tool:
   - Reads: `get_athlete`, `get_anchors`, `get_plan_week`, `get_session_detail` (incl. axis scores, alignment, metrics), `list_sessions(filter)`, `get_workout_library`, `search_history` (date-range summaries).
   - Writes (guarded): `append_anchor` (provenance required; `tested` provenance requires protocol string), `create_workout`, `propose_plan_change` (see 2), `write_session_evaluation` (interpretive text attached to a session — Tier 1), `annotate` (free commentary, Tier 0).
   - Explicitly absent: any tool that mutates recordings, streams, declared verdicts, or reasons.
2. **Proposals:** `propose_plan_change(changes: [create/update/move/delete planned sessions], rationale, expires_at)` → proposal record with computed diff. Athlete UI: proposal inbox with diff view, accept/reject (+ rejection reason free text). Expiry job → `lapsed`. New proposal for an entity with an open one → supersedes it (linked). Activity ingested that contradicts pending proposal (same date+discipline) → auto-`resolved_by_reality`, logged. Accept applies changes transactionally with audit rows.
3. **Guardrails (enforced in the service layer, not the MCP shell):** append-only anchors; dry-run flag on every write tool (returns the diff without committing); optimistic concurrency (entity version tokens; stale token → structured error); audit rows with `actor=agent:<key-label>`; rate cap (configurable, default 60 writes/hour) as a circuit breaker.
4. **Red-flag safety rules v0 (deterministic, in service layer):** if an active illness/injury flag is set on the athlete (simple boolean + note + severity in MVP, settable via UI), all `propose_plan_change` calls that add or intensify sessions are rejected with a stated reason; agent tools receive the flag in every read so it cannot claim ignorance.
5. Interpretive content model: `agent_note(session_id | plan_week, text, model_id, created_at, cites: [artefact ids])` — rendered in UI in a visually distinct "Coach" style, with model attribution and a one-tap 👍/👎 dispute stored per note (seed of the coach-quality loop).
6. Operator docs: `docs/agent-setup.md` — how to connect Claude (desktop/CLI) to the MCP server with an API key, plus a starter system prompt for the coach role (short: role, autonomy tier summary, "always dry-run first on multi-entity changes", "never guess anchors — ask for a test").

**Tests:** guardrail unit tests (append-only violation, concurrency conflict, rate cap, red-flag rejection); proposal lifecycle state machine (all transitions incl. supersede, lapse, resolved-by-reality); MCP integration test driving tools end-to-end against a seeded DB.

### WP-9: Hardening, seeds, backup, acceptance

1. Seed script (add a `just seed` recipe): demo athlete, anchors (FTP 250 `estimated`), 2-week plan, 6 golden FIT files pre-ingested and matched — the app demos in one command.
2. Backup: nightly APScheduler job → `pg_dump` + tar of `data/originals` to `data/backups/` (operator points a volume/restic at it); a `just verify-restore` recipe spins an ephemeral Postgres container, restores latest dump, runs smoke queries, reports. Document in `docs/operations.md`.
3. Retention/version GC: none — MVP keeps all versions (single user; revisit at MMP).
4. Playwright E2E (the critical path only): login → create workout → plan week → upload FIT → confirm match → declare verdict with reason → see week strip update → receive and accept an MCP-created proposal (drive MCP via test client).
5. Performance sanity: session detail with a 4h ride (14k points) renders <1.5s on a laptop; ingest of a 4h FIT <10s.
6. `README.md`: architecture sketch, setup, invariants (§0 verbatim), and a pointer to the running decision log in `docs/decisions.md`.

**MVP acceptance checklist (all must pass):**
- [ ] Fresh machine: `git clone && just init && just up` → usable app in <15 min
- [ ] Plan a week with intents in UI in <10 min
- [ ] Drop a FIT in the inbox → session appears, matched or pending, within 60s
- [ ] Scored session shows axis detail + suggested verdict; override works; reasons captured; contested flow works after an intent edit
- [ ] All matching case-table tests green (day-late, double-day, swap, unplanned, merge, displaced)
- [ ] Claude connected via MCP can: read the week, evaluate a session, propose a plan change with dry-run then commit; proposal appears in UI with diff; red-flag mode blocks intensification proposals
- [ ] Kill the LLM: every screen and every computed value still works
- [ ] `just verify-restore` passes
- [ ] `just check` green (ruff, pyrefly, import-linter, unit tests, API-contract drift) plus `just test-int` and `just smoke`; no `TODO` without an issue reference

---

## 4. Execution guidance for the model

- Work WP by WP; commit per WP; append to `docs/decisions.md` (it exists, entries D1–… are append-only) for every `DECIDE:` and any ambiguity resolved — state the choice, the alternative it displaced, and why. The operator reviews these. Supersede a decision with a new entry; never rewrite one.
- When a library pin conflicts with reality at build time (e.g. a newer patch), take the newest patch within the pinned minor, let the lockfile record it, and note it.
- Golden FIT files: if none are provided by the operator, generate synthetic FIT files with the fit-tool fork or construct parquet-level fixtures and mark the FIT-parse tests as operator-pending — do not silently skip the pipeline tests.
- Do not add features from later phases (weather, wellness, PMC, availability) even where they'd be easy — schema reservations only where this plan says so.
- Ask the operator only when a `DECIDE:` default is unworkable or credentials/files are needed (Google Maps key is NOT needed in MVP — no maps in MVP).
