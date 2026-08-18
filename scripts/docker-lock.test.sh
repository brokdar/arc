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
# Every GIT_* there is, not a hand-picked three: `GIT_CONFIG_PARAMETERS` alone
# (exported whenever git was invoked with `-c`) killed this suite at `git clone`
# with exit 128, no summary and no cleanup.
for _v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$_v"; done

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-lock.sh"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0

cd "$ROOT"
git init -q . && git config user.email t@t && git config user.name t
echo x > f && git add -A && git commit -qm base

run() { # name expected_exit -- args...
  local name="$1" want="$2"; shift 3
  out="$(bash "$SCRIPT" "$@" 2>&1)"
  got=$?
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
# Re-entrancy is for a RETRIED step whose process is gone, not for a second live
# run: two runs of one plan derive the same label from the same branch name, and
# both starting compose on the same ports is the collision this lock exists for.
run "a live holder's own label is still refused"  3 -- acquire run-a
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
# A flag in the label position used to BECOME the label, leaving a lock nobody
# could release for the full 90-minute TTL.
run "a flag is not a label"                    4 -- acquire --ttl 100
run "an unknown flag is refused"               4 -- acquire lbl --frobnicate
run "a non-numeric ttl is refused"             4 -- acquire lbl --ttl abc

# `--ttl` with no value: `shift 2` shifted nothing and the parse loop spun at 100%
# CPU forever. The timeout is the assertion.
printf '  '
if timeout 5 bash "$SCRIPT" acquire lbl --ttl >/dev/null 2>&1; then code=0; else code=$?; fi
if [ "$code" = 4 ]; then
  printf 'PASS  %-58s (exit 4)\n' "--ttl with no value exits, it does not spin"; PASS=$((PASS+1))
else
  printf 'FAIL  %-58s (exit %s; 124 means it hung)\n' "--ttl with no value exits, it does not spin" "$code"; FAIL=$((FAIL+1))
fi

# A lock caught mid-creation has no `epoch`. Treating that as age ~1.8e9 stole a
# lock a millisecond old.
rm -rf .claude/docker.lock && mkdir -p .claude/docker.lock && printf 'someone\n999999\n' > .claude/docker.lock/holder
run "a lock with no epoch is FRESH, not infinitely stale" 3 -- acquire thief --ttl 5400
run "  and the holder's own label cannot bypass it"       3 -- acquire someone --ttl 5400
rm -rf .claude/docker.lock

# Two concurrent acquires: exactly one may win.
race() {
  local won=0 i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    rm -rf .claude/docker.lock
    ( bash "$SCRIPT" acquire A >/dev/null 2>&1; echo $? > "$ROOT/.a" ) &
    ( bash "$SCRIPT" acquire B >/dev/null 2>&1; echo $? > "$ROOT/.b" ) &
    wait
    local a b
    a="$(cat "$ROOT/.a")"; b="$(cat "$ROOT/.b")"
    [ "$a" = 0 ] && [ "$b" = 0 ] && return 1
    [ "$a" = 0 ] || [ "$b" = 0 ] || return 1
    won=$((won+1))
  done
  [ "$won" = 10 ]
}
printf '  '
if race; then
  printf 'PASS  %-58s\n' "10 concurrent pairs: exactly one winner each"; PASS=$((PASS+1))
else
  printf 'FAIL  %-58s\n' "10 concurrent pairs: exactly one winner each"; FAIL=$((FAIL+1))
fi
rm -rf .claude/docker.lock

# status must not report a lock the next acquire would steal as held.
run "acquire, to age it"                       0 -- acquire aged
echo $(( $(date +%s) - 9000 )) > .claude/docker.lock/epoch
run "status calls a stale lock free"           0 -- status --ttl 3600
run "status reports it held under a long ttl"  3 -- status --ttl 100000
rm -rf .claude/docker.lock

rm -rf "$ROOT" "$OTHER"
printf '\n  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
