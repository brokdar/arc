#!/usr/bin/env bash
# Fuzz the API from its OpenAPI schema against a throwaway Postgres.
#
# The flags match `.github/workflows/api-fuzz.yml`, asserted by
# `backend/tests/unit/test_fuzz_invocation.py`. What differs is local-only: no
# JUnit report, no timeout backstop, and the database and API are started here
# rather than by separate CI steps.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $REPO_ROOT/backend/docker-compose.test.yml"
API_PID=""

cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Set here rather than read from .env so a fuzz run cannot reach the
# development database. The hash is bcrypt of "ci-test-password", as in CI.
export ENVIRONMENT=test
export POSTGRES__HOST=localhost
export POSTGRES__PORT=5433
export POSTGRES__USER=postgres
export POSTGRES__PASSWORD=test
export POSTGRES__DB=app_test
export AUTH__PASSWORD_HASH='$2b$12$zKJ/WPCZxSLDiNcagKB4NuxBO3.OqDch0OfYVoTkNfFBvjnvREdVm'
export AUTH__SESSION__SECRET_KEY=fuzz-session-secret

# `schemathesis.toml` is read from the working directory, and it holds the
# per-operation narrowing the run depends on.
cd "$REPO_ROOT/backend"

echo "Starting test database..."
$COMPOSE up -d --wait db-test

echo "Running migrations..."
uv run alembic upgrade head

echo "Starting API..."
uv run fastapi run app/main.py --port 8000 >/tmp/arc-fuzz-api.log 2>&1 &
API_PID=$!
timeout 60 bash -c 'until curl -sf http://localhost:8000/health >/dev/null; do sleep 1; done' ||
  { echo "API never became healthy; see /tmp/arc-fuzz-api.log"; exit 1; }

# Everything except /health and /api/v1/auth/* is behind the session guard, so
# without a cookie the fuzzer would only ever see 401s.
echo "Logging in..."
curl -sf -c /tmp/arc-fuzz-cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"ci-test-password"}' >/dev/null
cookie_value=$(awk '$6 == "arc_session" { print $7 }' /tmp/arc-fuzz-cookies.txt)
test -n "$cookie_value"

echo "Fuzzing..."
uv run schemathesis run http://localhost:8000/openapi.json \
  --max-examples 100 \
  --workers auto \
  --exclude-path /api/v1/auth/logout \
  --header "Cookie: arc_session=$cookie_value" \
  --exclude-checks negative_data_rejection,ignored_auth \
  "$@"
