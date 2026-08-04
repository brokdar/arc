# arc

One-sentence description of what this project does.

## Quick start

```bash
cp .env.example .env   # done automatically by `just init`
just up                # full stack in Docker
# or for development:
just infra             # Postgres only
just dev-api           # FastAPI with hot reload → http://localhost:8000
just dev-web           # Next.js dev server     → http://localhost:3000
```

## Services

| Service | URL | Notes |
|---|---|---|
| Frontend | http://localhost:3000 | Next.js App Router |
| API | http://localhost:8000 | OpenAPI docs at `/docs` |
| Postgres | localhost:5432 | credentials in `.env` |

## Development

```bash
just check        # lint + typecheck + unit tests (what CI runs)
just test-int     # backend integration tests + migration checks (real Postgres)
just e2e          # Playwright UI tests (no backend needed)
just smoke        # full Docker stack + @fullstack wiring smoke tests
just db-revision "add widgets table"   # autogenerate a migration
just db-upgrade   # apply migrations
just api-sync     # regenerate frontend API types from the backend OpenAPI schema
```

Git hooks are managed by [prek](https://prek.j178.dev/) (installed by the
devcontainer, or run `prek install -t pre-commit -t pre-push` once): cheap
checks run on commit, type checking and unit tests run on push.

## Releases

Push a semver tag to publish both Docker images to GHCR:

```bash
git tag v1.0.0 && git push --tags
# → ghcr.io/<owner>/<repo>/api:1.0.0 and ghcr.io/<owner>/<repo>/frontend:1.0.0
```

Set `NEXT_PUBLIC_API_BASE_URL` / `NEXT_PUBLIC_API_PATH` as repository
**variables** (Settings → Secrets and variables → Actions) so release builds
point at your real API — the values are baked in at build time.

## Layout

```
backend/    FastAPI app — layered: app/{domain,persistence,services,ingest,api,mcp} + cross-cutting app/core
frontend/   Next.js app — generated/api/ holds the OpenAPI-derived types (never edit by hand)
scripts/    cross-cutting automation (API type generation, integration tests)
.github/    CI workflows — path-filtered, no per-project edits needed
```
