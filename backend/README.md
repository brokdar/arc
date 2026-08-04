# Backend

FastAPI service for arc — and, from the same project and the same Docker
image, the MCP server that exposes the same use-cases to a coaching agent.

## Layout

```
app/
  main.py            app factory + lifespan (scheduler, data dirs, middleware)
  core/              cross-cutting, usable from any layer: config, logging,
                     exceptions, scheduler (in-process APScheduler)
  domain/            pure business rules — pydantic/stdlib only, no I/O
  persistence/       db.py (engine, session), ORM models + repositories,
                     alembic/ migrations (async env)
  services/          use-cases; orchestrate repositories, raise AppError
  ingest/            activity-file ingestion pipeline
  api/               HTTP adapter: routes/, schemas/, deps, pagination, validation
  mcp/               MCP adapter (sibling of api/, independent of it)
tests/
  unit/              fast, no external services (CI + pre-push)
  integration/       against a real Postgres (CI, docker compose)
```

Dependencies point one way only — `api | mcp` → `ingest` → `services` →
`persistence` → `domain` — and `app.domain` may not import frameworks or
outer layers at all. This is enforced by import-linter (`uv run lint-imports`,
contracts in `pyproject.toml`), which runs in CI, `just lint` and pre-push.

`items` is a worked example spread across the layers (`persistence/items.py`,
`services/items.py`, `api/schemas/items.py`, `api/routes/items.py`) — delete
it once you have a real one.

## Commands

```bash
uv sync                  # install deps
uv run fastapi dev app/main.py
uv run python -m app.mcp.main   # MCP server on :8001 (needs MCP__API_KEYS)
uv run pytest -n auto    # unit tests
uv run ruff check . && uv run ruff format .
uv run pyrefly check     # type checking
uv run lint-imports      # architecture boundaries
uv run alembic upgrade head
```

From the repo root, `just dev-api`, `just dev-mcp`, `just test`, `just lint`,
`just typecheck` and `just check` wrap these (and start Postgres first where
it is needed). The two that talk to Postgres also need
`POSTGRES__HOST=localhost` in front of them when run by hand: `.env` holds the
compose network name `db`, which only resolves inside Docker (the `dev-*` and
`db-*` recipes set it for you).

## Configuration

Settings live in `app/core/config.py` and are read from the repo-root `.env`
with nested double-underscore keys (`POSTGRES__HOST` → `settings.postgres.host`).
The path is anchored on the package location, not the working directory, so
processes started from `backend/` see it too; a `.env` in the working directory
takes precedence over it, and real environment variables over both. Every
setting must also appear in `.env.example` — `test_env_example_completeness`
fails otherwise. `just init` writes a working `.env`. Tests never read it (see
`tests/conftest.py`).

## Auth

One human user, no user table: `AUTH__PASSWORD_HASH` (a bcrypt hash) is the
whole credential store. `POST /api/v1/auth/login` verifies it and issues a
signed session cookie via Starlette's `SessionMiddleware`; `/auth/logout` and
`/auth/session` sit beside it and are always open.

Everything else under `/api/v1` is mounted on a router carrying
`Depends(require_session)` (`app/api/deps.py`) plus a declared 401, so **a new
router is protected unless it is deliberately mounted elsewhere**. `/health`
stays open for container probes. In production the app refuses to boot without
`AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY`, or with the default
`POSTGRES__PASSWORD` — and `docker-compose.yml` pins `ENVIRONMENT=production`
on the `api` and `mcp` services, so that guard always runs in the shipped
stack.

The MCP server authenticates separately: every request presents a bearer key
from `MCP__API_KEYS` (comma-separated `label:scope:key`, scope `read` or
`write`), parsed by the framework-free `app/mcp/auth.py` and compared in
constant time. With no keys — or with a key under 32 characters, one still
holding the `change-me` placeholder, or two entries sharing a key or a label —
the server exits 1 rather than serve an unauthenticated or ambiguous tool
surface. Its `/health` route is the one unauthenticated endpoint.

## Migrations

Every model change ships with a migration in the same commit.

```bash
just db-revision "add anchors table"   # autogenerate from model changes
just db-upgrade                        # apply to the dev database
```

Migrations live in `app/persistence/alembic/versions/`. Review the generated
file — autogenerate misses server defaults, constraint renames and data
migrations. Integration tests build the schema from scratch with
`alembic upgrade head` and then run `alembic check`, so model/migration drift
fails CI. The `api` container also runs `alembic upgrade head` on boot.

## Tests

```bash
uv run pytest -n auto              # unit tests: in-memory SQLite, no services
bash ../scripts/run-integration-tests.sh   # or: just test-int
```

Unit tests drive the app through HTTP with `AsyncClient`. Two fixtures:
`client` is authenticated (it logs in during setup), `anon_client` is not —
use it to assert that a route rejects unauthenticated callers. Coverage is
enforced at 80%. Integration tests need Docker: they spin up a real Postgres
(`docker-compose.test.yml`) for dialect-specific behavior and the migration
chain.
