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
(
  cd "$REPO_ROOT/backend" &&
    ENVIRONMENT=test \
    POSTGRES__HOST=localhost \
    POSTGRES__PORT=5433 \
    POSTGRES__USER=postgres \
    POSTGRES__PASSWORD=test \
    POSTGRES__DB=app_test \
    uv run pytest tests/integration "$@"
)
