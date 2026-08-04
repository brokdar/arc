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

## 1. Stack (pinned, verified 2026-08-01)

Backend: Python 3.14.6, FastAPI 0.141.x, Pydantic 2.13.x, SQLAlchemy 2.0.51 (2.1-clean style: typed `Mapped[]` only) + Alembic 1.18.x, PostgreSQL 18.4, uv 0.12.x, ruff 0.16.x, pyright (strict), pytest 9.1.x + pytest-asyncio + hypothesis, APScheduler 3.11.x, FastMCP 3.4.x, httpx 0.28.x, polars 1.43.x, pyarrow 25.x, garmin-fit-sdk (decode) with fitdecode fallback, gpxpy, tcxreader.
Frontend: Next.js 16.2.x (App Router, Turbopack), React 19.2.x, TypeScript 7.0.x, Tailwind 4.3.x, shadcn CLI 4.16.x (Base UI), TanStack Query 5.101.x, uPlot 1.6.32 (ride streams), ECharts 6.1.x (dashboard charts), Node 24 LTS, pnpm 11.18.x, openapi-typescript + openapi-fetch (current at scaffold time), Vitest + Playwright (current at scaffold time).
Infra: Docker Compose v5, Caddy 2.11.x. No Redis/Valkey, no Celery. Scheduling is in-process APScheduler.

Conventions: ruff format + lint (line length 100), pyright strict passes with zero errors, `from __future__` never needed (3.14), all functions typed, domain layer has no I/O imports. Frontend: strict TS, ESLint (next config), no `any`.

---

## 2. Repository layout (monorepo)

```
training-app/
├── apps/
│   ├── api/            # FastAPI app (thin: routers, deps, auth) — Python
│   ├── mcp/            # FastMCP server (thin: tools → domain services) — Python
│   └── web/            # Next.js 16 app — TypeScript
├── packages/
│   ├── domain/         # PURE Python: entities, value objects, scoring, matching,
│   │                   # metrics, workout model, zone math. No SQLAlchemy, no I/O.
│   ├── persistence/    # SQLAlchemy models, repositories, Alembic migrations
│   ├── ingest/         # file watching, FIT/TCX/GPX parsing, quarantine, parquet writer
│   └── client/         # generated TS client (openapi-typescript output)
├── infra/
│   ├── compose.yaml    # api, web, postgres, caddy, mcp
│   ├── Caddyfile
│   └── backup/         # pg_dump + originals backup scripts, restore-verify script
├── data/               # runtime volumes (gitignored): originals/, inbox/, streams/, quarantine/
├── pyproject.toml      # uv workspace root
├── pnpm-workspace.yaml
└── Makefile            # dev, test, lint, typecheck, migrate, seed, e2e — single entry points
```

Dependency rule: `domain` imports nothing from the other packages. `persistence`, `ingest`, `api`, `mcp` import `domain`. `api` and `mcp` never import each other; both consume the same service layer in `persistence` (or a small `services/` module inside it).

---

## 3. Work packages

Execute in order. Each WP ends with: tests green, `make lint typecheck test` clean, a short CHANGELOG entry, and a git commit. Do not start a WP with the previous one red.

### WP-0: Scaffold + infrastructure (day 0)

1. Init monorepo: uv workspace (`pyproject.toml` with `[tool.uv.workspace] members = ["apps/api","apps/mcp","packages/*"]` for the Python packages), pnpm workspace for `apps/web` + `packages/client`.
2. `packages/domain`, `packages/persistence`, `packages/ingest`: empty packages with py.typed, pytest wired.
3. `apps/api`: FastAPI skeleton, `/healthz`, `/api/v1` router mount, settings via pydantic-settings (env-prefixed `APP_`), structured logging (stdlib logging + JSON formatter).
4. `apps/web`: `create-next-app` (App Router, TS, Tailwind 4), shadcn init (Base UI), dark mode class strategy, one page rendering API health.
5. `infra/compose.yaml`: services `postgres:18.4` (volume, healthcheck), `api` (uv-based multi-stage Dockerfile), `web` (standalone Next build), `mcp`, `caddy` (reverse proxy: `/` → web, `/api` → api, auto-HTTPS disabled for local, enabled via env for deployment). Bind-mount `data/` into api.
6. Auth: single-user session cookie. `POST /api/v1/auth/login` with a bcrypt-hashed password from env; session middleware; every other route requires it. MCP uses static bearer API keys (`APP_MCP_API_KEYS`, comma-separated, each with a label) — scoped read/write flag per key.
7. CI (GitHub Actions or equivalent): lint, typecheck, test, frontend build, `alembic upgrade head` against a service Postgres.
8. Makefile targets working end-to-end. `docker compose up` yields a login page and healthy services.

**DoD:** fresh clone → `make dev` runs everything; CI green.

### WP-1: Domain core — athlete, anchors, zones, versioning primitives

`packages/domain`:

1. **Versioning primitives:** `Versioned[T]` pattern — every derived artefact type carries `artefact_id` (stable identity), `version` (int), `as_of` (UTC), `superseded_by | None`, `recompute_reason | None`. Helper for "current version" and "version as seen at time T".
2. **Athlete:** profile (name, dob, sex, height), discipline capability stubs (free-form per-discipline dict for MVP).
3. **Anchors:** `AnchorType` (FTP, LTHR, MAX_HR — CP/W′ reserved as enum values, unused), `AnchorVersion` (value, unit, provenance enum, protocol str, effective_date, ci_low/ci_high, created_at, source: athlete|agent). Append-only list per type. `staleness_state` field present (`fresh` hardcoded in MVP — the model is deferred, the column is not).
4. **Zones:** declared zone model enum (`coggan_7` power, `lthr_5` HR for MVP), pure derivation `zones_for(anchor_version, model) -> list[Zone]`. Zones are always computed, never stored.
5. Postgres schema + Alembic migration in `persistence`; repository functions; API routes: athlete get/update, anchor list/append (no update/delete — 405 with explanatory message), zones get (query param: anchor version, default current).
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

1. `packages/ingest`: APScheduler job (30s interval) scanning `data/inbox/`; also `POST /api/v1/ingest/upload`. Pipeline per file: hash (sha256) → duplicate check (by hash) → parse (garmin-fit-sdk; fitdecode fallback; gpxpy/tcxreader for GPX/TCX) → validate (monotonic timestamps, plausible ranges: power 0–2500W, HR 25–230, speed <35 m/s; total duration >2min) → on failure move to `data/quarantine/` + create quarantine record with reason → on success: move original to `data/originals/YYYY/MM/<hash>.<ext>` (never modified again), create `recording` row, write per-second channels to `data/streams/<recording_id>.parquet` (schema: `t` (UTC), `power`, `hr`, `cadence`, `speed`, `elevation`, `temp`, `lat`, `lon`; nullable columns; source label per channel in parquet metadata).
2. **Session vs recording:** MVP is single-recording, but the schema separates `session` (real-world event: start, end, discipline guess, timezone at start) from `recording` (device account, FK → session) 1:1 for now. Dedup: a new file whose time range overlaps an existing session >70% → quarantine as `suspected_duplicate` with a confirm/reject UI action (confirm = discard file with log; reject = create separate session). No channel merging in MVP.
3. Discipline classification: FIT sport field; fallback heuristics (has power/speed → ride; short + no GPS + no power → strength candidate); always athlete-overridable.
4. Session date = start time in athlete's local timezone at start (store tz name on session). Midnight-crossers belong to start date.
5. Manual session entry (for strength without a device): logged sets (exercise, reps, load, RIR per set), RPE, duration, notes → creates a session with `recording_kind=manual`.
6. Web: **Inbox/quarantine page** — pending duplicates and quarantined files with reasons and actions; ingest log list. **Session list page** (date, discipline, duration, load once WP-5 lands, matched/unmatched badge).

**Tests:** golden FIT files (commit 3–5 real anonymized files: outdoor ride, indoor trainer, strength-watch recording) → snapshot parsed summaries; quarantine paths (corrupt file, absurd values, duplicate); timezone/midnight property tests. **Never delete anything under `data/originals`.**

### WP-5: Metrics + session analysis (minimal) + stream charts

1. `domain/metrics.py` — pure functions over polars frames: normalized power (30s rolling 4th-power mean), IF (NP/FTP using pinned-or-current anchor per invariant), training load (TSS-style: `(dur_s × NP × IF)/(FTP×3600)×100`), average/max per channel, work (kJ), time-in-zone per zone model, simple elevation gain. Strength: volume load (Σ sets×reps×load), sets completed. Every metric result stored as a versioned artefact recording `anchor_version_id` inputs and `computed_at`. Reference values cross-checked in tests against hand-computed fixtures (document formulas in docstrings; GoldenCheetah is the reference implementation for NP).
2. **Structure alignment** (`domain/alignment.py`): map planned flattened steps onto the recording timeline. MVP algorithm: work-interval detection via power/HR threshold crossing smoothed at 10s, then order-preserving assignment to planned work steps (dynamic programming on duration similarity); each aligned step gets `alignment_confidence` (0–1, from duration + intensity mismatch); steps below 0.5 confidence are excluded from adherence scoring with reason `alignment_low_confidence`. For strength: alignment unit is the logged set list vs. prescription (no timeline).
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

1. `apps/mcp` (FastMCP 3): tools, all delegating to the same service layer as the API — no separate logic:
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

1. Seed script (`make seed`): demo athlete, anchors (FTP 250 `estimated`), 2-week plan, 6 golden FIT files pre-ingested and matched — the app demos in one command.
2. Backup: nightly APScheduler job → `pg_dump` + tar of `data/originals` to `data/backups/` (operator points a volume/restic at it); `make verify-restore` spins an ephemeral Postgres container, restores latest dump, runs smoke queries, reports. Document in `docs/operations.md`.
3. Retention/version GC: none — MVP keeps all versions (single user; revisit at MMP).
4. Playwright E2E (the critical path only): login → create workout → plan week → upload FIT → confirm match → declare verdict with reason → see week strip update → receive and accept an MCP-created proposal (drive MCP via test client).
5. Performance sanity: session detail with a 4h ride (14k points) renders <1.5s on a laptop; ingest of a 4h FIT <10s.
6. `README.md`: architecture sketch, setup, invariants (§0 verbatim), decision log of every `DECIDE:` taken.

**MVP acceptance checklist (all must pass):**
- [ ] Fresh machine: `git clone && make dev` → usable app in <15 min
- [ ] Plan a week with intents in UI in <10 min
- [ ] Drop a FIT in the inbox → session appears, matched or pending, within 60s
- [ ] Scored session shows axis detail + suggested verdict; override works; reasons captured; contested flow works after an intent edit
- [ ] All matching case-table tests green (day-late, double-day, swap, unplanned, merge, displaced)
- [ ] Claude connected via MCP can: read the week, evaluate a session, propose a plan change with dry-run then commit; proposal appears in UI with diff; red-flag mode blocks intensification proposals
- [ ] Kill the LLM: every screen and every computed value still works
- [ ] `make verify-restore` passes
- [ ] pyright strict + ruff + all tests green; no `TODO` without an issue reference

---

## 4. Execution guidance for the model

- Work WP by WP; commit per WP; keep a running `docs/decisions.md` for every `DECIDE:` and any ambiguity resolved (state the choice and why — the operator reviews these).
- When a library pin conflicts with reality at build time (e.g. a newer patch), take the newest patch within the pinned minor and note it.
- Golden FIT files: if none are provided by the operator, generate synthetic FIT files with the fit-tool fork or construct parquet-level fixtures and mark the FIT-parse tests as operator-pending — do not silently skip the pipeline tests.
- Do not add features from later phases (weather, wellness, PMC, availability) even where they'd be easy — schema reservations only where this plan says so.
- Ask the operator only when a `DECIDE:` default is unworkable or credentials/files are needed (Google Maps key is NOT needed in MVP — no maps in MVP).
