# arc

Self-hosted, single-athlete training application (cycling + strength): a
FastAPI backend and a Next.js frontend joined by an end-to-end typed API
contract, plus an MCP server that exposes the same services to a coaching
agent. `docs/mvp-build-plan.md` is the plan being executed; every non-obvious
choice is recorded in `docs/decisions.md`.

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
  not a review comment. `items` is a worked example spread across the layers;
  WP-1 deletes it.
- `frontend/` — Next.js (App Router), React, TypeScript, Tailwind 4, shadcn/ui
  (Base UI primitives — components use `render={...}`, NOT Radix `asChild`).
  See `frontend/AGENTS.md`: this Next.js major differs from older ones, so read
  `node_modules/next/dist/docs/` before writing app code.
- **MCP server** (`backend/app/mcp/`) — FastMCP 3 over streamable HTTP on
  :8001. Every request carries a bearer key from `MCP__API_KEYS`
  (`label:scope:key,...`, scope `read` or `write`); the label and scope land on
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

| Task | Command |
|---|---|
| Write `.env` (random secrets + your login password) | `just init` |
| Print an `AUTH__PASSWORD_HASH` line for a new password | `just hash-password` |
| Postgres only, in Docker | `just infra` |
| API dev server (hot reload, :8000) | `just dev-api` |
| MCP dev server (:8001, needs `MCP__API_KEYS`) | `just dev-mcp` |
| Frontend dev server (:3000) | `just dev-web` |
| Full stack in Docker → http://localhost | `just up` / `just down` |
| Format backend + frontend | `just format` |
| Lint backend + frontend (incl. import-linter) | `just lint` |
| Type-check backend + frontend (pyrefly, tsgo) | `just typecheck` |
| Unit tests, backend + frontend | `just test` |
| Backend integration tests (real Postgres) | `just test-int` |
| Playwright UI e2e (no backend) | `just e2e` |
| Full-stack smoke suite through Caddy | `E2E_PASSWORD=... just smoke` |
| **All CI checks locally** | `just check` (lint + typecheck + test + api-check) |
| Apply migrations | `just db-upgrade` |
| New migration | `just db-revision "add items table"` |
| Regenerate frontend API types | `just api-sync` |
| Fail on API-contract drift | `just api-check` |

Single-project equivalents, when a full recipe is more than you need:
`cd backend && uv run pytest -n auto`, `uv run ruff check .`,
`uv run pyrefly check`, `uv run lint-imports`;
`cd frontend && bun run lint:fix`, `bun run type-check`, `bun run test`.

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
  enforces this), using nested `__` keys, unprefixed.
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
- **Decisions**: when you resolve an ambiguity or depart from
  `docs/mvp-build-plan.md`, append an entry to `docs/decisions.md` (what,
  what it displaced, why). Entries are append-only.

## Testing strategy (write tests at the cheapest layer that catches the bug)

1. **Backend API tests** (`tests/unit/`, in-memory SQLite, no services):
   the default home for backend logic — test through HTTP, not internals.
2. **Backend integration** (`tests/integration/`, real Postgres via
   `just test-int`): dialect-specific behavior (JSONB, arrays, upserts,
   constraints) and the migration chain.
3. **Frontend component tests** (Vitest + MSW): mock the network with the
   TYPED handlers in `tests/mocks/handlers.ts` (openapi-msw) — never mock
   `lib/api/client.ts`. Untyped responses only via `http.untyped` for
   infra-failure simulation.
4. **UI e2e** (`frontend/e2e/*.spec.ts`, no backend): user flows against a
   production build with the API mocked/absent.
5. **Full-stack smoke** (`@fullstack` tests, `just smoke`): wiring only —
   CORS, env baking, Docker networking, migrations-on-boot, login through the
   real UI. Keep under ~5 tests; if a smoke failure could have been caught
   lower, add the lower-layer test instead of growing this suite.
6. **Schemathesis** (CI): fuzzes the API from the OpenAPI schema. When it
   finds something, fix it AND pin the case as a unit test (see the
   "found by Schemathesis" tests in `test_items_api.py` and
   `test_auth.py`).
