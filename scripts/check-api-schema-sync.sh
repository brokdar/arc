#!/usr/bin/env bash
# Fail if the committed frontend API types are out of sync with the backend.
# Used by pre-commit and CI.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$REPO_ROOT/scripts/generate-api-types.sh" >/dev/null

if ! git -C "$REPO_ROOT" diff --quiet -- frontend/generated/api/; then
  echo "ERROR: frontend/generated/api/ is out of sync with the backend OpenAPI schema." >&2
  echo "Run: just api-sync  (or scripts/generate-api-types.sh) and commit the result." >&2
  git -C "$REPO_ROOT" diff --stat -- frontend/generated/api/ >&2
  exit 1
fi

echo "API schema and generated types are in sync."
