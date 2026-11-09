#!/usr/bin/env bash
# Validation harness for scripts/trust-ca.sh (`just trust-ca`) and the static
# pieces of the local-HTTPS path around it. A stub `docker` stands in for the
# real one: nothing here touches Docker or a host port. The Caddyfile's runtime
# behaviour (308 for the real host, a plain answer for the rest) needs Caddy and
# is checked by hand — see the PR that introduced it.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO/scripts/trust-ca.sh"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
PASS=0; FAIL=0

ok() { printf '  PASS  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/        | /'; FAIL=$((FAIL+1)); }
check() { if [ "$2" = 0 ]; then ok "$1"; else bad "$1" "${3:-}"; fi; }

PEM=$'-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----'
mkdir -p "$ROOT/bin"
# STUB_RUNNING=1 -> `ps` reports a container; STUB_HAS_CA=1 -> `cp` succeeds.
cat > "$ROOT/bin/docker" <<STUB
#!/usr/bin/env bash
case "\$2" in
  ps) [ "\${STUB_RUNNING:-0}" = 1 ] && echo abc123; exit 0 ;;
  cp) [ "\${STUB_HAS_CA:-0}" = 1 ] || { echo "Error response from daemon: Could not find the file" >&2; exit 1; }
      printf '%s\n' '$PEM' > "\$4"; exit 0 ;;
esac
exit 99
STUB
chmod +x "$ROOT/bin/docker"

run() { # env assignments...; sets out, rc
  out="$(env PATH="$ROOT/bin:$PATH" ARC_TRUST_CA_OUT="$ROOT/out.crt" "$@" bash "$SCRIPT" 2>&1)"
  rc=$?
}

echo "== trust-ca =="

rm -f "$ROOT/out.crt"
run STUB_RUNNING=0
[ $rc -ne 0 ]; check "not running: exits nonzero" $? "$out"
case "$out" in *"just up"*) ok "not running: names just up" ;; *) bad "not running: names just up" "$out" ;; esac
case "$out" in *"Error response"*) bad "not running: no raw docker error" "$out" ;; *) ok "not running: no raw docker error" ;; esac
[ ! -e "$ROOT/out.crt" ]; check "not running: writes nothing" $?

run STUB_RUNNING=1 STUB_HAS_CA=0
[ $rc -ne 0 ]; check "no CA yet: exits nonzero" $? "$out"
case "$out" in *"plain HTTP"*) ok "no CA yet: says the address serves plain HTTP" ;; *) bad "no CA yet: says the address serves plain HTTP" "$out" ;; esac
case "$out" in *"Error response"*) bad "no CA yet: no raw docker error" "$out" ;; *) ok "no CA yet: no raw docker error" ;; esac
[ ! -e "$ROOT/out.crt" ]; check "no CA yet: writes nothing" $?

run STUB_RUNNING=1 STUB_HAS_CA=1
[ $rc -eq 0 ]; check "success: exits 0" $? "$out"
grep -q 'BEGIN CERTIFICATE' "$ROOT/out.crt"; check "success: PEM written to the output path" $?
case "$out" in *"BEGIN CERTIFICATE"*) ok "success: PEM printed" ;; *) bad "success: PEM printed" "$out" ;; esac
for os in "Linux" "macOS" "Firefox" "WSL2"; do
  case "$out" in *"$os"*) ok "success: instructions cover $os" ;; *) bad "success: instructions cover $os" ;; esac
done
case "$out" in *"down -v"*) ok "success: says to re-run after the volume is recreated" ;; *) bad "success: says to re-run after the volume is recreated" ;; esac
run STUB_RUNNING=1 STUB_HAS_CA=1
[ $rc -eq 0 ]; check "idempotent: a second run overwrites cleanly" $?

# The default output path, and the fossil's name, must be invisible to git.
git -C "$REPO" check-ignore -q caddy-root-ca.crt; check "default output path is gitignored" $?
git -C "$REPO" check-ignore -q caddy-local-ca.crt; check "the caddy-local-ca.crt fossil is gitignored" $?

echo "== the rest of the local-HTTPS path =="
CADDYFILE="$REPO/caddy/Caddyfile"
# Strip comments, then take the catch-all block: from `http:// {` to its close.
block="$(sed 's/#.*//' "$CADDYFILE" | awk '/^http:\/\/ *\{/{f=1} f{print} f&&/^\}/{exit}')"
[ -n "$block" ]; check "Caddyfile has a hostless http:// catch-all site" $?
case "$block" in *"respond"*) ok "catch-all answers with a static respond" ;; *) bad "catch-all answers with a static respond" "$block" ;; esac
case "$block" in *"reverse_proxy"*) bad "catch-all never proxies" "$block" ;; *) ok "catch-all never proxies" ;; esac
case "$block" in *'{$CADDY_SITE_ADDRESS'*) ok "catch-all names the configured address" ;; *) bad "catch-all names the configured address" "$block" ;; esac
grep -q 'http://' "$REPO/.env.example" && sed -n '/CADDY_SITE_ADDRESS=/q;p' "$REPO/.env.example" | tail -8 | grep -q 'http://'
check ".env.example's CADDY_SITE_ADDRESS comment states the http:// prefix rule" $?
grep -qi '^## .*HTTPS' "$REPO/README.md"; check "README has an HTTPS section" $?
sec="$(awk '/^## .*HTTPS/{f=1;next} /^## /{f=0} f' "$REPO/README.md")"
for needle in 'http://' 'just trust-ca' 'certificate' 'Dropbox'; do
  case "$sec" in *"$needle"*) ok "README HTTPS section mentions $needle" ;; *) bad "README HTTPS section mentions $needle" ;; esac
done

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
