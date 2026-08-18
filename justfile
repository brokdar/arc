# List available recipes
default:
	@just --list

# --- Setup -------------------------------------------------------------------

# Create .env from .env.example: random secrets + the password you pick
init:
	bash scripts/bootstrap-env.sh

# A worktree checks out tracked files and nothing else: no .env, no .venv, no
# node_modules. Install them before working there — the always-run
# api-schema-sync pre-commit hook shells out to bunx, and without
# frontend/node_modules it would generate the API types from an unpinned
# openapi-typescript (see scripts/generate-api-types.sh, which now refuses).

# Install the dependencies and .env of the worktree you are standing in
worktree-init:
	#!/usr/bin/env bash
	set -euo pipefail
	main="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
	# Claude Code copies .env in via .worktreeinclude; `git worktree add` does not.
	[ -f .env ] || cp -p "$main/.env" .env
	(cd backend && uv sync)
	(cd frontend && bun install)

# The printed line is single-quoted because bcrypt hashes are full of `$`,
# which .env parsers and your shell would otherwise expand.

# Print an AUTH__PASSWORD_HASH line for .env, for a password you type
hash-password:
	@cd backend && hash="$(uv run python -c 'import bcrypt, getpass, sys; pw = getpass.getpass("Password: "); sys.exit("passwords do not match, or the password is empty") if (not pw or pw != getpass.getpass("Confirm: ")) else None; print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode())')" && printf "AUTH__PASSWORD_HASH='%s'\n" "$hash"

# --- Dev servers -------------------------------------------------------------

# Start backing services (Postgres) in the background
infra:
	docker compose up -d db

# Host processes read the repo-root .env (see backend/app/core/config.py), but
# its POSTGRES__HOST=db is the compose network name — only reachable from inside
# the Docker network. `just infra` publishes the database on localhost, so these
# recipes override that one value; a real env var beats any .env entry.

# Run the FastAPI dev server with hot reload
dev-api: infra
	cd backend && POSTGRES__HOST=localhost uv run fastapi dev app/main.py

# Run the MCP server (needs MCP__API_KEYS in .env — `just init` writes it)
dev-mcp: infra
	cd backend && POSTGRES__HOST=localhost uv run python -m app.mcp.main

# Run the Next.js dev server
dev-web:
	cd frontend && bun dev

# Start the full stack in Docker
up:
	docker compose up --build -d

# Stop the Docker stack
down:
	docker compose down

# --- Quality gates -----------------------------------------------------------

# Format backend + frontend
format:
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd frontend && bun run format

# Lint backend + frontend
lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run lint-imports
	cd frontend && bun run lint

# The two globs repeat `project-includes` in backend/pyrefly.toml, and they are
# passed rather than left implicit because a worktree lives under
# `.claude/worktrees/`: pyrefly skips a *hidden* path segment when it walks for
# includes itself, so a bare `pyrefly check` there matched no files at all and
# exited 1 — type checking silently absent from every worktree. Passing the
# roots makes the walk start below the dot. `project-excludes` still applies.

# Type-check backend + frontend
typecheck:
	cd backend && uv run pyrefly check 'app/**' 'tests/**'
	cd frontend && bun run type-check

# Run unit tests (backend + frontend)
#
# The backend suite pins a non-UTC `TZ` in `backend/tests/conftest.py`, not
# here: CI and the pre-push hook invoke `pytest` directly, so a pin in this
# recipe would be missing from the two runs that gate a merge (issue #62). The
# frontend pins its two the same way, in `vitest.config.mts` and
# `playwright.config.ts`.
test:
	cd backend && uv run pytest -n auto
	cd frontend && bun run test

# Run backend integration tests against a real database
test-int:
	bash scripts/run-integration-tests.sh

# Production build of the frontend (catches what `tsgo --noEmit` cannot)
build:
	cd frontend && bun run build

# Run Playwright end-to-end tests (UI-only, no backend needed)
e2e:
	cd frontend && bun run test:e2e

# E2E_PASSWORD must be the password whose bcrypt hash is in .env as
# AUTH__PASSWORD_HASH — the suite logs in through the real UI. With an .env
# from `just init` that is the password you chose there, so pass it in:
# E2E_PASSWORD=... just smoke

# Boot the full Docker stack and run the @fullstack smoke suite against it
smoke:
	docker compose up --build --wait db api mcp frontend caddy
	cd frontend && E2E_FULLSTACK=1 E2E_PASSWORD="${E2E_PASSWORD:-ci-test-password}" bun run test:e2e

# The gates that need nothing but this checkout: lint, type-check, unit tests,
# the production frontend build and API-contract drift. NOT covered — each
# needs a service or a long run: `test-int` (Postgres; the only place
# `alembic check` runs), `e2e` (Playwright browsers), `smoke` (the Docker
# stack). `check-all` adds the integration suite; CI runs all of them.

# Everything that runs without Docker or a browser
check: lint typecheck test build api-check

# `check` plus the integration suite (needs Docker for Postgres)
check-all: check test-int

# The single pre-review gate, for a human or an agent: one command, one exit
# code, one success marker to grep for.
#
# WHAT IT RUNS: everything in `check` — ruff check · ruff format --check ·
# import-linter · biome · pyrefly · tsgo · backend unit tests · frontend unit
# tests · the production frontend build · api-contract drift — plus the
# migration heuristic. This is the list the workflow's prompts point at rather
# than restate, so it cannot drift from the recipe.
#
# WHAT IT DOES NOT RUN, so nobody reads it as "everything": `zizmor` and
# `exec-bits` (pre-push hooks outside `check`), the file hygiene hooks
# (end-of-file, trailing-whitespace, check-yaml/toml/json), and the repo's own
# tooling suites (`workflow-guards-test`, `implement-plan-sim-test`,
# `parse-plan-test`, `ci-status-test`, `docker-lock-test`, migration-required's
# own cases) — which run at commit time, scoped to the files they cover. So a
# green gate can still be followed by a hook that refuses the commit.
#
# The migration heuristic reads a COMMIT RANGE, so it is silent until the branch
# has a commit: on the first pass — the gate before the first commit — it exits 0
# without looking at anything.
#
# WHY IT EXISTS SEPARATELY. `implement-plan` used to tell each developer agent
# "the gate is `just check`, it must end GREEN" and then tell the reviewer "`just
# check` is already GREEN, do not re-run it". Neither statement was ever
# observed: on PR #54 (16 Aug 2026) the implementer ran `just lint`, `just test`
# and `just test-int` piecemeal and never ran the gate at all, and the reviewer
# was told it had passed. Now one cheap seat runs THIS, and its exit code is
# evidence the reviewer reads rather than a claim it is asked to trust.
#
# It does not reformat sources (`ruff format --check`, never `--write`), so it is
# safe on an uncommitted tree. It is NOT read-only: `api-check` regenerates
# `frontend/generated/api/` and then diffs it — that regeneration IS the drift
# check — and `build` writes `frontend/.next/`.

# The single pre-review gate: `check` plus the migration heuristic
gate: check
	@bash scripts/check-migration-required.sh
	@echo "GATE OK"

# --- Database ----------------------------------------------------------------

# POSTGRES__HOST=localhost for the same reason as the dev-* recipes above: the
# .env value `db` only resolves inside the compose network.

# Apply migrations to the dev database
db-upgrade:
	cd backend && POSTGRES__HOST=localhost uv run alembic upgrade head

# Autogenerate a migration from model changes: just db-revision "add items table"
db-revision message:
	cd backend && POSTGRES__HOST=localhost uv run alembic revision --autogenerate -m "{{message}}"

# Regenerate the frontend's session-metrics fixture from the real domain.
# Run after changing app/domain/metrics.py, alignment.py or
# session_analysis.py, and commit the result: the fixture is generated so that
# every number in it agrees with the stream it was computed from.
metrics-fixture:
	cd backend && uv run python scripts/emit_metrics_fixture.py
	cd frontend && bunx biome check --write tests/mocks/generated-metrics.ts

# Regenerate the frontend's match-breakdown fixtures from the real domain.
# Run after changing app/domain/matching.py or the fixture rows the script
# states its evidence against: the renormalised weights and the sentences on
# the unassessed components are the domain's, not a fixture author's.
matching-fixture:
	cd backend && uv run python scripts/emit_matching_fixture.py
	cd frontend && bunx biome check --write tests/mocks/generated-matching.ts

# Regenerate the frontend's score and alignment fixtures from the real domain.
# Run after changing app/domain/scoring.py, app/domain/alignment.py or the two
# sides the script states itself against: the axis values, their explanations,
# the criterion outcomes and the suggested verdict are the domain's, and the
# alignment at each offset is what `align` actually pairs — not what a fixture
# author expected it to.
scoring-fixture:
	cd backend && uv run python scripts/emit_scoring_fixture.py
	cd frontend && bunx biome check --write tests/mocks/generated-scoring.ts

# Regenerate the frontend's wellness-trend fixture from the real domain.
# Run after changing app/domain/wellness_baseline.py or the trend read shape,
# and commit the result: every baseline mean, band, deviation and maturity
# verdict in the fixture is the domain's own, and a hand-typed one would let a
# component test agree with an answer the API cannot produce.
wellness-trend-fixture:
	cd backend && uv run python scripts/emit_wellness_trend_fixture.py
	cd frontend && bunx biome check --write tests/mocks/generated-wellness-trend.ts

# --- Maintenance -------------------------------------------------------------

# Re-parse every original under data/originals/, rewrite its stream file and
# recording row, then append a metric version for each session that changed.
# The path for "the parser learned something new": recompute alone reads the
# stored parquet, so a stream written before a channel existed can never gain
# that channel without this. Originals are read-only and are never moved.
# Pass --no-recompute to rewrite streams only, or --recording <uuid> for one.
#
# ORDERING: deploy the new image FIRST, rebuild SECOND, and never roll the
# image back after a rebuild. A rebuilt parquet carries the new parser's
# channels; an older image has no enum member for a channel it predates, so it
# reads the file as missing and every rebuilt session loses its chart and its
# stream metrics until the newer image is back. Nothing is destroyed (the
# originals are untouched), but a rollback after this is an outage.

# Rebuild stored streams from the original files: just rebuild-streams [args]
# Runs inside the api container: the recording rows store originals-relative
# paths resolved against /app, and data/ belongs to the container's uid — a
# host-side run fails on both. Deploy new code BEFORE rebuilding.
rebuild-streams *args:
	docker compose exec api /app/.venv/bin/python /app/scripts/rebuild_streams.py {{args}}

# --- Deployment images -------------------------------------------------------
# The release workflow publishes multi-arch images on version tags; these two
# recipes are the path for UNRELEASED builds: cross-build locally (buildx +
# QEMU, both preinstalled in the devcontainer's docker), then ship the tarball
# straight to a docker host over ssh — no registry, no tag required. A small
# ARM server must pull or receive images, never build them.

# Cross-build the three images: just images [tag] [platform]
images tag="dev" platform="linux/arm64":
	docker buildx build --platform {{platform}} --load -t ghcr.io/brokdar/arc/api:{{tag}} backend
	docker buildx build --platform {{platform}} --load -t ghcr.io/brokdar/arc/mcp:{{tag}} backend
	docker buildx build --platform {{platform}} --load -t ghcr.io/brokdar/arc/frontend:{{tag}} --build-arg NEXT_PUBLIC_API_BASE_URL="" frontend

# Ship built images to a docker host over ssh: just ship-images <ssh-host> [tag]
ship-images host tag="dev":
	docker save ghcr.io/brokdar/arc/api:{{tag}} ghcr.io/brokdar/arc/mcp:{{tag}} ghcr.io/brokdar/arc/frontend:{{tag}} | ssh {{host}} docker load

# --- API contract ------------------------------------------------------------

# Regenerate the OpenAPI schema and frontend API types
api-sync:
	bash scripts/generate-api-types.sh

# Fail if generated API types are out of sync with the backend
api-check:
	bash scripts/check-api-schema-sync.sh

# --- Changelog ---------------------------------------------------------------

# git-cliff (cliff.toml) turns conventional commits into DRAFT entries, commit
# bodies included. CHANGELOG.md stays hand-curated and nothing writes to it:
# pipe a draft out, cut it down, and merge the ENTRIES into the existing
# `## [Unreleased]` section — the draft prints that heading itself, so don't
# paste it wholesale or you get the heading twice.
#
# Note the repo has no tags yet, so `--unreleased` is the whole history and
# re-emits what is already curated in CHANGELOG.md. That stops once the first
# `v*` tag exists. git-cliff prints "N commit(s) were skipped" on stderr for
# anything it could not parse — those are silently missing from the draft, so
# read that line; `git-cliff --unreleased -vv` names them.

# Draft changelog entries for every commit since the last release tag
changelog:
	@git-cliff --unreleased

# Draft changelog entries for a commit range: just changelog-range main..HEAD
changelog-range range:
	@git-cliff {{range}}
