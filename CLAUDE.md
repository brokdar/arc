# arc

Self-hosted, single-athlete training application (cycling + strength): a
FastAPI backend and a Next.js frontend joined by an end-to-end typed API
contract, plus an MCP server that exposes the same services to a coaching
agent. Every non-obvious choice is recorded where it binds — in the docstring
at the code site, in a test that fails when it is violated, or in
`.claude/rules/` — never in a document that can drift from the code.

## Architecture

- `backend/` — Python 3.14, FastAPI, SQLAlchemy 2 (async), Alembic, in-process
  APScheduler (`app/core/scheduler.py`). One uv project, two entrypoints: the
  API (`app/main.py`) and the MCP server (`python -m app.mcp.main`), shipped
  from the same Docker image.
  Layered layout: `app/domain/` (pure business rules — no I/O, no frameworks)
  → `app/persistence/` (ORM models, repositories, Alembic) → `app/services/`
  (use-cases) → `app/ingest/` → the adapters `app/api/` (routes, schemas) and
  `app/mcp/`, with `app/core/` cross-cutting (config, logging, exceptions,
  scheduler) and usable from anywhere. Boundaries are enforced by
  import-linter (`uv run lint-imports`, contracts in `backend/pyproject.toml`),
  which fails CI, `just lint` and pre-push — the domain-purity contract names
  forbidden packages explicitly, so adding an import there is a build error,
  not a review comment; a unit test (`test_domain_purity_contract`) keeps that
  list in step with `[project].dependencies`, so every new dependency has to be
  classified. Only `[project].dependencies` are checked — a dev-only tool
  (`hypothesis`) needs no entry.
- `frontend/` — Next.js (App Router), React, TypeScript, Tailwind 4, shadcn/ui
  (Base UI primitives — components use `render={...}`, NOT Radix `asChild`).
  See `frontend/CLAUDE.md`: this Next.js major differs from older ones, so read
  `node_modules/next/dist/docs/` before writing app code.
- **MCP server** (`backend/app/mcp/`) — FastMCP 3 over streamable HTTP on
  :8001. Every request carries a bearer key from `MCP__API_KEYS`
  (`label:scope[+scope]:key,...`, scopes `read`/`write`, e.g.
  `coach:read+write:<hex>` — `write` does not imply `read`); the label and
  scope set land on
  the authenticated identity for per-tool checks and audit rows. Tools delegate
  to `app/services/` — the same layer `app/api/` uses. No logic lives in an
  adapter, and `api` and `mcp` may not import each other.
- **Auth** — single user, no user table: one bcrypt hash in
  `AUTH__PASSWORD_HASH` exchanged at login for a signed session cookie.
  Everything under `/api/v1` except `/auth/*` hangs off a router carrying
  `Depends(require_session)` (`app/api/deps.py`), so **new routers are
  protected by default**; `/health` stays open for probes.
- **Reverse proxy** — `caddy/Caddyfile` fronts the stack as one origin:
  `/api/*` and `/health` → api, `/mcp*` → mcp, everything else → frontend. The
  compose frontend build therefore sets an empty `NEXT_PUBLIC_API_BASE_URL`
  (same-origin calls, no CORS).
- **API contract**: backend OpenAPI → `frontend/generated/api/` (committed,
  never hand-edited) → consumed via `openapi-fetch` / `$api` react-query
  hooks. After changing any backend endpoint or schema, run `just api-sync`
  and commit the result — CI fails on drift.

## Commands

Everything goes through the `justfile`; `just` alone lists the recipes.
The one recipe needing an environment variable: `E2E_PASSWORD=... just smoke`.

## Conventions

- **Package managers**: `uv` for Python, `bun` for the frontend. Never npm/npx/pnpm/yarn.
- **Backend layering**: `app/domain/` is pure — no SQLAlchemy, FastAPI,
  Starlette, httpx, structlog, pydantic-settings, or any other layer including
  `app/core/`. Imports point inward only
  (`api | mcp` → `ingest` → `services` → `persistence` → `domain`).
  import-linter enforces this; if a contract fails, fix the dependency
  direction rather than the contract.
- **Backend**: endpoints stay thin (HTTP only); business logic in services;
  data access in repositories. MCP tools are equally thin — they call the same
  services, never a repository or another adapter. Services raise `AppError`
  subclasses (`app/core/exceptions.py`) — never `HTTPException` outside
  endpoints. New settings go in `app/core/config.py` AND `.env.example` (a test
  enforces this), using nested `__` keys, unprefixed. Service wiring lives in
  the service (`X.from_session(session)`), not in a route — `app.mcp` cannot
  import `app.api`.
- **Transactions**: the **service** commits (`persistence.db.commit`) at the
  end of a mutating use-case; `get_session` and `session_scope()` only roll
  back and close. Non-HTTP callers (MCP, scheduler, ingest) use
  `session_scope()`; tests bind it with `set_session_factory` (fixtures
  `session_factory` / `db_session`).
- **Models**: timestamps are `UtcDateTime` (aware UTC on SQLite *and*
  Postgres), JSON is `JSONColumn` (JSONB on Postgres), enums are
  `enum_column(X)` (non-native), ids default to `uuid.uuid7` — all from
  `app/persistence/types.py`. Constraints are named by the `Base` metadata
  convention. Modules under `app/persistence/` are swept by `load_models()`;
  never hand-list them in `alembic/env.py` or a conftest.
- **Actor**: every mutating service method takes `actor: Actor`
  (`app/domain/actor.py`) — `ActorDep` in the API, `app.mcp.identity`
  (`current_actor()` / `require_scope()`) in MCP tools.
- **Auth**: protected routes come from the guarded router, not per-route
  dependencies. In backend tests, `client` is logged in and `anon_client` is
  not — use `anon_client` to assert a 401. In `.env`, `AUTH__PASSWORD_HASH`
  must be **single-quoted**: bcrypt hashes are full of `$`, which .env parsers
  and shells expand.
- **Frontend**: client components use `$api.useQuery/useMutation` from
  `lib/api/client.ts`. Unit tests mock the network with MSW
  (`tests/mocks/handlers.ts`), never the client module.
- **Migrations**: every model change ships with an Alembic migration in the
  same PR. Integration tests build the schema via `alembic upgrade head` and
  run `alembic check` — model/migration drift fails CI.
- **Decisions live where they bind.** When you resolve an ambiguity or make a
  non-obvious choice, the reasoning goes in the **docstring or comment at the
  code site it governs** — what was chosen, what it displaced, why — and, where
  a later edit could violate it silently, a **test that fails when it does**. A
  convention spanning a class of files goes in `.claude/rules/<name>.md` with a
  `paths:` key; a machine-catchable mistake goes in a hook. What a change is
  *for* goes in the PR description, which squash-merge makes the commit body on
  `main`, so `git log` is the narrative record.
- **Commits, PRs, merges**: commit subjects follow Conventional Commits, scoped
  by area/subsystem (`feat(mcp): ...`; historic commits used work packages,
  which ended with the MVP build). `main` is **squash-only** (the
  `protect-main` ruleset allows no other method, and PRs are required) with
  `squash_merge_commit_title = PR_TITLE` and `squash_merge_commit_message =
  PR_BODY`, so a merged PR becomes one commit whose subject is the **PR title**
  and whose body is the **PR description**. Two lint layers, deliberately
  unequal: the `commit-msg` hook only rejects subjects it cannot *parse* (scope
  is not required; `Feat(WP-1): Add Thing.` passes), while
  `.github/workflows/pr-title.yml` additionally enforces lowercase-start and
  no-trailing-period on the title — the text that becomes the changelog entry.
  The eleven-type list is duplicated in `.pre-commit-config.yaml`, `cliff.toml`
  and `pr-title.yml`; change all three together. See the `commit` and
  `commit-push-pr` skills.
- **Changelog**: `CHANGELOG.md` is hand-curated Keep a Changelog, and no tool
  writes to it. `just changelog` (git-cliff, `cliff.toml`) prints a *draft* from
  conventional commits with bodies included; edit it down and merge the entries
  into the existing `## [Unreleased]` section — the draft prints that heading
  itself. `filter_unconventional` is on, so an unparseable subject is dropped
  from the draft silently; git-cliff reports only a count on stderr, and
  `-vv` names them.

## Testing strategy (write tests at the cheapest layer that catches the bug)

1. **Backend API tests** (`tests/unit/`, in-memory SQLite, no services):
   the default home for backend logic — test through HTTP, not internals.
2. **Backend integration** (`tests/integration/`, real Postgres via
   `just test-int`): dialect-specific behavior (JSONB, arrays, upserts,
   constraints) and the migration chain.
3. **Frontend component tests** (Vitest + MSW): mock the network with the
   TYPED handlers in `tests/mocks/handlers.ts` (openapi-msw) — never mock
   `lib/api/client.ts`. Untyped responses only via `http.untyped` for
   infra-failure simulation. **A fixture must be a payload the real API could
   produce, and a handler must honour the request**: type-checking proves the
   shape, not the arithmetic, so derived numbers are computed by running the
   backend domain over the same document rather than typed in, fields the
   service derives from one another agree (`title` non-null iff `workout_id`
   is), and a mutating handler echoes what it was sent. A canned reply cannot
   fail when the form drops a field, and a test written against an impossible
   fixture agrees with the fixture instead of the application.
4. **UI e2e** (`frontend/e2e/*.spec.ts`, no backend): user flows against a
   production build with the API mocked/absent.
5. **Full-stack smoke** (`@fullstack` tests, `just smoke`): wiring only —
   CORS, env baking, Docker networking, migrations-on-boot, login through the
   real UI. Keep under ~5 tests; if a smoke failure could have been caught
   lower, add the lower-layer test instead of growing this suite.
6. **Schemathesis** (CI): fuzzes the API from the OpenAPI schema. When it
   finds something, fix it AND pin the case as a unit test (see the
   "found by Schemathesis" tests in `test_auth.py` and `test_athlete_api.py`).
   An endpoint that refuses schema-valid input by design (a 405, a domain-rule
   422) is narrowed per operation in `backend/schemathesis.toml`, never by
   excluding a check globally. Reproduce locally: run the API against the
   compose test database, log in for a cookie, then
   `uvx schemathesis run http://localhost:8000/openapi.json --max-examples 100
   --header "Cookie: …" --exclude-checks negative_data_rejection,ignored_auth`
   from `backend/` (the config is picked up from the working directory).
7. **Property tests** (hypothesis, backend unit): for pure domain code whose
   invariants are stated more usefully than its outputs are enumerated — see
   `test_domain_zones.py`.

## Improving this repo

Recurring friction is a defect in the tooling. The trigger is **the second
time**: hitting the same correction, lookup, or multi-step dance twice — in
this session or an earlier one — is the signal to make the fix durable. Once
is not a pattern.

Route it to the cheapest artifact: a re-derived procedure → a skill; a
convention that applies only to certain files → `.claude/rules/<name>.md` with
a `paths:` frontmatter key (gitignore-style globs, comma-separated or a YAML
list, matched repo-relative; a rule without it loads every session, which is
what this file is for); a machine-catchable mistake → a hook in
`.claude/hooks/` or pre-commit; a command you had to reconstruct → a `justfile`
recipe; a fact every session needs up front → a line in this file.

Build the fix directly when it happens — the operator has delegated this — and
note what changed in the report. One at a time, no end-of-session improvement
retrospectives, and never a change whose effect is to widen permissions or
weaken a guard.
