#!/usr/bin/env bash
# Fail if the committed frontend API types are out of sync with the backend.
# Used by pre-commit, CI and `just gate`.
#
# The comparison is CONTENT, not git state: snapshot frontend/generated/api/,
# regenerate it, and diff the two directories. It used to ask git instead
# (`git diff --quiet -- frontend/generated/api/`), which conflates two
# different questions and got both wrong:
#
#   * `just gate` runs mid-PR, before anything is committed. A developer who
#     changed an endpoint and dutifully ran `just api-sync` has correct,
#     idempotent output sitting unstaged in the tree — and git reports it as a
#     difference from the index, so the gate failed on a tree that was in sync.
#     The justfile promises the gate is safe on an uncommitted tree; this is
#     what makes that true. (Hit on `feat(connectors): arc finds the folder the
#     fit files are already in`, and flagged as a hazard by the PR before it.)
#   * `git diff` is blind to untracked files, so a generator that grew a new
#     artifact nobody had committed yet reported "in sync".
#
# frontend/generated/api/ is wholly generated, so anything in it the generator
# does not produce — tracked or not — is drift. The cases are pinned in
# check-api-schema-sync.test.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/frontend/generated/api"

BEFORE="$(mktemp -d)"
trap 'rm -rf "$BEFORE"' EXIT
[ -d "$OUT_DIR" ] && cp -a "$OUT_DIR/." "$BEFORE/"

bash "$REPO_ROOT/scripts/generate-api-types.sh" >/dev/null

# The regenerated output stays in the tree on failure: it is the answer the
# developer is being told to commit.
if ! DRIFT="$(diff -r -q "$BEFORE" "$OUT_DIR")"; then
  echo "ERROR: frontend/generated/api/ is out of sync with the backend OpenAPI schema." >&2
  echo "Run: just api-sync  (or scripts/generate-api-types.sh) and commit the result." >&2
  printf '%s\n' "$DRIFT" | sed -e "s#$BEFORE#frontend/generated/api (committed)#" \
    -e "s#$OUT_DIR#frontend/generated/api (regenerated)#" >&2
  exit 1
fi

echo "API schema and generated types are in sync."
