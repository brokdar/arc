# List available recipes
default:
	@just --list

# --- Setup -------------------------------------------------------------------

# Create .env from .env.example: random secrets + the password you pick
init:
	bash scripts/bootstrap-env.sh

# The printed line is single-quoted because bcrypt hashes are full of `$`,
# which .env parsers and your shell would otherwise expand.

# Print an AUTH__PASSWORD_HASH line for .env, for a password you type
hash-password:
	@cd backend && hash="$(uv run python -c 'import bcrypt, getpass, sys; pw = getpass.getpass("Password: "); sys.exit("passwords do not match, or the password is empty") if (not pw or pw != getpass.getpass("Confirm: ")) else None; print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode())')" && printf "AUTH__PASSWORD_HASH='%s'\n" "$hash"

# --- Dev servers -------------------------------------------------------------

# Start backing services (Postgres) in the background
infra:
	docker compose up -d db

# Run the FastAPI dev server with hot reload
dev-api: infra
	cd backend && uv run fastapi dev app/main.py

# Run the MCP server (needs MCP__API_KEYS in backend/.env or the environment)
dev-mcp: infra
	cd backend && uv run python -m app.mcp.main

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

# Type-check backend + frontend
typecheck:
	cd backend && uv run pyrefly check
	cd frontend && bun run type-check

# Run unit tests (backend + frontend)
test:
	cd backend && uv run pytest -n auto
	cd frontend && bun run test

# Run backend integration tests against a real database
test-int:
	bash scripts/run-integration-tests.sh

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

# Everything CI runs, locally
check: lint typecheck test api-check

# --- Database ----------------------------------------------------------------

# Apply migrations to the dev database
db-upgrade:
	cd backend && uv run alembic upgrade head

# Autogenerate a migration from model changes: just db-revision "add items table"
db-revision message:
	cd backend && uv run alembic revision --autogenerate -m "{{message}}"

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
