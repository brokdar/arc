#!/usr/bin/env bash
# Create a ready-to-run .env from .env.example: random secrets for everything
# the stack needs, plus a bcrypt hash of the login password you choose.
# Idempotent — an existing .env is never touched. Run via `just init`.
#
# Non-interactive use (CI, provisioning): set ARC_INIT_PASSWORD to skip the
# prompt. With no TTY and no ARC_INIT_PASSWORD the placeholder hash is left in
# place and you finish the job later with `just hash-password`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
EXAMPLE_FILE="$REPO_ROOT/.env.example"

PLACEHOLDER_HASH="'change-me-to-a-bcrypt-hash'"

if [ -e "$ENV_FILE" ]; then
  echo ".env already exists — leaving it alone."
  echo "To start over: rm .env && just init"
  exit 0
fi

if [ ! -f "$EXAMPLE_FILE" ]; then
  echo "ERROR: $EXAMPLE_FILE not found." >&2
  exit 1
fi

# --- helpers -----------------------------------------------------------------

# Print N random bytes as hex. openssl is everywhere, but not guaranteed.
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"
  else
    echo "ERROR: need either openssl or python3 to generate secrets." >&2
    exit 1
  fi
}

# bcrypt-hash $1. The password goes through the environment, not argv, so it
# does not show up in `ps`.
hash_password() {
  (
    cd "$REPO_ROOT/backend"
    ARC_PLAINTEXT_PASSWORD="$1" uv run python -c 'import bcrypt, os; print(bcrypt.hashpw(os.environ["ARC_PLAINTEXT_PASSWORD"].encode(), bcrypt.gensalt()).decode())'
  )
}

# Ask for the login password twice, hidden. Prompts and errors go to stderr so
# the caller can capture the password from stdout.
prompt_password() {
  local first second
  while true; do
    printf 'Choose the password you will log in with: ' >&2
    read -rs first
    printf '\n' >&2
    if [ -z "$first" ]; then
      echo "  Password must not be empty." >&2
      continue
    fi
    if [ "$(printf '%s' "$first" | wc -c)" -gt 72 ]; then
      echo "  bcrypt ignores everything past 72 bytes — pick a shorter password." >&2
      continue
    fi
    printf 'Confirm the password: ' >&2
    read -rs second
    printf '\n' >&2
    if [ "$first" = "$second" ]; then
      printf '%s' "$first"
      return 0
    fi
    echo "  Passwords did not match — try again." >&2
  done
}

# --- collect the password before writing anything ----------------------------

password_hash="$PLACEHOLDER_HASH"
hash_is_placeholder=1

if [ -n "${ARC_INIT_PASSWORD:-}" ]; then
  echo "Hashing the password from ARC_INIT_PASSWORD..."
  password_hash="'$(hash_password "$ARC_INIT_PASSWORD")'"
  hash_is_placeholder=0
elif [ -t 0 ]; then
  echo "arc has a single user. The password you pick here is stored in .env"
  echo "as a bcrypt hash — there is no way to recover it, only to replace it."
  echo
  plaintext="$(prompt_password)"
  echo "Hashing..."
  password_hash="'$(hash_password "$plaintext")'"
  unset plaintext
  hash_is_placeholder=0
fi

# --- write .env --------------------------------------------------------------

cp "$EXAMPLE_FILE" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Values are substituted with python, not sed: bcrypt hashes and random hex are
# full of `$`, `/` and `.`, which sed would happily eat or expand. Everything
# below is written verbatim, including the single quotes.
BOOT_POSTGRES_PASSWORD="$(rand_hex 16)" \
  BOOT_SESSION_SECRET="$(rand_hex 32)" \
  BOOT_MCP_API_KEYS="'coach:write:$(rand_hex 32),readonly:read:$(rand_hex 32)'" \
  BOOT_PASSWORD_HASH="$password_hash" \
  python3 - "$ENV_FILE" <<'PY'
import os
import sys

path = sys.argv[1]
updates = {
    "POSTGRES__PASSWORD": os.environ["BOOT_POSTGRES_PASSWORD"],
    "AUTH__SESSION__SECRET_KEY": os.environ["BOOT_SESSION_SECRET"],
    "MCP__API_KEYS": os.environ["BOOT_MCP_API_KEYS"],
    "AUTH__PASSWORD_HASH": os.environ["BOOT_PASSWORD_HASH"],
}

with open(path, encoding="utf-8") as handle:
    lines = handle.readlines()

replaced = set()
out = []
for line in lines:
    for key, value in updates.items():
        # Only real assignments — commented examples keep their placeholders.
        if line.startswith(f"{key}="):
            line = f"{key}={value}\n"
            replaced.add(key)
            break
    out.append(line)

missing = sorted(set(updates) - replaced)
if missing:
    sys.exit(
        "ERROR: .env.example has no assignment for: "
        + ", ".join(missing)
        + " — update scripts/bootstrap-env.sh to match it."
    )

with open(path, "w", encoding="utf-8") as handle:
    handle.writelines(out)
PY

# --- report ------------------------------------------------------------------

echo
echo "Wrote .env (mode 600) with:"
echo "  POSTGRES__PASSWORD         random"
echo "  AUTH__SESSION__SECRET_KEY  random"
echo "  MCP__API_KEYS              random keys for the 'coach' (write) and 'readonly' (read) clients"
if [ "$hash_is_placeholder" -eq 0 ]; then
  echo "  AUTH__PASSWORD_HASH        bcrypt hash of your password"
  echo
  echo "Next: just up   → the stack comes up on http://localhost"
else
  echo "  AUTH__PASSWORD_HASH        STILL A PLACEHOLDER (no terminal to prompt on)"
  echo
  echo "Next:"
  echo "  1. just hash-password        → prints an AUTH__PASSWORD_HASH= line"
  echo "  2. replace that line in .env (keep the single quotes)"
  echo "  3. just up                   → the stack comes up on http://localhost"
fi

if [ "$hash_is_placeholder" -eq 0 ] && grep -q 'change-me' "$ENV_FILE"; then
  echo
  echo "Note: .env still contains 'change-me' — check the REQUIRED block at the top."
fi
