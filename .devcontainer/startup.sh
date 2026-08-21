#!/bin/bash
set -e

# postCreateCommand runs at the workspace root — derive paths instead of
# hardcoding the repo name so the template works under any project name.
WORKSPACE="$(pwd)"

echo "🚀 Initializing development environment..."

# Fix ownership of the named volume (created as root by Docker). This must run
# before anything writes to $HOME/.claude — the Claude Code installer and the
# settings merge below both do, and on a freshly created volume they would hit
# a root-owned mount point and fail the rebuild.
echo "🔧 Fixing volume permissions..."
sudo chown -R "$(id -u):$(id -g)" "$HOME/.claude"

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

echo "📦 Installing backend dependencies..."
cd "$WORKSPACE/backend"
uv sync

# Versions for the three installs below come from tools/requirements.txt, never
# from PyPI's latest: an unpinned `uv tool install` means the gate a rebuilt
# container runs is whatever shipped that morning, with no commit here to blame.
# Dependabot's `pip` ecosystem raises the pins.
TOOL_PINS="$WORKSPACE/tools/requirements.txt"

echo "🛠️  Installing just..."
# Every workflow in this repo goes through the justfile, so the task runner has
# to be present. `rust-just` is the just project's own PyPI distribution; uv
# drops the binary in ~/.local/bin, which is already on PATH.
uv tool install --constraints "$TOOL_PINS" rust-just

echo "📝 Installing git-cliff..."
# Drafts CHANGELOG.md entries from conventional commits (`just changelog`,
# config in cliff.toml). Published to PyPI as a wheel with the binary, so it
# needs no Rust toolchain.
uv tool install --constraints "$TOOL_PINS" git-cliff

echo "🔧 Setting up prek hooks..."
cd "$WORKSPACE"
uv tool install --constraints "$TOOL_PINS" prek
# Install all three shims: cheap checks run on commit, heavy checks (pyrefly,
# unit tests, type-check) on push, and conventional-commit subject linting on
# commit-msg.
prek install -t pre-commit -t pre-push -t commit-msg

echo "🔎 Installing language servers for Claude Code..."
# Each language server is the tool the build type-checks with, launched through
# the project's package manager: pyrefly, not pyright, and tsgo, not
# tsserver — the repo checks with pyrefly and tsgo (`just typecheck`, pre-push,
# CI), and a second checker means editor diagnostics that contradict the build.
# `.claude/marketplace/` holds both plugins; each runs its server out of the
# project (`uv run --project backend pyrefly lsp`, `bun run tsgo --lsp`), so a
# language server cannot drift from the version CI installs. No binaries to
# install — `uv sync` above and `bun install` below already provide them; just
# register the marketplace and enable the plugins.
# `|| true` because these are idempotent and must not fail a container rebuild.
claude plugin marketplace add "$WORKSPACE/.claude/marketplace" || true
claude plugin install pyrefly-lsp@arc-local || true
claude plugin install tsgo-lsp@arc-local || true

echo "🎭 Setting up frontend dependencies..."
cd "$WORKSPACE/frontend"
bun install
# Playwright browsers + system deps (Chromium only for faster local dev)
bunx playwright install --with-deps chromium
cd "$WORKSPACE"

echo "✅ Development environment setup complete!"
