#!/usr/bin/env bash
# Mutual exclusion for the Docker-bound tiers, shared across every worktree of
# this checkout.
#
# WHY THIS EXISTS. `just test-int`, `just smoke`, `just up` and `just infra` bind
# fixed host ports and `test-int` reuses one compose project name in every
# checkout — CLAUDE.md says run them from one checkout at a time. On 16 Aug 2026
# two `implement-plan` runs overlapped for an hour (wf_e0fcc017 12:59–14:36 and
# wf_0fddad15 13:35–16:51), and the local-verification agent in the first was
# told by its own prompt "you have exclusive use of Docker right now — nothing
# else is running". It was not true, and nothing could have told it.
#
# The lock is a DIRECTORY, because `mkdir` is the only filesystem primitive that
# is atomic across processes without a helper. Its metadata is written into a
# staging directory and `mv`d into place, so a loser never sees a half-built lock:
# reading a lock with no `epoch` used to compute an age of ~1.8e9 seconds and
# steal a lock taken a millisecond earlier.
#
# It is anchored on `--git-common-dir`, not on the working tree, so every
# worktree of one checkout contends for the same lock while a genuinely separate
# clone does not.
#
# It is advisory-with-an-expiry, deliberately: a run that dies holding the lock
# must not wedge the repository forever, so a lock older than its TTL is stolen
# with a warning naming what held it.
#
# THE SAME LABEL IS REFUSED TOO. An earlier version re-entered on a label match,
# which meant two runs of one plan — a relaunch while the first is still working —
# both held it, since the labels are derived from branch names. Keying re-entrancy
# on the recorded pid does not help either: every `acquire` is a short-lived
# process that has exited by the time the next one asks, so the holder's pid is
# always dead and "the holder is gone" would always be true. So a second acquire
# under a live label is BUSY, and the message names the remedy (release it first);
# the pid is recorded for diagnosis only.
#
# Usage:
#   scripts/docker-lock.sh acquire <label> [--ttl 5400]   # 0 held · 3 busy
#   scripts/docker-lock.sh release <label> [--force]      # 0 always
#   scripts/docker-lock.sh status                         # 0 free · 3 held
set -uo pipefail

usage() {
  echo "usage: docker-lock.sh acquire <label> [--ttl N] | release <label> [--force] | status" >&2
  exit 4
}

cmd="${1:-}"
[ -n "$cmd" ] || usage
shift
label=""
# A flag in the label position used to become the label: `acquire --ttl 100` took
# the lock under the name "--ttl", which no caller can ever release.
case "${1:-}" in
  --*) ;;
  "") ;;
  *) label="$1"; shift ;;
esac

ttl=5400
force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ttl)
      # `shift 2` with one argument left shifts NOTHING, and with no `set -e` the
      # failure is silent — so the loop never advanced and the script spun at 100%
      # CPU forever. Reachable from one typo in a prompt.
      [ $# -ge 2 ] || { echo "docker-lock: --ttl needs a value" >&2; exit 4; }
      ttl="$2"
      shift 2
      ;;
    --force) force=1; shift ;;
    *) echo "docker-lock: unknown argument '$1'" >&2; exit 4 ;;
  esac
done
case "$ttl" in
  ''|*[!0-9]*) echo "docker-lock: --ttl must be a whole number of seconds, got '$ttl'" >&2; exit 4 ;;
esac

common="$(git rev-parse --git-common-dir 2>/dev/null)" || {
  echo "docker-lock: not a git repository" >&2
  exit 4
}
case "$common" in
  /*) ;;
  *) common="$(cd "$common" && pwd)" ;;
esac
LOCK="$(dirname "$common")/.claude/docker.lock"
HOLDER="$LOCK/holder"

mkdir -p "$(dirname "$LOCK")"

lock_age() { # seconds since the lock was taken, or 0 when it cannot be told
  [ -d "$LOCK" ] || return 1
  local taken now
  taken="$(cat "$LOCK/epoch" 2>/dev/null || echo "")"
  case "$taken" in
    ''|*[!0-9]*) echo 0; return 0 ;;  # mid-creation or corrupt: FRESH, never stale
  esac
  now="$(date +%s)"
  echo $(( now - taken ))
}

holder_label() { head -1 "$HOLDER" 2>/dev/null || echo "unknown"; }
holder_pid() { sed -n '2p' "$HOLDER" 2>/dev/null || echo ""; }

# Build the lock complete, then move it into place: `mkdir` is atomic but writing
# the metadata afterwards is not, and a loser reading that window used to see an
# ageless lock and steal it.
take_lock() {
  local staging
  staging="$(mktemp -d "$(dirname "$LOCK")/.docker.lock.XXXXXX")" || return 1
  date +%s > "$staging/epoch"
  { printf '%s\n' "$label"; printf '%s\n' "$$"; printf 'taken %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; } > "$staging/holder"
  if mv -T "$staging" "$LOCK" 2>/dev/null; then return 0; fi
  rm -rf "$staging"
  return 1
}

case "$cmd" in
  acquire)
    [ -n "$label" ] || { echo "docker-lock: acquire needs a label" >&2; usage; }
    if take_lock; then
      echo "docker-lock: acquired by $label"
      exit 0
    fi
    age="$(lock_age || echo 0)"
    who="$(holder_label)"
    pid="$(holder_pid)"
    if [ "$age" -gt "$ttl" ]; then
      echo "docker-lock: STEALING a stale lock held by '$who' for ${age}s (ttl ${ttl}s)" >&2
      rm -rf "$LOCK"
      if take_lock; then
        echo "docker-lock: acquired by $label after stealing a stale lock"
        exit 0
      fi
    fi
    echo "docker-lock: BUSY — held by '$who'${pid:+ (pid $pid)} for ${age}s. The Docker tiers bind fixed"
    echo "host ports and one shared compose project name, so they must not run concurrently."
    if [ "$who" = "$label" ]; then
      echo "This is your own label. If you hold it and are retrying, release it first:"
      echo "  scripts/docker-lock.sh release $label"
      echo "If another run of this plan is live, it is holding it — wait, or stop that run."
    fi
    exit 3
    ;;
  release)
    [ -n "$label" ] || [ "$force" -eq 1 ] || { echo "docker-lock: release needs a label" >&2; usage; }
    if [ ! -d "$LOCK" ]; then
      echo "docker-lock: nothing to release"
      exit 0
    fi
    who="$(holder_label)"
    if [ "$who" != "$label" ] && [ "$force" -eq 0 ]; then
      echo "docker-lock: not released — held by '$who', not '$label' (use --force to override)" >&2
      exit 0
    fi
    rm -rf "$LOCK"
    echo "docker-lock: released by ${label:-force}"
    exit 0
    ;;
  status)
    if [ -d "$LOCK" ]; then
      age="$(lock_age || echo 0)"
      pid="$(holder_pid)"
      # Report a lock any `acquire` would steal as free, so a caller that polls
      # status before acquiring does not wait out a lock that is already gone by
      # this tool's own rule.
      if [ "$age" -gt "$ttl" ]; then
        echo "docker-lock: free (a stale lock held by '$(holder_label)' for ${age}s would be stolen)"
        exit 0
      fi
      echo "docker-lock: held by '$(holder_label)'${pid:+ (pid $pid)} for ${age}s"
      exit 3
    fi
    echo "docker-lock: free"
    exit 0
    ;;
  *)
    usage
    ;;
esac
