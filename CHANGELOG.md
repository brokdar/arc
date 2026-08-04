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
- Added the MCP server skeleton (`backend/app/mcp/`): a FastMCP 3 server run
  from the backend image as the `mcp` compose service
  (`python -m app.mcp.main`, streamable HTTP on :8001, behind Caddy's `/mcp*`).
  Every MCP request must present a bearer key from `MCP__API_KEYS`
  (`label:scope:key,...` with scope `read` or `write`); keys are parsed by the
  framework-free `app/mcp/auth.py` and compared with `secrets.compare_digest`
  by a `TokenVerifier` subclass, which puts the caller's label and scope on the
  request identity for per-tool scope checks in WP-8. The server refuses to
  start (exit 1) with no keys, so `MCP__API_KEYS` is required for
  `docker compose up`. The surface is one `ping` tool plus an unauthenticated
  `/health` route for the container healthcheck.
- Added single-user session-cookie authentication end to end. The credential
  store is one setting, `AUTH__PASSWORD_HASH` (a bcrypt hash — there is no
  user table); `POST /api/v1/auth/login` swaps it for a signed session cookie
  issued by Starlette's `SessionMiddleware` (`arc_session`, `SameSite=Lax`,
  14 days, `AUTH__SESSION__HTTPS_ONLY` once Caddy serves TLS), with
  `POST /api/v1/auth/logout` and an always-open `GET /api/v1/auth/session`
  alongside it. Everything else under `/api/v1` is mounted on a router
  carrying `Depends(require_session)` and a declared 401, so new routers are
  protected by default; `/health` stays open. Failed logins sleep ~0.3s to
  blunt guessing, and production now refuses to boot without
  `AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY` (the unused
  `AUTH__JWT__*` shell is gone). On the frontend the API client sends
  credentials, a `/login` page posts the password, and an `AuthGuard` client
  component bounces unauthenticated visitors off the protected pages. The
  Playwright `@fullstack` suite logs in once in a `setup` project and replays
  the session via `storageState`.
- Added the runtime data tree: `DATA__ROOT` (default `data`) with
  `inbox/`, `originals/`, `streams/`, `quarantine/` created on API startup and
  bind-mounted into the api container at `/app/data` (a one-shot `data-init`
  service hands the root-owned bind mount to the api's non-root user first).
- Made `just init` real (`scripts/bootstrap-env.sh`): it copies `.env.example`
  to a mode-600 `.env`, generates `POSTGRES__PASSWORD`,
  `AUTH__SESSION__SECRET_KEY` and both `MCP__API_KEYS`, and prompts (hidden,
  twice) for the login password it bcrypt-hashes into `AUTH__PASSWORD_HASH`.
  Values are substituted with Python rather than `sed`, so the `$` and `/` in
  bcrypt hashes survive; the script is idempotent (an existing `.env` is left
  alone) and degrades to a placeholder hash plus instructions when there is no
  terminal to prompt on. `just hash-password` prints a ready-to-paste
  single-quoted hash for rotating the password later. `just check` now also
  runs `api-check`, so contract drift is part of the one local gate, and the
  devcontainer installs `just` itself (`uv tool install rust-just`) — the
  whole workflow depended on a tool that was not in the image.
