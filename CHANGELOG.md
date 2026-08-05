# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### WP-2 — workout model, library, purpose templates, planned sessions

The prescription half of the loop: what a session *is*, what it is *for*, and
the machinery that freezes both at planning time (build-plan invariant 4).
Decisions D42–D54.

**Domain (`backend/app/domain/`, pure)**

- Added the **structured workout** (`workout.py`): a recursive step tree of
  `SteadyStep` / `RampStep` / `RepeatBlock`, with per-channel targets (power,
  hr, cadence) that are either a percentage range of an anchor or an absolute
  range in the channel's own unit. `flatten()` expands repeat blocks — each
  flat step remembering which iteration of which block it came from — and
  leaves ramps whole, carrying a start and an end target set (D42). Rules are
  enforced at construction: exactly one of duration or distance, a channel may
  only be a percentage of an anchor it derives from, an absolute target must
  use the channel's unit and lie in a plausible range, and a ramp's two ends
  must be the same kind of target — no interpolating from a fraction of an
  unresolved anchor to an absolute number (D53).
- The nesting bound is enforced *while* a step tree is decoded, not once it is
  built: decoding is recursive, so a deep enough document exhausted the
  interpreter stack before there was a workout to check.
- A `ceiling` criterion's absolute limit is held to the channel's plausibility
  bounds, as an absolute target always was — a 1e300 W cap is a criterion
  every session passes, not a rule.
- Added the **strength model** (`strength.py`): a catalogue `Exercise`
  (slug, name, category, unilateral flag) and a `StrengthSet` prescription
  (exercise, sets, reps, load as kg / %e1RM / RPE / bodyweight, RIR, rest,
  tempo), grouped by `StrengthGroup` — a group of more than one item *is* a
  superset, with no flag to keep in step with the count.
- Added the **purpose vocabulary** (`purpose.py`): eleven endurance and seven
  strength purposes, each paired with exactly one discipline.
- Added **success criteria** (`criteria.py`): the MVP five — `time_in_band`,
  `duration_floor`, `ceiling`, `sets_completed`, `load_within` — as a
  tagged-union value set with (de)serialization. A band is a tolerance around
  the step's *own* prescribed target rather than an absolute range (D44), which
  is what lets a purpose template state one at all. Evaluation is WP-7.
- Added **purpose templates** (`templates.py`) and the `ScoringAxis`
  vocabulary WP-7 will compute (`completion, adherence, discipline, pacing,
  sets_load`, with `response`/`fuelling` reserved and deliberately unclaimed).
- Added the **planned-session intent** (`sessions.py`): purpose, prescription
  snapshot, criteria, pinned anchor versions, and the rule that every anchor a
  prescription refers to must be pinned.
- Percentages are fractions everywhere, matching the zone model (D43), and
  domain JSON is decoded by shared helpers that refuse unknown fields and
  locate every error in the document (D52).

**Data in the repository**

- Added `backend/app/resources/purpose_templates.json`: per purpose, the
  scoring axes that apply and the success criteria a session starts with.
  Loaded and validated at startup, so a file that omits a purpose, names an
  unknown axis, or carries a criterion that purpose could never evaluate stops
  the boot rather than surfacing at scoring time (D45).
- Added `backend/app/resources/exercise_catalogue.json`: 98 hand-curated
  movements across nine families (the plan's `DECIDE:` default). The
  `exercises` table is keyed by slug and seeded from it **lazily and
  idempotently on first access** — not by a migration, which a truncating test
  fixture or a restore would defeat, and not by the lifespan, which would make
  a successful boot depend on a writable database (D46).

**Persistence**

- Added the `exercises`, `workouts`, `workout_tags`, `planned_sessions` and
  `planned_session_intents` tables with migration `0003`. Prescriptions are one
  JSON document; tags get a table because "which workouts are tagged X" is a
  query and array containment is dialect-specific; folders stay a column (D50).
- Intent versions are append-only and carry WP-1's versioning vocabulary
  verbatim, so `app.domain.versioning`'s chain helpers work on the ORM rows
  unchanged.

**API**

- Added `GET/POST /api/v1/workouts`, `GET/PATCH/DELETE /api/v1/workouts/{id}`
  with search (ILIKE, wildcards escaped), folder/tag/discipline filters, and
  `GET /api/v1/workout-labels` for the folder and tag lists in use.
- Added the read-only `GET /api/v1/exercises`(`/{id}`) and
  `GET /api/v1/purposes`(`/{purpose}`), the latter returning each purpose's
  axes and default criteria so the planning UI pre-fills from the same
  templates the server derives from.
- Added `GET/POST /api/v1/planned-sessions`,
  `GET/PATCH/DELETE /api/v1/planned-sessions/{id}` and the intent history at
  `/{id}/intents` and `/{id}/intents/{version}`. Creating a session derives its
  criteria from the purpose template and pins the anchor versions in force; a
  prescription referring to an anchor with none in force is refused with a 422
  that names which half of it — the targets, the criteria or both — asked for
  the anchor, and what to do about it (D49).
- `PATCH /api/v1/planned-sessions/{id}` refuses an explicit `null` for
  `purpose`, `date` or `status` with a 422, and refuses an empty body rather
  than answering 200 with an audit row saying nothing changed. A patch that
  moves the session *and* edits its intent now leaves both audit rows.
- Implemented the freeze rule (D47): an intent edit before a match exists
  writes a new version and re-pins; an edit after a match exists writes a new
  version flagged `edited_post_hoc`, **keeps** the pins the athlete executed
  against, and triggers a rescore. Editing only a session's date or status
  versions nothing. Matching and rescoring do not exist yet, so both are
  explicit, tested seams the later work packages replace with one function
  each (D48).
- The whole step tree and criterion set are typed end to end: recursive
  discriminated unions in the API schemas, regenerated into
  `frontend/generated/api/`.
- Schemathesis found that `/workouts/folders` and `/workouts/tags` were
  shadowed by `/workouts/{workout_id}`, so an undocumented method on them
  answered 422 about uuid syntax instead of 405. The facet moved to
  `/workout-labels`, outside the id namespace (D50); the four new write
  operations that refuse schema-valid input by domain rule are narrowed per
  operation in `backend/schemathesis.toml`.

**Testing**

- Added property tests over random step trees (hypothesis): flattening and
  expansion round-trip, indices are execution order, duration is conserved,
  and the serialized form round-trips.
- Added template-derivation tests for every purpose in the vocabulary, plus
  the malformed-file cases that must stop the boot.
- Added freeze-semantics tests driving the match/rescore seams: post-match
  edit produces a new version, the flag, kept pins, a rescore call and a
  retrievable original; pre-match edit produces a new version, no flag, and
  re-pinned anchors.
- The unit suite now turns SQLite's foreign keys on (D51), so `ON DELETE
  CASCADE`/`SET NULL` behave there as they do on Postgres — the divergence
  that hid a real failure until the pragma went in.

### WP-1 — domain core: athlete, anchors, zones, versioning primitives

The first real entities, and the first code the build plan's invariants are
enforced by rather than described in. Decisions D31–D37.

**Domain (`backend/app/domain/`, pure)**

- Added **versioning primitives** (`versioning.py`): the `VersionRecord`
  protocol and the `Versioned[T]` envelope fixing the vocabulary every derived
  artefact will carry — `artefact_id`, `version`, `as_of`, `superseded_by`,
  `recompute_reason` — plus `current_version`, `version_as_seen_at` and
  `next_version`. Recomputation returns both halves of the change (the closed
  old version and the new tip), so a chain cannot be left with a dangling link.
  Nothing in WP-1 is versioned yet; scores, metrics and alignment are, from
  WP-5.
- Added the **athlete profile** (`athlete.py`): name, date of birth, sex,
  height, and a free-form per-discipline capability stub. Every field is
  optional — an empty profile is a legal state, not an error.
- Added **anchors** (`anchors.py`): `AnchorType` (FTP, LTHR, MAX_HR, with CP
  and W′ reserved and unused), `Provenance`, `AnchorSource`, `AnchorUnit`,
  `StalenessState` (hardcoded `fresh`; `aging`/`stale` reserved), and the
  immutable `AnchorVersion`. Legality is enforced where it belongs (D35): the
  unit must be the anchor type's own, values must be plausible per type,
  `tested` provenance requires a protocol, and a confidence interval must
  bracket its value. `anchor_as_of` computes which version was in force at a
  moment — effective date *and* creation time — so a back-dated correction
  changes the present without rewriting the past.
- Added **zones** (`zones.py`): `zones_for(anchor_version, model)`, with
  `coggan_7` (%FTP) and `lthr_5` (%LTHR) boundary tables documented in D32.
  Zones are always computed, never stored. Bands are half-open and contiguous,
  the top zone is open-ended, and a model may only be applied to the anchor
  type it derives from.
- Domain values are frozen dataclasses rather than pydantic models (D31);
  `app.core.exceptions.domain_rules()` translates their `ValueError`s into the
  documented 422 envelope.

**Persistence**

- Added the `athlete`, `anchor_versions` and `audit_log` tables with their
  repositories, and migration `0002` creating them. The athlete row is a
  singleton with a fixed primary key, bootstrapped on first access rather than
  seeded by the migration (D33). Neither the anchor nor the audit repository
  offers an update or a delete.
- `enum_column` now stores the enum member's **value** (`max_hr`), not
  SQLAlchemy's default of its name (`MAX_HR`), so the database, the API and the
  generated frontend types share one vocabulary (D34). WP-1 is its first user,
  so nothing needed migrating.

**API**

- Added `GET`/`PATCH /api/v1/athlete`, `GET`/`POST /api/v1/anchors`,
  `GET /api/v1/anchors/current` and `GET /api/v1/anchors/{id}`, all on the
  guarded router.
- Added zones as two endpoints, each addressed by what it derives from (D38):
  `GET /api/v1/zones?anchor_type=…` uses the version in force,
  `GET /api/v1/anchors/{id}/zones` uses one pinned version. The zone model is
  derived from the anchor type or named explicitly.
- `PUT`, `PATCH` and `DELETE` on an anchor version return **405 with an
  explanation** and an `Allow` header, because FastAPI answers an undefined
  method+path with 404 — which reads as "wrong id" (D36).
- The reserved anchor types `cp` and `w_prime` cannot be appended (D40): the
  create contract only offers the MVP three, and the service refuses them for
  callers that bypass the schema.
- The singleton athlete bootstrap is race-tolerant and never a side effect of
  a rejected write (D41): a lost first-access race returns the winner's row
  instead of a 409, and a 422'd first-ever `PATCH` leaves the database
  untouched — bootstrap and update happen in one transaction, both audited.
- Hardened the new surface against what Schemathesis found (D39): the 422
  contract now admits both shapes the status really has
  (`ValidationErrorDetail`), the append-only refusals answer 405 for any id
  rather than 422 for a malformed one, and free-form `capabilities` JSON is
  validated for driver-safe text at every depth, not just at the top level.
  `backend/schemathesis.toml` narrows the `positive_data_acceptance` check for
  the handful of operations that refuse schema-valid input by design, instead
  of switching it off everywhere.

**Audit log**

- Every mutating service path now appends an `audit_log` row — actor (`athlete`
  / `agent:<label>` / `system`), action, entity type and id, JSON payload, and
  timestamp — in the same transaction as the write it describes, so a rejected
  write leaves no trail and no write escapes one.

**Removed**

- Deleted WP-0's `items` worked example end to end: backend persistence,
  service, schemas, routes and tests; the frontend page, component and MSW
  handler; and the table itself, dropped by migration `0002` rather than by
  rewriting the already-shipped `0001` (whose `downgrade` recreates it, so the
  chain still round-trips).

**Testing**

- Added `hypothesis` (dev-only, D37) and the repo's first property tests: zone
  schemes must partition, stay ordered, scale linearly with the anchor, and
  keep percentages and absolute bounds in agreement — properties that hold for
  any scheme added later, not just the two shipped.

### WP-0 — scaffold + infrastructure

The scaffold was built by adapting the full-stack template rather than
scaffolding the build plan's `apps/`+`packages/` workspace monorepo; the
reasoning for this and every other departure is in `docs/decisions.md`
(D1–D19).

**Backend architecture**

- Restructured the backend from `app/domains/<domain>/` into layered modules:
  `app/domain` (pure business rules, filled in by WP-1), `app/persistence`
  (db, ORM models, repositories, Alembic), `app/services`, `app/ingest` and
  `app/mcp` skeletons, and `app/api` (routes, schemas, pagination,
  validation), with `app/core` reduced to genuinely cross-cutting code.
  Boundaries — domain purity, `api`/`mcp` independence, and the layer stack
  `api|mcp → ingest → services → persistence → domain` — are enforced by
  import-linter contracts (`uv run lint-imports`) wired into CI, `just lint`
  and pre-push. The OpenAPI contract is unchanged.
- Upgraded the backend from Python 3.13 to 3.14 (D4) across `pyproject.toml`,
  `.python-version`, `pyrefly.toml`, both `Dockerfile` stages and the
  devcontainer image, and relocked `uv.lock`.
- Removed the ARQ worker and its Redis service (D5), replacing them with an
  in-process APScheduler started by the API lifespan
  (`backend/app/core/scheduler.py`); dropped the `redis` and `worker` compose
  services, the `dev-worker` recipe and the `REDIS__URL` setting.

**Authentication**

- Added single-user session-cookie authentication end to end (D6). The
  credential store is one setting, `AUTH__PASSWORD_HASH` (a bcrypt hash —
  there is no user table); `POST /api/v1/auth/login` swaps it for a signed
  session cookie issued by Starlette's `SessionMiddleware` (`arc_session`,
  `SameSite=Lax`, 14 days, `Secure` once `AUTH__SESSION__HTTPS_ONLY` is on),
  with `POST /api/v1/auth/logout` and an always-open
  `GET /api/v1/auth/session` alongside it. Everything else under `/api/v1` is
  mounted on a router carrying `Depends(require_session)` and a declared 401,
  so new routers are protected by default (D12); `/health` stays open. Failed
  logins sleep ~0.3s to blunt guessing, and production refuses to boot without
  `AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY`. The unused
  `AUTH__JWT__*` shell is gone.
- On the frontend, the API client sends credentials, a `/login` page posts the
  password, and an `AuthGuard` client component bounces unauthenticated
  visitors off the protected pages.
- Schemathesis found an undocumented 400 on login (unparseable body); the
  contract now declares it and a unit test pins it. The fuzz job supplies a
  session cookie and excludes the `ignored_auth` check, which cannot strip a
  raw header (D13).

**MCP server**

- Added the MCP server skeleton (`backend/app/mcp/`): a FastMCP 3 server run
  from the backend image as the `mcp` compose service
  (`python -m app.mcp.main`, streamable HTTP on :8001, behind Caddy's
  `/mcp*`). Every request must present a bearer key from `MCP__API_KEYS`
  (`label:scope:key,...`, scope `read` or `write`); keys are parsed by the
  framework-free `app/mcp/auth.py` and compared with `secrets.compare_digest`
  in a `TokenVerifier` subclass (D10), which puts the caller's label and scope
  on the request identity for per-tool scope checks in WP-8. The server
  refuses to start (exit 1) with no keys, so `MCP__API_KEYS` is required for
  `docker compose up`. The surface is one `ping` tool plus an unauthenticated
  `/health` route for the container healthcheck.

**Infrastructure**

- Added a Caddy reverse proxy (`caddy/Caddyfile`, `caddy` service on :80/:443)
  fronting the whole stack from one origin: `/api/*` and `/health` to the API,
  `/mcp*` to the MCP server, everything else to the frontend.
  `CADDY_SITE_ADDRESS` defaults to `:80` (plain HTTP); set a hostname for
  automatic HTTPS. The frontend is built with an empty
  `NEXT_PUBLIC_API_BASE_URL`, so the browser calls the API same-origin through
  the proxy, and the `@fullstack` smoke suite runs against `http://localhost`.
  Caddy deliberately does not depend on `mcp`, so a missing MCP key set cannot
  take the site down (D9).
- Upgraded Postgres from 17 to 18 (dev stack and the integration-test
  database). Postgres 18 moved the image's `VOLUME` to `/var/lib/postgresql`
  (`PGDATA` is now `/var/lib/postgresql/18/docker`), so the `postgres-data`
  volume and the test tmpfs mount that path instead of `.../data`. **Existing
  local volumes hold a 17 cluster and must be recreated**
  (`docker compose down -v`).
- Added the runtime data tree: `DATA__ROOT` (default `data`) with `inbox/`,
  `originals/`, `streams/`, `quarantine/` created on API startup and
  bind-mounted into the api container at `/app/data`; a one-shot `data-init`
  service hands the root-owned bind mount to the api's non-root user first
  (D8).

**Developer workflow**

- Made `just init` real (`scripts/bootstrap-env.sh`): it copies `.env.example`
  to a mode-600 `.env`, generates `POSTGRES__PASSWORD`,
  `AUTH__SESSION__SECRET_KEY` and both `MCP__API_KEYS`, and prompts (hidden,
  twice) for the login password it bcrypt-hashes into `AUTH__PASSWORD_HASH`.
  Values are substituted with Python rather than `sed`, so the `$` and `/` in
  bcrypt hashes survive; the script is idempotent and degrades to a
  placeholder hash plus instructions when there is no terminal to prompt on
  (D15). `just hash-password` prints a ready-to-paste single-quoted hash for
  rotating the password later.
- `just check` now also runs `api-check`, so API-contract drift is part of the
  one local gate, and the devcontainer installs `just` itself
  (`uv tool install rust-just`, D16) — the whole workflow depended on a tool
  that was not in the image.
- The Playwright `@fullstack` suite logs in once in a `setup` project and
  replays the session via `storageState` (D14).
- Repo hygiene: tracked `docs/`, removed orphaned build artifacts
  (`packages/`, root `node_modules/`), ignored `/data/` and `.schemathesis/`,
  and bumped the `ruff-pre-commit` hook to v0.16.1 to match the backend
  lockfile.

- Added changelog tooling (D18). `just changelog` runs git-cliff (`cliff.toml`)
  over the conventional commits since the last tag and prints a **draft** —
  commit bodies included, grouped under Keep a Changelog headings — whose
  entries are edited down by hand into the `## [Unreleased]` section above;
  `just changelog-range main..HEAD` does the same for a branch. Nothing writes
  to `CHANGELOG.md`. Conventional Commit format is now enforced rather than
  documented, in two deliberately unequal layers: a `commit-msg` hook
  (`conventional-pre-commit`, plus the `commit-msg` prek shim) rejects branch
  subjects it cannot parse, and `.github/workflows/pr-title.yml` additionally
  requires the PR title to start lowercase and not end in a period. The title
  is the one that matters — the `protect-main` ruleset allows only squash
  merges, so it becomes the commit subject on `main`, where
  `filter_unconventional` would otherwise drop an unparseable subject from
  every draft with no error. git-cliff installs in the devcontainer via
  `uv tool install git-cliff`.
- Switched the repository's squash-merge settings to `PR_TITLE` + `PR_BODY`
  (D19), so a merged PR's description — not a bullet dump of its commits —
  becomes the commit body on `main` and the raw material for a changelog entry.
  The `protect-main` ruleset gained a `required_status_checks` rule naming the
  `pr-title` check, so a non-conventional title now blocks the merge instead of
  only annotating it.
- Replaced the `commit-commands` plugin with project skills (`.claude/skills/`:
  `commit`, `commit-push-pr`, `clean-gone`), disabling the plugin in the repo's
  `.claude/settings.json` so the swap travels with the checkout. The plugin's
  generic "create a commit with an appropriate message" knew nothing of this
  repo's conventional format, work-package scopes, hooks that rewrite files
  mid-commit, or the squash-only PR title rule; its `clean_gone` also grepped
  `git branch -v` for `[gone]`, which that command never prints (only `-vv`
  does), so it matched nothing.

**Documentation**

- Seeded `CHANGELOG.md` and the decision log `docs/decisions.md` (D1–D18).
- Aligned `docs/mvp-build-plan.md` (stack, repository layout, WP-0),
  `docs/tech-stack.md`, `README.md`, `AGENTS.md`, `backend/README.md` and
  `frontend/README.md` with what was actually built, and corrected the stale
  references in WP-1…WP-9 (`packages/*` paths, `make` targets) so later work
  packages execute against the real repository.
- Folded `AGENTS.md` and `frontend/AGENTS.md` into `CLAUDE.md` and
  `frontend/CLAUDE.md`, which previously only `@`-included them, and dropped the
  `AGENTS.md` files — this project is worked on with Claude Code only (D17).
- Re-verified the WP-0 scaffold against the running stack and corrected the
  drift it exposed. `scripts/setup-repo.sh` did not reproduce the repository
  configuration D19 describes — it left `squash_merge_commit_title`/`_message`
  at their defaults and created a `protect-main` ruleset with no
  `required_status_checks` rule, so a fresh clone of this template got neither
  `PR_TITLE`+`PR_BODY` squash commits nor a blocking `pr-title` check; both are
  now applied, with a note that an existing ruleset is skipped rather than
  updated. In the docs: the Python pin is the *minor* (`3.14`, patch floats —
  3.14.4 in the devcontainer, 3.14.6 in the runtime image), not the "3.14.6"
  the plan and tech stack claimed; the release publishes three images
  (`api`, `mcp`, `frontend`), not two; WP-0's CI summary described the
  integration job's throwaway Postgres as a service container and the
  full-stack smoke job as "Docker Compose validation"; and WP-0 gained the
  repo-governance/dev-workflow item (devcontainer, prek hooks, squash-only
  ruleset, changelog tooling) that D16–D19 recorded but the plan never listed.
  Documented the first-run trap that `just init` + a pre-existing
  `postgres-data` volume produces: a new random `POSTGRES__PASSWORD` that
  Postgres ignores, surfacing as an api crash-loop on `InvalidPasswordError`.
