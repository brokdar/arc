# Tech Stack

Versions verified against PyPI/npm/official release pages on **2026-08-01**. Recommendation: your preference (Python + Next.js) is the right call — no dissent, only refinements. Rationale follows each section; upgrade watch list at the bottom.

---

## Backend — Python

| Component | Version | Role / rationale |
|---|---|---|
| **Python** | **3.14.6** | Mature (6 patch releases in). Free-threaded build exists but stays opt-in — not needed for this workload. 3.15 lands Oct 2026; don't wait. Fall back to 3.13.14 only if a C-extension dependency lags. |
| **FastAPI** | 0.141.x (pin `>=0.141,<0.142`) | API layer + auto-OpenAPI (feeds the typed frontend client and the MCP surface). Still 0.x with fast cadence — pin minor. |
| **Pydantic** | 2.13.x | Domain models, validation, settings (`pydantic-settings`). 2.14 in alpha, non-breaking; no v3 imminent. |
| **SQLAlchemy** | 2.0.51 + **Alembic 1.18.x** | ORM + migrations. 2.1 is in beta — start on 2.0, write 2.1-clean code (fully typed, `Mapped[]`-style only). Migrations are first-class from day one: the versioning doctrine lives in the schema. |
| **PostgreSQL** | **18.4** | Single database for everything at MVP: domain, coach memory, audit, scores. EOL 2030. 18.5 lands ~Aug 13 — take it. |
| TimescaleDB | 2.27.x — **deferred, not in MVP** | Optional later for per-second streams if plain PG hurts. License fine for self-hosting (TSL Community). Decision point at MMP when analysis parity lands — see storage note below. |
| **uv** | 0.12.x | Package/project manager, lockfile, Python toolchain. De-facto standard. |
| **ruff** | 0.16.x | Lint + format (replaces black/isort/flake8). |
| **pyright** | latest (CI-pinned) | Strict mode. Astral's `ty` is still beta (0.0.x) — run it alongside if curious, don't gate CI on it until 1.0 (targeted late 2026). |
| **pytest** | 9.1.x | Plus `pytest-asyncio`, `hypothesis` for the scoring/matching engines (property-based tests are a natural fit for matching semantics). |
| **APScheduler** | 3.11.x | In-process scheduling for scheduled inference, ingest polling, consolidation jobs. **4.0 never went stable — do not plan around it.** Single-user app ⇒ no Redis, no worker fleet, no Celery. If a real queue ever becomes necessary: arq or taskiq + Valkey 9. |
| **FastMCP** | **3.4.x** (GA since Feb 2026) | The coach MCP server. Richer DX (auth, composition) than the official SDK. Official `mcp` SDK went 2.0 (July 2026, breaking vs 1.x) — FastMCP 3 tracks it; standardize on FastMCP and let it absorb spec revisions. |
| **Anthropic SDK** | 0.120.x | Scheduled inference + interpretive layer. Pre-1.0, pin minor. Wrap behind your own `CoachModel` port — model-change handling is a spec feature (§11.4), so the abstraction is domain-driven, not speculative. |
| **httpx** | 0.28.x | All outbound HTTP (Wahoo, weather, Strava). Stable/slow-moving; 1.0 still unreleased. |
| **polars** | 1.43.x | Stream crunching: per-second channels, interval detection, metric derivation. Prefer over pandas here (14k-row frames, lazy pipelines, no legacy). pandas 3.0 only if a lib demands it. |
| **pyarrow** | 25.x | Parquet for derived/normalized stream artifacts (see storage note). |
| Domain libs | `garmin-fit-sdk` (decode), `fitdecode` (fallback), `gpxpy`, `tcxreader`, `stravalib` 2.x | From the earlier ecosystem research. Metrics (NP/IF/load/CP/W′) implemented in-house against GoldenCheetah reference — no maintained package exists; keep them pure functions, property-tested. |

**Stream storage note (MVP decision):** originals are immutable files on disk (the invariant). Parsed per-second channels go to **Parquet files keyed by recording**, read with polars; Postgres holds sessions, intervals, scores, and everything relational. This keeps PG lean, makes "every artefact rebuildable from originals" literal, and defers the TimescaleDB question until real query patterns exist at MMP.

---

## Frontend — Next.js

| Component | Version | Role / rationale |
|---|---|---|
| **Next.js** | **16.2.x** | App Router (default), Turbopack stable and default for dev+build. 16.3 imminent, minor. Mostly a client-heavy SPA-style app behind auth — use RSC where it's free, don't contort for it. PWA (offline today-view) via service worker + web manifest. |
| **React** | 19.2.x | — |
| **TypeScript** | **7.0.x** | Native Go compiler, 8–12× faster, GA and npm default since July 2026. React/Next path is fully supported (Vue/MDX gaps don't apply). |
| **Tailwind CSS** | 4.3.x | CSS-first config (`@theme`), no JS config file. |
| **shadcn/ui** | CLI 4.16.x | Component base — new projects default to **Base UI primitives** (not Radix). Tailwind 4 + React 19 native. |
| **TanStack Query** | 5.101.x | Server state, polling for run-ledger/ingest status. v5 is the current React major (ignore "v6" — Svelte adapter only). |
| **uPlot** | 1.6.32 | **Ride-stream charts**: canvas, ~50 KB, built-in `cursor.sync` across stacked channel charts, handles 150k points in ~90 ms — purpose-built for the 14k-point per-second workload and the chart↔map hover sync. Caveat: low-level/unstyled, slow release pace (stable, Grafana-proven) — wrap it in one owned component. |
| **ECharts** | 6.1.x | Dashboard/analysis charts (PMC, power curves, distributions, weekly review): batteries included, `dataZoom`, LTTB sampling. Two chart libs is deliberate: one for the hot path, one for everything else. |
| **@vis.gl/react-google-maps** | 1.9.x | Google Maps route view — the official Google-endorsed wrapper, stable 1.x. |
| **Node.js** | **24 LTS** (24.18) | Active LTS until Oct 2026; plan the cheap bump to 26 LTS when it lands (2026-10-28). |
| **pnpm** | 11.18.x | Workspace manager. |
| API client | `openapi-typescript` + `openapi-fetch` (or orval) | Generated from FastAPI's OpenAPI — one contract, no hand-written types. Versions unpinned here; adopt current at scaffold time. |
| Tests | Vitest + Playwright | Unpinned — adopt current at scaffold time. Playwright covers the few critical flows (plan→ride→score); don't over-invest in E2E for a single-user app. |

---

## Infra

| Component | Version | Role / rationale |
|---|---|---|
| **Docker Compose** | v5.3.x | Deployment unit: `api`, `web`, `postgres`, `caddy`, watched-folder volume. (Compose jumped to a v5 major line in 2026; file format unchanged in practice.) |
| **Caddy** | 2.11.x | Reverse proxy + automatic HTTPS. Two-line config; Traefik is overkill for one host, one user. |
| Valkey | 9.1 — **only if needed** | Not in MVP (APScheduler is in-process). If a queue/cache appears later, Valkey over Redis: BSD-3, no license question. |
| Auth | Single-user session auth in-app; optional OIDC (e.g. Authelia/Pocket ID) in front later | Don't build user management — there is one user. The MCP surface authenticates with scoped API keys per client (FastMCP 3 has auth support). |
| Backups | `pg_dump` + originals folder → restic/borg to off-box target | "Backup with verified restore" is a spec feature — script the restore test, schedule it via APScheduler. |

---

## Shape of the repo

Monorepo (pnpm workspace + uv workspace): `apps/api` (FastAPI), `apps/web` (Next.js), `apps/mcp` (FastMCP server, imports the same domain package), `packages/domain` (Python: entities, scoring, matching, metrics — pure, no I/O), `packages/client` (generated TS client). The domain package is where the property-tested core lives; API/MCP are thin shells over it — which is also what keeps the deterministic layer functional when the LLM is offline.

---

## Upgrade watch list

| When | What |
|---|---|
| ~Aug 13, 2026 | PostgreSQL 18.5 minor — take immediately |
| Oct 2026 | Node 26 → Active LTS: bump. Python 3.15 releases: wait for .2 before moving |
| Late 2026 | `ty` 1.0 — evaluate replacing pyright. TypeScript 7.1 |
| Watch | SQLAlchemy 2.1 stable (code written 2.1-clean, migration should be near-zero). FastAPI toward 1.0. MCP spec revisions (FastMCP absorbs them). Anthropic SDK 1.0 |
| Signal-driven | TimescaleDB: revisit at MMP if stream queries in PG/Parquet become the bottleneck |

---

## Alternatives considered, rejected

- **Django** — batteries aimed at multi-user CRUD apps; FastAPI's Pydantic-native contract + OpenAPI generation fits the API/MCP-first design better.
- **SQLModel** — thin and lagging behind SQLAlchemy 2.x; plain SQLAlchemy with full typing is the durable choice.
- **Celery/Redis at MVP** — infrastructure tax with no single-user payoff; APScheduler in-process does everything scheduled inference needs.
- **Plotly.js everywhere** — heaviest bundle, weakest multi-chart cursor sync at this density; visx — SVG, wrong tool at 14k points.
- **Leaflet/MapLibre** — you specified Google Maps; the vis.gl wrapper is first-party-endorsed and stable. (MapLibre remains the escape hatch if Google API pricing ever bites — isolate map rendering in one component.)
- **tRPC** — the API must serve non-TS consumers (MCP server, agent, future clients); OpenAPI is the contract.
