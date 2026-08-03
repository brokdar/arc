# Backend

FastAPI service for __PROJECT_NAME__.

## Layout

```
app/
  main.py            app factory + lifespan
  core/              cross-cutting: config, db, logging, exceptions, pagination
  domains/<name>/    one package per business domain:
    endpoints.py     FastAPI router (thin — HTTP concerns only)
    service.py       business logic
    repository.py    data access (SQLAlchemy)
    models.py        ORM models
    schemas.py       Pydantic request/response schemas
  worker/            ARQ background worker + tasks
  alembic/           migrations (async env)
tests/
  unit/              fast, no external services (CI + pre-push)
  integration/       against a real Postgres (CI, docker compose)
```

The `items` domain is a worked example demonstrating every layer — delete it
once you have a real domain.

## Commands

```bash
uv sync                  # install deps
uv run fastapi dev app/main.py
uv run pytest -n auto    # unit tests
uv run ruff check . && uv run ruff format .
uv run pyrefly check     # type checking
uv run alembic upgrade head
```
