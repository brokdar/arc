#!/usr/bin/env bash
# Validation harness for scripts/check-api-schema-sync.sh
#
# The question that check answers is "does frontend/generated/api/ match what
# the backend produces" — a fact about CONTENT. It must not also answer "is the
# working tree clean", because `just gate` runs it mid-PR on an uncommitted tree
# and the justfile promises the gate is safe there.
#
# HERMETIC. `git commit` exports GIT_DIR, GIT_INDEX_FILE and friends to its
# hooks, so a suite that builds its own repository in a temp directory silently
# operates on the REAL one instead. Unset them before touching git at all.
set -uo pipefail

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_QUARANTINE_PATH

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-api-schema-sync.sh"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
PASS=0; FAIL=0

# A throwaway repo laid out like the real one, with the EXPENSIVE generator
# replaced by a stub: the check locates its generator relative to its own path,
# so copying it into this tree swaps uv+bun for a script that writes whatever
# the case under test tells it to.
mkdir -p "$ROOT/scripts" "$ROOT/frontend/generated/api"
cp "$SCRIPT" "$ROOT/scripts/check-api-schema-sync.sh"
OUT="$ROOT/frontend/generated/api"

# The stub emits the contents of $ROOT/.next-output/ — one file per generated
# artifact — so a case declares the backend's answer by writing that directory.
cat > "$ROOT/scripts/generate-api-types.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rm -rf "$ROOT/frontend/generated/api"
mkdir -p "$ROOT/frontend/generated/api"
cp -a "$ROOT/.next-output/." "$ROOT/frontend/generated/api/"
echo "generated"
SH
chmod +x "$ROOT/scripts/generate-api-types.sh"

cd "$ROOT"
git init -q -b throwaway . && git config user.email t@t && git config user.name t

# backend answer == committed types
mkdir -p "$ROOT/.next-output"
printf '{"openapi":"3.1.0"}\n' > "$ROOT/.next-output/openapi.json"
printf 'export type paths = Record<string, never>;\n' > "$ROOT/.next-output/schema.d.ts"
cp -a "$ROOT/.next-output/." "$OUT/"
printf '.next-output/\n' > .gitignore
git add -A && git commit -qm base
BASE="$(git rev-parse HEAD)"

run() { # name expected_exit
  local name="$1" want="$2"
  out="$(bash "$ROOT/scripts/check-api-schema-sync.sh" 2>&1)"
  got=$?
  if [ "$got" = "$want" ]; then
    printf '  PASS  %-56s (exit %s)\n' "$name" "$got"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-56s (exit %s, wanted %s)\n' "$name" "$got" "$want"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}

reset_tree() {
  git reset -q --hard "$BASE"
  git clean -qfd -e .next-output
  printf '{"openapi":"3.1.0"}\n' > "$ROOT/.next-output/openapi.json"
  printf 'export type paths = Record<string, never>;\n' > "$ROOT/.next-output/schema.d.ts"
}

echo "== api-schema-sync =="

# 1 — committed tree, backend agrees
reset_tree
run "clean tree, types match the backend -> pass" 0

# 2 — committed tree, backend has moved on
reset_tree
printf '{"openapi":"3.1.0","paths":{"/new":{}}}\n' > "$ROOT/.next-output/openapi.json"
run "clean tree, backend schema drifted -> fail" 1

# 3 — THE REGRESSION. Mid-PR: the endpoint is new, `just api-sync` was run, the
# result is in the tree but not yet staged. The types ARE in sync; only the
# index is behind, and the index is not what this check is about.
reset_tree
printf '{"openapi":"3.1.0","paths":{"/discover":{}}}\n' > "$ROOT/.next-output/openapi.json"
cp -a "$ROOT/.next-output/." "$OUT/"
run "uncommitted but regenerated types -> pass" 0

# 4 — uncommitted AND stale: the athlete hand-edited generated output, or
# changed the backend after running api-sync. Still a drift.
reset_tree
printf '{"openapi":"3.1.0","paths":{"/typed-by-hand":{}}}\n' > "$OUT/openapi.json"
run "uncommitted types the backend does not produce -> fail" 1

# 5 — the generator grows a new artifact nobody committed
reset_tree
printf 'export const client = 1;\n' > "$ROOT/.next-output/client.ts"
run "generator emits a file missing from the tree -> fail" 1

# 6 — an artifact the generator no longer emits is still lying in the tree
reset_tree
printf 'export const stale = 1;\n' > "$OUT/legacy.d.ts"
run "tree holds a file the generator no longer emits -> fail" 1

echo
printf '  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
