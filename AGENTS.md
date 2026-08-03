# __PROJECT_NAME__

Full-stack monorepo: FastAPI backend + Next.js frontend with an end-to-end
typed API contract.

## Architecture

- `backend/` — Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, ARQ worker.
  Domain-driven layout: `app/domains/<domain>/{endpoints,service,repository,models,schemas}.py`,
  cross-cutting code in `app/core/`. The `items` domain is a worked example.
- `frontend/` — Next.js (App Router), React, TypeScript, Tailwind 4, shadcn/ui
  (Base UI primitives — components use `render={...}`, NOT Radix `asChild`).
- **API contract**: backend OpenAPI → `frontend/generated/api/` (committed,
  never hand-edited) → consumed via `openapi-fetch` / `$api` react-query
  hooks. After changing any backend endpoint or schema, run
  `bash scripts/generate-api-types.sh` and commit the result — CI fails on drift.

## Commands

| Task | Command |
|---|---|
| All CI checks locally | `just check` |
| Backend lint/format | `cd backend && uv run ruff check . && uv run ruff format .` |
| Backend types | `cd backend && uv run pyrefly check` |
| Backend unit tests | `cd backend && uv run pytest -n auto` |
| Backend integration tests | `bash scripts/run-integration-tests.sh` |
| Frontend lint+format | `cd frontend && bun run lint:fix` |
| Frontend types | `cd frontend && bun run type-check` |
| Frontend tests | `cd frontend && bun run test` |
| New migration | `cd backend && uv run alembic revision --autogenerate -m "..."` |
| Regenerate API types | `bash scripts/generate-api-types.sh` |

## Conventions

- **Package managers**: `uv` for Python, `bun` for the frontend. Never npm/npx/pnpm/yarn.
- **Backend**: endpoints stay thin (HTTP only); business logic in services;
  data access in repositories. Services raise `AppError` subclasses
  (`app/core/exceptions.py`) — never `HTTPException` outside endpoints.
  New settings go in `app/core/config.py` AND `.env.example` (a test enforces this).
- **Frontend**: client components use `$api.useQuery/useMutation` from
  `lib/api/client.ts`. Unit tests mock the network with MSW
  (`tests/mocks/handlers.ts`), never the client module.
- **Migrations**: every model change ships with an Alembic migration in the
  same PR. Integration tests build the schema via `alembic upgrade head` and
  run `alembic check` — model/migration drift fails CI.

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
   CORS, env baking, Docker networking, migrations-on-boot. Keep under ~5
   tests; if a smoke failure could have been caught lower, add the
   lower-layer test instead of growing this suite.
6. **Schemathesis** (CI): fuzzes the API from the OpenAPI schema. When it
   finds something, fix it AND pin the case as a unit test (see the
   "found by Schemathesis" tests in `test_items_api.py`).
