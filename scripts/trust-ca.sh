#!/usr/bin/env bash
# Export Caddy's local root CA from the running stack and say how to trust it.
# Run via `just trust-ca`.
#
# WHY. Set CADDY_SITE_ADDRESS to a hostname (`arc.localhost`) and Caddy mints a
# private CA and serves TLS from it. Until a browser trusts that CA, every
# navigation to arc is a certificate interstitial — including the return leg of
# the Dropbox OAuth flow, which is a fresh navigation carrying an authorization
# code. The CA lives in the caddy-data volume at a path no document named.
#
# WHAT IT NEVER DOES. It does not install the CA into any trust store: a recipe
# that writes to the system trust store is a privilege an update could abuse, so
# it prints per-OS commands for the operator to run. It does not restart or
# reconfigure anything: it reads one file out of the running container. Only the
# public root certificate leaves the volume; the private key stays in it.
#
# OUTPUT. `caddy-root-ca.crt` at the repo root, overwritten on every run, and
# gitignored. (An untracked, un-ignored `caddy-local-ca.crt` from a hand
# extraction was the fossil that prompted this; that name is ignored too, but
# this script writes its own so it never overwrites one.)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ARC_TRUST_CA_OUT:-$REPO_ROOT/caddy-root-ca.crt}"
CA_IN_CONTAINER="/data/caddy/pki/authorities/local/root.crt"

cd "$REPO_ROOT" || exit 1

if [ -z "$(docker compose ps --status running -q caddy 2>/dev/null)" ]; then
  echo "ERROR: the caddy container is not running, so there is no CA to export." >&2
  echo "Start the stack with \`just up\`, then run \`just trust-ca\` again." >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if ! docker compose cp "caddy:$CA_IN_CONTAINER" "$tmp" >/dev/null 2>&1 || [ ! -s "$tmp" ]; then
  echo "ERROR: caddy has no local CA yet ($CA_IN_CONTAINER does not exist)." >&2
  echo "Caddy only creates one when it serves a hostname over TLS." >&2
  echo "With the current CADDY_SITE_ADDRESS (the default \`:80\`, or an \`http://\` address)" >&2
  echo "it serves plain HTTP, so there is nothing to trust." >&2
  echo "Set CADDY_SITE_ADDRESS=arc.localhost in .env, run \`just up\`, and try again." >&2
  exit 1
fi
cp "$tmp" "$OUT"
chmod 644 "$OUT"

echo "Caddy's local root CA is at: $OUT"
echo "(public certificate only; the private key stays in the caddy-data volume)"
echo
cat "$OUT"
cat <<EOF

Trust it, or the browser stops on a certificate warning at every visit and on the
Dropbox return leg (the redirect back to arc is a fresh navigation). Nothing below
is run for you.

Linux (Debian/Ubuntu):
  sudo cp "$OUT" /usr/local/share/ca-certificates/arc-caddy-local-ca.crt
  sudo update-ca-certificates
Linux (Fedora/RHEL):
  sudo cp "$OUT" /etc/pki/ca-trust/source/anchors/arc-caddy-local-ca.crt
  sudo update-ca-trust
macOS:
  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$OUT"
WSL2 / devcontainer: a Windows browser reads the Windows store, not the Linux one.
  certutil.exe -addstore -user Root "\$(wslpath -w "$OUT")"
Firefox keeps its own store and ignores the OS one: Settings > Privacy & Security >
  Certificates > View Certificates > Authorities > Import, then tick "Trust this CA
  to identify websites". (Or set security.enterprise_roots.enabled in about:config
  to make it read the OS store.)
Chrome and Edge use the OS store; restart the browser after adding the CA.

Run this again after \`docker compose down -v\`: that deletes the volume, Caddy
mints a new CA, and the one you trusted no longer signs anything.
EOF
# probe
