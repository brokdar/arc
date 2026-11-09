#!/usr/bin/env bash
# Validation harness for scripts/set-password.py (`just set-password`).
# Real bcrypt, a throwaway .env per case; nothing here touches the real one.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO/scripts/set-password.py"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
PASS=0; FAIL=0

# `uv run`, not bare python: CLAUDE.md, a VS Code terminal exports VIRTUAL_ENV
# for another checkout.
sp() { (cd "$REPO/backend" && uv run --quiet python "$SCRIPT" "$@"); }

ok() { printf '  PASS  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/        | /'; FAIL=$((FAIL+1)); }
check() { # name, condition-exit
  if [ "$2" = 0 ]; then ok "$1"; else bad "$1" "${3:-}"; fi
}

# A fixture with a comment, a blank line, a commented key and an unrelated
# value full of characters sed would eat.
fixture() {
  printf '# top comment\n\nPOSTGRES__PASSWORD=pa$$/&word\n# AUTH__PASSWORD_HASH=commented-example\nAUTH__PASSWORD_HASH=$2b$12$unquotedpaste\nOTHER=1\n' > "$1"
}

verify() { # env-file password -> exit 0 if the live line is single-quoted and matches
  local f="$1" pw="$2"
  (cd "$REPO/backend" && ENVF="$f" PW="$pw" uv run --quiet python -c '
import bcrypt, os, re
lines = [l for l in open(os.environ["ENVF"], encoding="utf-8").read().split("\n") if l.startswith("AUTH__PASSWORD_HASH=")]
assert len(lines) == 1, lines
m = re.fullmatch(r"AUTH__PASSWORD_HASH=\x27(\$2b\$[^\x27]+)\x27", lines[0])
assert m, lines[0]
assert bcrypt.checkpw(os.environ["PW"].encode(), m.group(1).encode())
assert not bcrypt.checkpw(b"old-password", m.group(1).encode())
')
}

echo "== set-password =="

# 1 — success: quoted, verifies, others byte-identical, backup holds the original
D="$ROOT/1"; mkdir "$D"; fixture "$D/.env"; cp "$D/.env" "$D/orig"
out="$(printf 'new-pw\nnew-pw\n' | sp "$D/.env" 2>&1)"; rc=$?
check "exit 0 on success" $rc 0 "$out"
verify "$D/.env" "new-pw"; check "single quoted hash verifies the new password" $?
grep -v '^AUTH__PASSWORD_HASH=' "$D/.env" > "$D/a"; grep -v '^AUTH__PASSWORD_HASH=' "$D/orig" > "$D/b"
cmp -s "$D/a" "$D/b"; check "every other line byte-identical (comments, blanks, commented key)" $?
cmp -s "$D/orig" "$D/.env.set-password.bak"; check "backup holds the previous file" $?
[ "$(stat -c %a "$D/.env.set-password.bak")" = 600 ]; check "backup is mode 600" $?
case "$out" in *'$2b$'*|*new-pw*) bad "output prints neither hash nor password" "$out" ;; *) ok "output prints neither hash nor password" ;; esac
case "$out" in *"docker compose restart api"*"$D/.env"*|*"$D/.env"*"docker compose restart api"*) ok "output names the file and the restart" ;; *) bad "output names the file and the restart" "$out" ;; esac
ls -A "$D" | grep -q '\.tmp$'; [ $? -ne 0 ]; check "no temp file left behind" $?

# 2 — mismatch: nothing changes, no backup
D="$ROOT/2"; mkdir "$D"; fixture "$D/.env"; cp "$D/.env" "$D/orig"
out="$(printf 'one\ntwo\n' | sp "$D/.env" 2>&1)"; rc=$?
[ $rc -ne 0 ]; check "mismatch exits nonzero" $? "$out"
cmp -s "$D/orig" "$D/.env"; check "mismatch leaves .env untouched" $?
[ ! -e "$D/.env.set-password.bak" ]; check "mismatch leaves no backup" $?
case "$out" in *"did not match"*) ok "mismatch says so" ;; *) bad "mismatch says so" "$out" ;; esac

# 3 — empty password and EOF (the non-tty shape of Ctrl-C)
D="$ROOT/3"; mkdir "$D"; fixture "$D/.env"; cp "$D/.env" "$D/orig"
printf '\n\n' | sp "$D/.env" >/dev/null 2>&1; [ $? -ne 0 ]; check "empty password refused" $?
sp "$D/.env" </dev/null >/dev/null 2>&1; [ $? -ne 0 ]; check "EOF at the prompt exits nonzero" $?
cmp -s "$D/orig" "$D/.env" && [ ! -e "$D/.env.set-password.bak" ]; check "neither touches .env or leaves a backup" $?

# 4 — no live line: append (also when the file lacks a final newline)
D="$ROOT/4"; mkdir "$D"; printf '# c\nOTHER=1\n# AUTH__PASSWORD_HASH=x' > "$D/.env"
printf 'pw\npw\n' | sp "$D/.env" >/dev/null 2>&1; check "append exits 0" $?
verify "$D/.env" pw; check "appended line is quoted and verifies" $?
head -c 35 "$D/.env" | cmp -s - <(printf '# c\nOTHER=1\n# AUTH__PASSWORD_HASH=x'); check "preceding bytes kept" $?

# 5 — no .env at all: fail naming `just init`, create nothing
D="$ROOT/5"; mkdir "$D"
out="$(printf 'pw\npw\n' | sp "$D/.env" 2>&1)"; rc=$?
[ $rc -ne 0 ]; check "missing .env exits nonzero" $?
case "$out" in *"just init"*) ok "missing .env names just init" ;; *) bad "missing .env names just init" "$out" ;; esac
[ -z "$(ls -A "$D")" ]; check "nothing created" $?

# 6 — two live lines collapse to one, anomaly named; double-quoted form replaced
D="$ROOT/6"; mkdir "$D"; printf 'A=1\nAUTH__PASSWORD_HASH="$2b$12$x"\nB=2\nAUTH__PASSWORD_HASH=junk\n' > "$D/.env"
out="$(printf 'pw\npw\n' | sp "$D/.env" 2>&1)"
verify "$D/.env" pw; check "duplicates collapse to one single-quoted line" $?
[ "$(sed -n 1p "$D/.env")$(sed -n 3p "$D/.env")" = "A=1B=2" ]; check "neighbouring lines keep their place" $?
case "$out" in *"2 live"*) ok "anomaly named" ;; *) bad "anomaly named" "$out" ;; esac

# 7 — quote and unicode in the password
D="$ROOT/7"; mkdir "$D"; fixture "$D/.env"; PWD7="it's-pässwörd \"x\" \$HOME"
printf '%s\n%s\n' "$PWD7" "$PWD7" | sp "$D/.env" >/dev/null 2>&1; check "quote/unicode password accepted" $?
verify "$D/.env" "$PWD7"; check "and verifies exactly" $?

# 8 — CRLF file keeps its line endings
D="$ROOT/8"; mkdir "$D"; printf 'A=1\r\nAUTH__PASSWORD_HASH=old\r\nB=2\r\n' > "$D/.env"
printf 'pw\npw\n' | sp "$D/.env" >/dev/null 2>&1
[ "$(grep -c $'\r$' "$D/.env")" = 3 ]; check "CRLF endings preserved" $?

# 9 — the backup path is ignored by git, in the real repo
git -C "$REPO" check-ignore -q .env.set-password.bak; check ".env.set-password.bak is gitignored" $?
git -C "$REPO" check-ignore -q .env.bak; check ".env.bak is gitignored" $?

# 10 — file mode of .env survives
D="$ROOT/10"; mkdir "$D"; fixture "$D/.env"; chmod 600 "$D/.env"
printf 'pw\npw\n' | sp "$D/.env" >/dev/null 2>&1
[ "$(stat -c %a "$D/.env")" = 600 ]; check ".env keeps mode 600" $?

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
