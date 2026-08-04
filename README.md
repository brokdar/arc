# arc

A self-hosted training application for a single athlete — cycling, strength and
core — where **every planned session carries recorded intent** (what it is for,
what it should achieve, what to watch for) and every completed session is
ingested from its activity file, matched to the plan, and scored against that
intent. A log of unlabelled activities cannot tell you whether you are
improving or just riding more; a log of intent-linked, scored sessions can. An
LLM coaching agent works the plan alongside you through a guarded MCP surface,
but the app is deterministic first: kill the agent and every screen and every
computed value still works.

Status: scaffolding (WP-0) complete — the stack runs end to end with a login,
a health-checked API, an MCP server and CI. The training domain itself is being
built work package by work package; see `docs/mvp-build-plan.md`.

## Quick start

Needs Docker and [just](https://just.systems) (`uv tool install rust-just`);
the devcontainer installs both.

```bash
git clone <this repo> arc && cd arc
just init   # writes .env: random secrets + the login password you pick
just up     # full stack in Docker → http://localhost
```

Open http://localhost and log in with the password you chose. There is one
user and no sign-up: `just init` stored a bcrypt hash of that password in
`.env` as `AUTH__PASSWORD_HASH`, and nothing else. To change it later, run
`just hash-password` and paste the line it prints over the one in `.env`.

> **Re-running `just init` on a machine that already ran the stack?** It mints
> a *new* random `POSTGRES__PASSWORD`, but Postgres only applies that when the
> cluster is first created — the existing `postgres-data` volume keeps the old
> one and the api crash-loops on `InvalidPasswordError`. Wipe the volume too:
> `docker compose down -v && just up`.

## Services

| Service | URL | Notes |
|---|---|---|
| Caddy | http://localhost | reverse proxy — one origin for UI + `/api/*` |
| Frontend | http://localhost:3000 | Next.js App Router |
| API | http://localhost:8000 | OpenAPI docs at `/docs` |
| MCP | http://localhost/mcp | FastMCP server (loopback :8001 direct) |
| Postgres | localhost:5432 | credentials in `.env` |

With `just up`, use http://localhost: Caddy routes `/api/*` and `/health` to
the API, `/mcp*` to the MCP server, and everything else to the frontend, so
the browser never makes a cross-origin request. Set `CADDY_SITE_ADDRESS` to a
hostname for automatic HTTPS. Runtime files live in `./data`
(`inbox/`, `originals/`, `streams/`, `quarantine/`), bind-mounted into the API
container.

MCP clients authenticate with a bearer key from `MCP__API_KEYS` — comma-
separated `label:scope:key` entries, scope `read` or `write`. `just init`
generates a `coach` (write) and a `readonly` (read) key; the server refuses to
start without any, and rejects keys under 32 characters, keys still holding the
`change-me` placeholder, and two entries sharing a key.

## Development

The devcontainer (`.devcontainer/`) installs uv, bun, just, prek, Playwright
browsers and both projects' dependencies. Outside it, you need uv, bun, just
and Docker.

For day-to-day work, run the apps on the host with hot reload and only
Postgres in Docker:

```bash
just infra     # Postgres only
just dev-api   # FastAPI with hot reload → http://localhost:8000
just dev-web   # Next.js dev server      → http://localhost:3000
just dev-mcp   # MCP server              → http://localhost:8001
```

```bash
just check        # lint + typecheck + unit tests + API contract drift (what CI runs)
just test-int     # backend integration tests + migration checks (real Postgres)
just e2e          # Playwright UI tests (no backend needed)
just db-revision "add widgets table"   # autogenerate a migration
just db-upgrade   # apply migrations
just api-sync     # regenerate frontend API types from the backend OpenAPI schema

# full Docker stack + @fullstack wiring smoke tests; the password is the one
# you gave `just init`, because the suite logs in through the real UI
E2E_PASSWORD=... just smoke
```

`just` on its own lists every recipe. Git hooks are managed by
[prek](https://prek.j178.dev/) (installed by the devcontainer, or run
`prek install -t pre-commit -t pre-push` once): cheap checks run on commit,
type checking and unit tests run on push.

## Layout

```
backend/    FastAPI API + MCP server — one uv project, one image, two entrypoints
  app/domain/       pure business rules: no I/O, no frameworks, no other layers
  app/persistence/  ORM models, repositories, Alembic migrations
  app/services/     use-cases — the layer api/ and mcp/ both consume
  app/ingest/       activity-file pipeline (from WP-4)
  app/api/          HTTP adapter: routes, schemas, session guard
  app/mcp/          FastMCP adapter — independent of api/
  app/core/         cross-cutting: config, logging, exceptions, scheduler
frontend/   Next.js app — generated/api/ holds the OpenAPI-derived types (never edit by hand)
caddy/      Caddyfile for the reverse proxy that fronts the stack
scripts/    cross-cutting automation (env bootstrap, API type generation, integration tests)
data/       runtime tree (gitignored): inbox/, originals/, streams/, quarantine/
docs/       build plan, tech stack, decision log, product description
.github/    CI workflows — path-filtered, no per-project edits needed
```

Imports point one way only — `api | mcp` → `ingest` → `services` →
`persistence` → `domain` — and `app/domain` may not import frameworks or outer
layers at all. This is enforced by import-linter in CI, `just lint` and
pre-push, so the architecture fails the build rather than drifting.

## Documentation

| Where | What |
|---|---|
| `docs/mvp-build-plan.md` | The plan being executed: invariants, stack, work packages WP-0…WP-9 |
| `docs/tech-stack.md` | Every dependency choice with its rationale, upgrade watch list |
| `docs/decisions.md` | Running decision log — what was chosen, over what, and why |
| `docs/training-application-description-v2.md` | Full product description (beyond the MVP) |
| `docs/training-application-delivery-plan.md` | Phased delivery plan |
| `CLAUDE.md` | Conventions and commands for humans and coding agents |
| `backend/README.md`, `frontend/README.md` | Per-project detail |

## Releases

Push a semver tag to publish all three Docker images to GHCR:

```bash
git tag v1.0.0 && git push --tags
# → ghcr.io/<owner>/<repo>/api:1.0.0
#   ghcr.io/<owner>/<repo>/mcp:1.0.0        (same image as api, other entrypoint)
#   ghcr.io/<owner>/<repo>/frontend:1.0.0
```

Set `NEXT_PUBLIC_API_BASE_URL` / `NEXT_PUBLIC_API_PATH` as repository
**variables** (Settings → Secrets and variables → Actions) so release builds
point at your real API — the values are baked in at build time. Leave
`NEXT_PUBLIC_API_BASE_URL` empty when the frontend is served behind the
bundled Caddy proxy (same origin).
