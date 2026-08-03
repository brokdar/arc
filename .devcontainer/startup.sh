#!/bin/bash
set -e

# postCreateCommand runs at the workspace root — derive paths instead of
# hardcoding the repo name so the template works under any project name.
WORKSPACE="$(pwd)"

echo "🚀 Initializing development environment..."

# Ensure bun global bin is in PATH for all shell sessions
export PATH="$HOME/.bun/bin:$PATH"
if ! grep -q 'bun/bin' ~/.bashrc 2>/dev/null; then
    echo 'export PATH="$HOME/.bun/bin:$PATH"' >> ~/.bashrc
fi

curl -fsSL https://claude.ai/install.sh | bash

# Set Claude Code's default permission mode (user-level settings live inside
# the container, so re-apply on every rebuild). Merge so existing settings
# are kept.
echo "⚙️  Configuring Claude Code permission mode..."
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
if [ -f "$CLAUDE_SETTINGS" ]; then
    tmp="$(mktemp)"
    jq '.permissions.defaultMode = "auto"' "$CLAUDE_SETTINGS" >"$tmp" && mv "$tmp" "$CLAUDE_SETTINGS"
else
    echo '{"permissions":{"defaultMode":"auto"}}' >"$CLAUDE_SETTINGS"
fi

# Fix ownership of named volumes (created as root by Docker)
echo "🔧 Fixing volume permissions..."
sudo chown -R "$(id -u):$(id -g)" \
    "$WORKSPACE/frontend/node_modules" \
    "$WORKSPACE/backend/.venv" \
    "$WORKSPACE/frontend/.next"

echo "📦 Installing backend dependencies..."
cd "$WORKSPACE/backend"
uv sync

echo "🔧 Setting up prek hooks..."
cd "$WORKSPACE"
uv tool install prek
# Install both shims: cheap checks run on commit, heavy checks (pyrefly, unit
# tests, type-check) run on push.
prek install -t pre-commit -t pre-push

echo "🎭 Setting up frontend dependencies..."
cd "$WORKSPACE/frontend"
bun install
# Playwright browsers + system deps (Chromium only for faster local dev)
bunx playwright install --with-deps chromium
cd "$WORKSPACE"

echo "✅ Development environment setup complete!"
