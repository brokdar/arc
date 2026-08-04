# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### WP-0

Scaffolding is in progress. Later phases append to this section.

- Repo hygiene: tracked `docs/`, removed orphaned build artifacts
  (`packages/`, root `node_modules/`), ignored `/data/` and `.schemathesis/`,
  bumped the `ruff-pre-commit` hook to v0.16.1 to match the backend lockfile,
  and seeded this changelog plus `docs/decisions.md`.
- Upgraded the backend from Python 3.13 to 3.14 (per D4) across
  `pyproject.toml`, `.python-version`, `pyrefly.toml`, both `Dockerfile`
  stages, and the devcontainer image, and relocked `uv.lock`.
- Removed the ARQ worker and its Redis service (per D5) and replaced them with
  an in-process APScheduler started by the API lifespan
  (`backend/app/core/scheduler.py`); dropped the `redis` and `worker` services
  from Compose, the `dev-worker` recipe, and the `REDIS__URL` setting.
- Restructured the backend from `app/domains/<domain>/` into layered modules —
  `app/domain` (pure, filled in by WP-1), `app/persistence` (db, ORM models,
  repositories, Alembic), `app/services`, `app/ingest` and `app/mcp`
  skeletons, and `app/api` (routes, schemas, pagination, validation) — with
  `app/core` reduced to genuinely cross-cutting code. Boundaries are now
  enforced by import-linter contracts (`uv run lint-imports`) wired into CI,
  `just lint` and pre-push. The OpenAPI contract is unchanged.
- Upgraded Postgres from 17 to 18 (dev stack and the integration-test
  database). Postgres 18 moved the image's `VOLUME` to `/var/lib/postgresql`
  (`PGDATA` is now `/var/lib/postgresql/18/docker`), so the `postgres-data`
  volume and the test tmpfs mount that path instead of `.../data`. Existing
  local volumes hold a 17 cluster and must be recreated
  (`docker compose down -v`).
- Added a Caddy reverse proxy (`caddy/Caddyfile`, `caddy` service on :80/:443)
  that fronts the whole stack from one origin: `/api/*` and `/health` to the
  API, `/mcp*` to the (not yet existing) MCP server, everything else to the
  frontend. `CADDY_SITE_ADDRESS` defaults to `:80` (plain HTTP); set a
  hostname for automatic HTTPS. The frontend is now built with an empty
  `NEXT_PUBLIC_API_BASE_URL`, so the browser calls the API same-origin
  through the proxy, and the `@fullstack` smoke suite runs against
  `http://localhost`.
- Added the runtime data tree: `DATA__ROOT` (default `data`) with
  `inbox/`, `originals/`, `streams/`, `quarantine/` created on API startup and
  bind-mounted into the api container at `/app/data` (a one-shot `data-init`
  service hands the root-owned bind mount to the api's non-root user first).
