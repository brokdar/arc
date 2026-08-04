# Backend

FastAPI service for arc.

## Layout

```
app/
  main.py            app factory + lifespan
  core/              cross-cutting, usable from any layer: config, logging,
                     exceptions, scheduler (in-process APScheduler)
  domain/            pure business rules — pydantic/stdlib only, no I/O
  persistence/       db.py (engine, session), ORM models + repositories,
                     alembic/ migrations (async env)
  services/          use-cases; orchestrate repositories, raise AppError
  ingest/            activity-file ingestion pipeline
  api/               HTTP adapter: routes/, schemas/, pagination, validation
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
uv run pytest -n auto    # unit tests
uv run ruff check . && uv run ruff format .
uv run pyrefly check     # type checking
uv run lint-imports      # architecture boundaries
uv run alembic upgrade head
```
