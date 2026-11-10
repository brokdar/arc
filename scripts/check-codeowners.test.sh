#!/usr/bin/env bash
# Validation harness for scripts/check-codeowners.sh
#
# WHY: the checker exists to catch protection that silently stopped existing,
# so a checker that silently passes is the bug it was written for. Each way a
# CODEOWNERS line can become a no-op gets a case, in a scratch repository.
set -uo pipefail

# HERMETIC, as in docker-lock.test.sh.
for _v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$_v"; done
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-codeowners.sh"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
PASS=0; FAIL=0

cd "$ROOT" || exit 1
git init -q .
mkdir -p backend/app/api caddy .github
echo x > backend/app/api/session.py; echo x > caddy/Caddyfile; echo x > docker-compose.yml
echo x > caddy-local.txt   # shares a prefix with caddy/ but is not under it
git add -A && git commit -qm base

case_() { # name expected_exit needle -- CODEOWNERS lines...
  local name="$1" want="$2" needle="$3"; shift 4
  printf '%s\n' "$@" > .github/CODEOWNERS
  out="$(bash "$SCRIPT" 2>&1)"; got=$?
  if [ "$got" = "$want" ] && [[ "$out" == *"$needle"* ]]; then
    printf '  PASS  %-58s (exit %s)\n' "$name" "$got"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-58s (exit %s, wanted %s)\n' "$name" "$got" "$want"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}

echo "== check-codeowners =="
case_ "files and directories that exist pass"        0 "all 3 entries" -- \
  "# comment" "" "/backend/app/api/session.py @brokdar" "/caddy/ @brokdar" "/docker-compose.yml @brokdar"
case_ "a renamed file is caught"                      1 "matches no tracked file" -- "/backend/app/api/sessions.py @brokdar"
case_ "a removed directory is caught"                 1 "matches no tracked file" -- "/deploy/ @brokdar"
case_ "a prefix of a file is not a directory match"   1 "matches no tracked file" -- "/caddy-local/ @brokdar"
case_ "an untracked file does not count"              1 "matches no tracked file" -- \
  "$(echo y > untracked.py; echo '/untracked.py @brokdar')"
case_ "a glob is refused"                             1 "is a glob" -- "/backend/**/session.py @brokdar"
case_ "an unanchored path is refused"                 1 "not anchored" -- "session.py @brokdar"
case_ "a line with no owner (it unsets) is refused"   1 "no owner" -- "/caddy/"
case_ "an empty file is refused"                      1 "no entries" -- "# only comments"
case_ "a mistyped owner is refused"                   1 "names owner '@brokdr'" -- "/caddy/ @brokdr"
case_ "an unknown second owner is refused"            1 "names owner '@someone'" -- "/caddy/ @brokdar @someone"
case_ "one bad entry fails the whole file"            1 "deploy" -- "/caddy/ @brokdar" "/deploy/ @brokdar"

# A listing big enough to fill a pipe, with the match sorting FIRST: the shape
# that made the first version fail real entries (SIGPIPE under pipefail) while
# every small case above passed. Index entries only — no files on disk.
empty="$(git hash-object -w --stdin </dev/null)"
for i in $(seq -w 1 20000); do printf '100644 %s\tzz/file-%s.txt\n' "$empty" "$i"; done | git update-index --index-info
git commit -qm many
case_ "an early match in a long listing still passes"  0 "all 2 entries" -- "/backend/app/api/session.py @brokdar" "/caddy/ @brokdar"

# The ruleset record: a fresh repo built from a setup script that says `false`
# has CODEOWNERS and no owner lane.
mkdir -p scripts
printf '"require_code_owner_review": true,\n' > scripts/setup-repo.sh
case_ "setup-repo.sh declaring the review passes"      0 "all 1 entries" -- "/caddy/ @brokdar"
printf '"require_code_owner_review": false,\n' > scripts/setup-repo.sh
case_ "setup-repo.sh declaring false is refused"       1 "no owner lane" -- "/caddy/ @brokdar"
printf 'nothing about it\n' > scripts/setup-repo.sh
case_ "setup-repo.sh silent on it is refused"          1 "no owner lane" -- "/caddy/ @brokdar"
rm scripts/setup-repo.sh

printf '\n  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
