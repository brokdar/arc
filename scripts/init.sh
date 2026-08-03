#!/usr/bin/env bash
# One-time project initialization after creating a repository from the
# template. Idempotent: safe to re-run if it fails halfway. Self-deletes on
# success.
#
# What it does:
#   1. Replaces the __PROJECT_NAME__ placeholder everywhere with your name
#   2. Strips the template-only section from README.md
#   3. Creates .env with freshly generated secrets
#   4. Re-locks dependencies (project name appears in uv.lock)
#   5. Regenerates the committed API types
#   6. Installs prek git hooks
#   7. Removes itself and commits the result
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PLACEHOLDER="__PROJECT_NAME__"

# --- 1. Determine the project name -------------------------------------------
# Default: repo directory name. Override: scripts/init.sh "My Project"
PROJECT_NAME="${1:-$(basename "$REPO_ROOT")}"
echo "Initializing project: $PROJECT_NAME"

if ! grep -rl --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv \
    --exclude-dir=.next -F "$PLACEHOLDER" . >/dev/null 2>&1; then
  echo "No placeholders found — already initialized. Continuing with the rest."
else
  grep -rl --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv \
      --exclude-dir=.next -F "$PLACEHOLDER" . | while read -r file; do
    # Portable in-place sed (BSD + GNU)
    sed -i.initbak "s/$PLACEHOLDER/$PROJECT_NAME/g" "$file" && rm -f "$file.initbak"
  done
  echo "Replaced $PLACEHOLDER with '$PROJECT_NAME'."
fi

# --- 2. Strip the template-only README section --------------------------------
if grep -q "template-only:start" README.md; then
  sed -i.initbak '/<!-- template-only:start -->/,/<!-- template-only:end -->/d' README.md
  rm -f README.md.initbak
  # Remove leading blank lines left behind
  printf '%s\n' "$(cat README.md)" | sed '/./,$!d' >README.md.tmp && mv README.md.tmp README.md
  echo "Stripped template-only section from README.md."
fi

# --- 3. Create .env with fresh secrets ----------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  PG_PASSWORD="$(openssl rand -hex 16)"
  JWT_SECRET="$(openssl rand -hex 32)"
  sed -i.initbak "s/^POSTGRES__PASSWORD=.*/POSTGRES__PASSWORD=$PG_PASSWORD/" .env
  sed -i.initbak "s/^AUTH__JWT__SECRET_KEY=.*/AUTH__JWT__SECRET_KEY=$JWT_SECRET/" .env
  rm -f .env.initbak
  echo "Created .env with generated secrets."
else
  echo ".env already exists — leaving it untouched."
fi

# --- 4. Re-lock dependencies ---------------------------------------------------
# The backend package name stays "backend" (no rename needed), but re-lock to
# be safe and to pull the freshest compatible versions at project start.
echo "Re-locking backend dependencies..."
(cd backend && uv lock && uv sync)
echo "Installing frontend dependencies..."
(cd frontend && bun install)

# --- 5. Regenerate API types ---------------------------------------------------
bash scripts/generate-api-types.sh

# --- 6. Install git hooks -------------------------------------------------------
if command -v prek >/dev/null 2>&1; then
  prek install -t pre-commit -t pre-push
  echo "Installed prek hooks."
else
  echo "NOTE: prek not found — install it (uv tool install prek) and run:"
  echo "  prek install -t pre-commit -t pre-push"
fi

# --- 7. Self-delete and commit ---------------------------------------------------
rm -f scripts/init.sh
# Drop the init recipe from the justfile
sed -i.initbak '/^# One-time project initialization/,/^	bash scripts\/init.sh$/d' justfile
rm -f justfile.initbak

git add -A
git commit -m "chore: initialize project from template" >/dev/null
echo ""
echo "✅ Project '$PROJECT_NAME' initialized and committed."
echo "Next steps:"
echo "  bash scripts/setup-repo.sh   # apply GitHub settings (rulesets etc.)"
echo "  just up                      # start the full stack"
