#!/usr/bin/env bash
# Run backend integration tests against a real, throwaway Postgres.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $REPO_ROOT/backend/docker-compose.test.yml"

cleanup() {
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting test database..."
$COMPOSE up -d --wait db-test

echo "Running integration tests..."
# AUTH__PASSWORD_HASH below is a fixed cost-4 bcrypt hash of
# "integration-test-password" (tests/integration/conftest.py logs in with it).
# Single-quoted: bcrypt hashes contain `$`.
(
  cd "$REPO_ROOT/backend" &&
    ENVIRONMENT=test \
    POSTGRES__HOST=localhost \
    POSTGRES__PORT=5433 \
    POSTGRES__USER=postgres \
    POSTGRES__PASSWORD=test \
    POSTGRES__DB=app_test \
    AUTH__PASSWORD_HASH='$2b$04$gMtsVD7iYeuOns1k/bkQc.R2.Lul4ptFnN7RmnzdJEdG.APG8k3r2' \
    AUTH__SESSION__SECRET_KEY=integration-test-secret \
    uv run pytest tests/integration "$@"
)
