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

echo "🛠️  Installing just..."
# Every workflow in this repo goes through the justfile, so the task runner has
# to be present. `rust-just` is the just project's own PyPI distribution; uv
# drops the binary in ~/.local/bin, which is already on PATH.
uv tool install rust-just

echo "📝 Installing git-cliff..."
# Drafts CHANGELOG.md entries from conventional commits (`just changelog`,
# config in cliff.toml). Published to PyPI as a wheel with the binary, so it
# needs no Rust toolchain.
uv tool install git-cliff

echo "🔧 Setting up prek hooks..."
cd "$WORKSPACE"
uv tool install prek
# Install all three shims: cheap checks run on commit, heavy checks (pyrefly,
# unit tests, type-check) on push, and conventional-commit subject linting on
# commit-msg.
prek install -t pre-commit -t pre-push -t commit-msg

echo "🔎 Installing language servers for Claude Code..."
# The typescript-lsp plugin shells out to `typescript-language-server`, which is
# not bundled — without it the plugin loads but never starts a server. Installed
# via bun (never npm -g; .claude/hooks/block_npm.py blocks that anyway) into
# ~/.bun/bin, already on PATH from the top of this script. The server prefers the
# workspace's own TypeScript from frontend/node_modules, so the pinned project
# version is what actually type-checks.
# Python needs nothing here: the pyrefly LSP configured in .claude/settings.json
# runs `uv run pyrefly lsp` out of backend/.venv, so it always matches the
# pyrefly that `just typecheck` and CI use.
bun add -g typescript-language-server typescript

# Python: pyrefly, not pyright. The repo type-checks with pyrefly (just
# typecheck, pre-push, CI), and pyright infers differently — running both means
# editor diagnostics that contradict the build. `.claude/marketplace/` holds a
# one-plugin local marketplace pointing the LSP at `uv run --project backend
# pyrefly lsp`, so the language server is the exact pyrefly from backend/.venv.
# No binary to install; just register the marketplace and enable the plugin.
# `|| true` because both are idempotent and must not fail a container rebuild.
claude plugin marketplace add "$WORKSPACE/.claude/marketplace" || true
claude plugin install pyrefly-lsp@arc-local || true

echo "🎭 Setting up frontend dependencies..."
cd "$WORKSPACE/frontend"
bun install
# Playwright browsers + system deps (Chromium only for faster local dev)
bunx playwright install --with-deps chromium
cd "$WORKSPACE"

echo "✅ Development environment setup complete!"
