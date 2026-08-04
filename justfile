# List available recipes
default:
	@just --list


# --- Dev servers -------------------------------------------------------------

# Start backing services (Postgres) in the background
infra:
	docker compose up -d db

# Run the FastAPI dev server with hot reload
dev-api: infra
	cd backend && uv run fastapi dev app/main.py

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
	cd backend && uv run ruff check . && uv run ruff format --check .
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

# Boot the full Docker stack and run the @fullstack smoke suite against it
smoke:
	docker compose up --build --wait db api frontend
	cd frontend && E2E_FULLSTACK=1 bun run test:e2e

# Everything CI runs, locally
check: lint typecheck test

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
