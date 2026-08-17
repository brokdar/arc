#!/usr/bin/env bash
# Validation harness for scripts/docker-lock.sh
#
# WHY: the lock is what makes "you have exclusive use of Docker" a true statement
# instead of a hopeful one, and a lock with a bug is worse than none — a false
# BUSY blocks the only tier that can verify a PR when CI has no budget, and a
# false acquire puts two compose stacks on the same ports. Every branch is cheap
# to prove, so prove it.
set -uo pipefail

# HERMETIC. `git commit` exports GIT_DIR, GIT_INDEX_FILE and friends to its hooks,
# so a suite that builds its own repository in a temp directory silently operates
# on the REAL one instead — which is how this passed standalone and failed inside
# a commit. Unset them before touching git at all.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_QUARANTINE_PATH

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-lock.sh"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0

cd "$ROOT"
git init -q . && git config user.email t@t && git config user.name t
echo x > f && git add -A && git commit -qm base

run() { # name expected_exit -- args...
  local name="$1" want="$2"; shift 3
  set +e
  out="$(bash "$SCRIPT" "$@" 2>&1)"
  got=$?
  set -e
  if [ "$got" = "$want" ]; then
    printf '  PASS  %-58s (exit %s)\n' "$name" "$got"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-58s (exit %s, wanted %s)\n' "$name" "$got" "$want"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}

echo "== docker-lock =="
run "status on a free repo"                    0 -- status
run "first acquire wins"                       0 -- acquire run-a
run "status reports it held"                   3 -- status
run "a second, different holder is refused"    3 -- acquire run-b
run "the same holder re-enters instead of deadlocking" 0 -- acquire run-a
run "a foreign release is a no-op"             0 -- release run-b
run "and the lock survives that no-op"         3 -- status
run "the owner releases"                       0 -- release run-a
run "status is free again"                     0 -- status
run "acquire after release"                    0 -- acquire run-c

# A run that dies holding the lock must not wedge the repo forever.
echo $(( $(date +%s) - 9000 )) > .claude/docker.lock/epoch
run "a stale lock is stolen"                   0 -- acquire run-d --ttl 3600
holder="$(head -1 .claude/docker.lock/holder)"
if [ "$holder" = "run-d" ]; then
  printf '  PASS  %-58s\n' "the thief is recorded as the new holder"; PASS=$((PASS+1))
else
  printf '  FAIL  %-58s (holder=%s)\n' "the thief is recorded as the new holder" "$holder"; FAIL=$((FAIL+1))
fi
echo $(( $(date +%s) - 100 )) > .claude/docker.lock/epoch
run "a fresh lock is NOT stolen"               3 -- acquire run-e --ttl 3600
run "force releases someone else's lock"       0 -- release run-e --force
run "status free after a forced release"       0 -- status

# One lock per checkout, shared by every worktree — anchored on the common git
# dir, so a worktree contends with its main checkout.
git worktree add -q wt -b side >/dev/null 2>&1
run "acquire from the main checkout"           0 -- acquire main-run
cd wt
run "a worktree of the same checkout is refused" 3 -- acquire wt-run
run "and it sees the same lock in status"      3 -- status
cd "$ROOT"
run "released from the main checkout"          0 -- release main-run
cd wt
run "the worktree sees it free"                0 -- status
cd "$ROOT"

# A genuinely separate clone must NOT contend.
OTHER="$(mktemp -d)"
git clone -q "$ROOT" "$OTHER/clone" 2>/dev/null
run "acquire here"                             0 -- acquire here
cd "$OTHER/clone"
run "a separate clone has its own lock"        0 -- acquire there
cd "$ROOT"

run "usage error on an unknown verb"           4 -- frobnicate
run "acquire with no label is a usage error"   4 -- acquire

rm -rf "$ROOT" "$OTHER"
printf '\n  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
