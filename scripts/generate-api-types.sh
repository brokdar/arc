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

# Refuse to run without the installed dependency tree. `bunx openapi-typescript`
# prefers the local binary but silently falls back to downloading the LATEST
# release when there is none — a different major than the `^7` in bun.lock, so
# it rewrites schema.d.ts wholesale and the sync check then fails on drift it
# created itself. That is the default state of a fresh git worktree, and this
# script runs from the always-run pre-commit hook, so the first commit made
# there would trash the committed types. Fail before writing anything.
if [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
  echo "ERROR: $REPO_ROOT/frontend/node_modules is missing — refusing to" >&2
  echo "generate API types with an unpinned openapi-typescript." >&2
  echo "Run: bun install  (in a fresh worktree: just worktree-init)" >&2
  exit 1
fi

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
