#!/usr/bin/env bash
# Regenerate the OpenAPI schema from the FastAPI app (offline, no server
# needed) and derive the frontend TypeScript types from it. The output in
# frontend/generated/api/ is committed; CI fails if it drifts from the
# backend (see check-api-schema-sync.sh).
#
# Why the frontend carries two TypeScript compilers: type-checking runs on
# `tsgo` (the native Go port — the TypeScript 7 line), but `openapi-typescript`
# below and Next's own compiler call into the JavaScript API, which the native
# build does not expose yet. So TypeScript 5.9 stays installed for the tools
# that need the JS API while checking happens on the fast native compiler.
# Neither is redundant; removing 5.9 breaks this script.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/frontend/generated/api"
mkdir -p "$OUT_DIR"

echo "Exporting OpenAPI schema from FastAPI app..."
(cd "$REPO_ROOT/backend" && uv run python - <<'PY' >"$OUT_DIR/openapi.json"
import json

from app.main import app

schema = app.openapi()
# Pin the version so hatch-vcs dev versions don't churn the committed schema.
schema["info"]["version"] = "0.0.0"
print(json.dumps(schema, indent=2, sort_keys=True))
PY
)

echo "Generating TypeScript types..."
(cd "$REPO_ROOT/frontend" && bunx openapi-typescript generated/api/openapi.json -o generated/api/schema.d.ts)
# Keep generated output Biome-stable so `biome check` passes untouched
(cd "$REPO_ROOT/frontend" && bunx biome format --write generated/api >/dev/null)

echo "API types written to frontend/generated/api/"
