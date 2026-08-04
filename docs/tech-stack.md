# Tech Stack

Versions verified against PyPI/npm/official release pages on **2026-08-01**, reconciled with what WP-0 actually installed on **2026-08-04**. Recommendation: your preference (Python + Next.js) is the right call — no dissent, only refinements. Rationale follows each section; upgrade watch list at the bottom.

Rows marked **in use** are installed and locked today (`backend/uv.lock`, `frontend/bun.lock` — the lockfile is the pin, the manifests carry `>=` floors). Everything else is a forward-looking choice that arrives with the work package that needs it. Where WP-0 departed from the original recommendation, the reason is in `docs/decisions.md`.

---

## Backend — Python

| Component | Version | Role / rationale |
|---|---|---|
| **Python** | **3.14.6** — in use | Mature (6 patch releases in). Free-threaded build exists but stays opt-in — not needed for this workload. 3.15 lands Oct 2026; don't wait. Upgraded from the template's 3.13 in WP-0 (D4): `pyproject.toml`, `.python-version`, `pyrefly.toml`, both Dockerfile stages, devcontainer image. |
| **FastAPI** | 0.141.1 — in use | API layer + auto-OpenAPI (feeds the typed frontend client and the MCP surface). Still 0.x with fast cadence — the lockfile holds the exact version. |
| **Pydantic** | 2.13.4 + **pydantic-settings 2.14.2** — in use | Domain models, validation, settings. Settings are unprefixed with nested `__` delimiters (`POSTGRES__HOST` → `settings.postgres.host`); a backend test keeps `.env.example` in sync with the model. |
| **SQLAlchemy** | 2.0.51 (asyncio) + **Alembic 1.18.5** — in use | ORM + migrations, async throughout on asyncpg 0.31. 2.1 is in beta — stay on 2.0, write 2.1-clean code (fully typed, `Mapped[]`-style only). Migrations are first-class from day one: the versioning doctrine lives in the schema, and CI runs `alembic check` so model/migration drift fails the build. |
| **PostgreSQL** | **18** (`postgres:18-alpine`) — in use | Single database for everything at MVP: domain, coach memory, audit, scores. EOL 2030. Note 18 moved the image's `VOLUME` to `/var/lib/postgresql` (`PGDATA` is `.../18/docker`), so the compose volume mounts that path — a pre-18 local volume must be recreated. |
| TimescaleDB | 2.27.x — **deferred, not in MVP** | Optional later for per-second streams if plain PG hurts. License fine for self-hosting (TSL Community). Decision point at MMP when analysis parity lands — see storage note below. |
| **uv** | in use | Package/project manager, lockfile, Python toolchain. De-facto standard. Also installs the `just` task runner in the devcontainer (`uv tool install rust-just`) and runs `uvx schemathesis` in CI. |
| **ruff** | 0.16.1 — in use | Lint + format (replaces black/isort/flake8). Line length **88** (the template's default, kept — D7), google-style docstrings, a broad rule selection including bandit/bugbear/pathlib/async rules. |
| **pyrefly** | 1.2 — in use | Backend type checker, in place of pyright strict (**D3**): it was already wired into CI, the pre-push hook and the devcontainer's editor extensions, and gives equivalent strictness here. Astral's `ty` is still beta (0.0.x) — re-evaluate both at `ty` 1.0. |
| **import-linter** | 2.13 — in use | Enforces the architecture, so the layering is a build failure rather than a code-review habit. Three contracts in `backend/pyproject.toml`: *domain is pure* (forbidden imports of SQLAlchemy/FastAPI/Starlette/asyncpg/structlog/… and of every other layer), *api and mcp are independent*, and the layer stack `api\|mcp → ingest → services → persistence → domain`. Run by `uv run lint-imports` in CI, `just lint` and pre-push. |
| **structlog** | 26.1 — in use | Structured logging everywhere, kept over the plan's stdlib-logging-plus-JSON-formatter (D7) — already threaded through the app and its tests. |
| **pytest** | 9.1.1 — in use | Plus `pytest-asyncio` (auto mode), `pytest-xdist` (`-n auto`), `pytest-cov` (80% floor), `asgi-lifespan`, `aiosqlite` for the in-memory unit-test database. **`hypothesis` is not installed yet** — it lands in WP-1 with the first pure scoring/matching code, where property-based tests actually pay. |
| **APScheduler** | 3.11.3 — in use | In-process scheduling for scheduled inference, ingest polling, consolidation jobs. Wired in WP-0 (**D5**): started by the API lifespan in `backend/app/core/scheduler.py`, replacing the template's ARQ worker and its Redis service. **4.0 never went stable — do not plan around it.** Single-user app ⇒ no Redis, no worker fleet, no Celery. If a real queue ever becomes necessary: arq or taskiq + Valkey 9. |
| **FastMCP** | **3.4.5** — in use | The coach MCP server (`backend/app/mcp/`), run from the backend image as its own process (`python -m app.mcp.main`, streamable HTTP on :8001 behind Caddy's `/mcp*`). Richer DX (auth, composition) than the official SDK; the official `mcp` SDK went 2.0 (July 2026, breaking vs 1.x) and FastMCP 3 tracks it — standardize here and let it absorb spec revisions. Tool surface today is a single `ping`; WP-8 fills it in. |
| **bcrypt 5** + **itsdangerous 2.2** | in use | Single-user auth (**D6**): one bcrypt hash in `AUTH__PASSWORD_HASH` is the whole credential store, exchanged at login for a cookie signed by Starlette's `SessionMiddleware`. No user table, no JWT. MCP clients authenticate separately with scoped static bearer keys. |
| **Anthropic SDK** | 0.120.x — **WP-8** | Scheduled inference + interpretive layer. Pre-1.0, pin minor. Wrap behind your own `CoachModel` port — model-change handling is a spec feature (§11.4), so the abstraction is domain-driven, not speculative. |
| **httpx** | 0.28.1 — in use | Arrived transitively with `fastapi[standard]` and is the test client today; it is also the outbound HTTP client for later integrations. Stable/slow-moving; 1.0 still unreleased. |
| **polars** | 1.43.x — **WP-5** | Stream crunching: per-second channels, interval detection, metric derivation. Prefer over pandas here (14k-row frames, lazy pipelines, no legacy). pandas 3.0 only if a lib demands it. |
| **pyarrow** | 25.x — **WP-4/5** | Parquet for derived/normalized stream artifacts (see storage note). |
| Domain libs | `garmin-fit-sdk` (decode), `fitdecode` (fallback), `gpxpy`, `tcxreader`, `stravalib` 2.x — **WP-4** | From the earlier ecosystem research. Metrics (NP/IF/load/CP/W′) implemented in-house against GoldenCheetah reference — no maintained package exists; keep them pure functions, property-tested. |

**Stream storage note (MVP decision):** originals are immutable files on disk (the invariant). Parsed per-second channels go to **Parquet files keyed by recording**, read with polars; Postgres holds sessions, intervals, scores, and everything relational. This keeps PG lean, makes "every artefact rebuildable from originals" literal, and defers the TimescaleDB question until real query patterns exist at MMP.

---

## Frontend — Next.js

| Component | Version | Role / rationale |
|---|---|---|
| **Next.js** | **16.2.12** — in use | App Router (default), Turbopack stable and default for dev+build. Mostly a client-heavy SPA-style app behind auth — use RSC where it's free, don't contort for it. PWA (offline today-view) via service worker + web manifest. Its APIs and conventions differ from older majors: read `node_modules/next/dist/docs/` before writing code. |
| **React** | 19.2.8 — in use | — |
| **TypeScript** | **5.9** workspace + **`tsgo`** (`@typescript/native-preview` 7.0.0-dev) for `bun run type-check` — in use | The plan's "TypeScript 7" *is* `tsgo`, the native Go compiler — so checking already runs on that generation (**D2**). The workspace TypeScript deliberately stays on 5.9: `openapi-typescript` and Next's own compiler call the JavaScript API, which the native build does not expose yet. Both installed, each on a supported path. |
| **Tailwind CSS** | 4.x — in use | CSS-first config (`@theme`), no JS config file. |
| **shadcn/ui** | CLI 4.16 over **`@base-ui/react` 1.6** — in use | Component base — **Base UI primitives, not Radix**: components compose with `render={...}`, never `asChild`. Tailwind 4 + React 19 native. |
| **TanStack Query** | 5.101 — in use | Server state, polling for run-ledger/ingest status. v5 is the current React major (ignore "v6" — Svelte adapter only). |
| **uPlot** | 1.6.32 — **WP-5** | **Ride-stream charts**: canvas, ~50 KB, built-in `cursor.sync` across stacked channel charts, handles 150k points in ~90 ms — purpose-built for the 14k-point per-second workload and the chart↔map hover sync. Caveat: low-level/unstyled, slow release pace (stable, Grafana-proven) — wrap it in one owned component. |
| **ECharts** | 6.1.x — **WP-5** | Dashboard/analysis charts (PMC, power curves, distributions, weekly review): batteries included, `dataZoom`, LTTB sampling. Two chart libs is deliberate: one for the hot path, one for everything else. |
| **@vis.gl/react-google-maps** | 1.9.x — post-MVP | Google Maps route view — the official Google-endorsed wrapper, stable 1.x. No maps in the MVP. |
| **Node.js** | **24 LTS** — runtime only | The production image runs Next's standalone server on `node:24-alpine`. Nothing in development invokes Node directly; bun builds and runs the app. Plan the cheap bump to 26 LTS when it lands (2026-10-28). |
| **bun** | in use | Package manager and script runner (`bun.lock`, `bun install --frozen-lockfile` in CI and the Dockerfile), in place of the plan's pnpm (**D2**) — no workspace to manage, since `frontend/` is a standalone project. |
| **Biome** | 2.5 — in use | Lint **and** format in one fast tool (`bun run lint` / `lint:fix` / `format`), in place of ESLint + Prettier (**D2**). |
| API client | `openapi-typescript` 7.13 → `openapi-fetch` 0.17 + `openapi-react-query` 0.5 — in use | Generated from FastAPI's OpenAPI into `frontend/generated/api/` (committed, never hand-edited). `just api-sync` regenerates; `just api-check` fails on drift, and so does CI. One contract, no hand-written types. |
| Tests | Vitest 4.1 + MSW 2.15 (`openapi-msw` 2.0 for typed handlers), Playwright 1.62 — in use | Component tests mock the *network*, never the API client module. Playwright runs in two modes: UI-only against a production build (no backend), and `@fullstack` against the running Compose stack through Caddy. Cover the few critical flows (plan→ride→score); don't over-invest in E2E for a single-user app. |

---

## Infra

| Component | Version | Role / rationale |
|---|---|---|
| **Docker Compose** | in use | The deployment unit, `docker-compose.yml` at the repo root: `db`, `data-init` (one-shot, hands the root-owned `./data` bind mount to the api's non-root uid), `api` (runs `alembic upgrade head` then `fastapi run`), `mcp` (same image, `python -m app.mcp.main`), `frontend`, `caddy`. `./data` is the watched-folder bind mount. |
| **Caddy** | 2.11 (`caddy:2.11-alpine`) — in use | Reverse proxy + automatic HTTPS, and the single public origin: `/api/*` and `/health` → api, `/mcp*` → mcp, everything else → frontend. That same-origin routing is why the frontend is built with an empty `NEXT_PUBLIC_API_BASE_URL` and the browser never makes a cross-origin call. `CADDY_SITE_ADDRESS` defaults to `:80` (plain HTTP); set a hostname and Caddy obtains and renews the certificate itself. Traefik is overkill for one host, one user. |
| Valkey | 9.1 — **only if needed** | Not in MVP, and nothing in the stack needs it (APScheduler is in-process). If a queue/cache appears later, Valkey over Redis: BSD-3, no license question. |
| Auth | in use — single-user session cookie + scoped MCP bearer keys | Human: one bcrypt hash in `AUTH__PASSWORD_HASH`, no user table, exchanged at login for a signed session cookie; every `/api/v1` router but `/auth/*` carries the guard by construction. Agents: `MCP__API_KEYS` holds comma-separated `label:scope:key` entries (scope `read` or `write`), compared in constant time; the label lands on the authenticated identity for per-tool scope checks and audit rows. Optional OIDC (Authelia/Pocket ID) can still go in front later. |
| Backups | `pg_dump` + originals folder → restic/borg to off-box target — **WP-9** | "Backup with verified restore" is a spec feature — script the restore test, schedule it via APScheduler. |

---

## Shape of the repo

Two standalone projects, not a workspace monorepo (**D1**): `backend/` (one uv project) and `frontend/` (one bun project), with `caddy/`, `scripts/`, `data/` and `docs/` alongside them and a `justfile` as the single entry point for every task.

The layering lives *inside* `backend/app/`: `core/` (cross-cutting — config, logging, exceptions, scheduler), `domain/` (pure: entities, scoring, matching, metrics — no frameworks, no I/O), `persistence/` (ORM models, repositories, Alembic), `services/` (use-cases), `ingest/` (file pipeline), and the two independent adapters `api/` (HTTP) and `mcp/` (FastMCP). The MCP server ships as a second Compose service built from the *same* image as the API, differing only in entrypoint.

`domain/` is where the property-tested core lives; `api/` and `mcp/` are thin shells over the shared `services/` layer — which is what keeps the deterministic layer working when the LLM is offline. Unlike a package split, the boundary is checked mechanically: import-linter contracts fail CI on a violation, so "pure domain" is a property of the build, not of anyone's discipline.

The frontend consumes the backend through a generated, committed typed client (`frontend/generated/api/`), so a backend schema change that the frontend has not absorbed fails CI rather than production.

---

## Upgrade watch list

| When | What |
|---|---|
| ~Aug 13, 2026 | PostgreSQL 18.5 minor — take immediately (bump the `postgres:18-alpine` tag's digest by pulling; the compose file tracks the 18 line) |
| Oct 2026 | Node 26 → Active LTS: bump the frontend runtime image. Python 3.15 releases: wait for .2 before moving |
| Late 2026 | `ty` 1.0 — evaluate against pyrefly (D3) before switching; the bar is "meaningfully better signal", not novelty. TypeScript 7 stable: drop the `@typescript/native-preview` dev tag, and drop the 5.9 workspace pin once `openapi-typescript` and Next work against the native build's JS API (D2) |
| Watch | SQLAlchemy 2.1 stable (code written 2.1-clean, migration should be near-zero). FastAPI toward 1.0. MCP spec revisions (FastMCP absorbs them). Anthropic SDK 1.0. FastMCP 3.x minors — the MCP auth surface is the part most likely to move |
| Signal-driven | TimescaleDB: revisit at MMP if stream queries in PG/Parquet become the bottleneck |

---

## Alternatives considered, rejected

- **Django** — batteries aimed at multi-user CRUD apps; FastAPI's Pydantic-native contract + OpenAPI generation fits the API/MCP-first design better.
- **SQLModel** — thin and lagging behind SQLAlchemy 2.x; plain SQLAlchemy with full typing is the durable choice.
- **Celery/Redis at MVP** — infrastructure tax with no single-user payoff; APScheduler in-process does everything scheduled inference needs.
- **Plotly.js everywhere** — heaviest bundle, weakest multi-chart cursor sync at this density; visx — SVG, wrong tool at 14k points.
- **Leaflet/MapLibre** — you specified Google Maps; the vis.gl wrapper is first-party-endorsed and stable. (MapLibre remains the escape hatch if Google API pricing ever bites — isolate map rendering in one component.)
- **tRPC** — the API must serve non-TS consumers (MCP server, agent, future clients); OpenAPI is the contract.
- **uv/pnpm workspace monorepo** (`apps/*` + `packages/*`) — the layer boundaries it would buy are had instead from import-linter contracts inside `backend/app/`, at a fraction of the packaging cost. See D1.
- **JWT auth** — nothing to distribute and no second party to trust when there is exactly one user; a signed session cookie is simpler and revocable by rotating one secret. See D6.
