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
# is atomic across processes without a helper. It is anchored on
# `--git-common-dir`, not on the working tree, so every worktree of one checkout
# contends for the same lock while a genuinely separate clone does not.
#
# It is advisory-with-an-expiry, deliberately: a run that dies holding the lock
# must not wedge the repository forever, so a lock older than its TTL is stolen
# with a warning naming what held it.
#
# Usage:
#   scripts/docker-lock.sh acquire <label> [--ttl 5400]   # 0 held · 3 busy
#   scripts/docker-lock.sh release <label> [--force]      # 0 always
#   scripts/docker-lock.sh status                         # 0 free · 3 held
set -uo pipefail

cmd="${1:-}"
label="${2:-}"
ttl=5400
force=0
shift $(( $# > 0 ? 1 : 0 ))
while [ $# -gt 0 ]; do
  case "$1" in
    --ttl) ttl="${2:-5400}"; shift 2 ;;
    --force) force=1; shift ;;
    *) shift ;;
  esac
done

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

lock_age() { # epoch seconds since the lock was taken, or empty
  [ -d "$LOCK" ] || return 1
  local then now
  then="$(cat "$LOCK/epoch" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  echo $(( now - then ))
}

write_holder() {
  date +%s > "$LOCK/epoch"
  printf '%s\n' "$label" > "$HOLDER"
  printf 'taken %s by pid %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" >> "$HOLDER"
}

case "$cmd" in
  acquire)
    [ -n "$label" ] || { echo "docker-lock: acquire needs a label" >&2; exit 4; }
    if mkdir "$LOCK" 2>/dev/null; then
      write_holder
      echo "docker-lock: acquired by $label"
      exit 0
    fi
    age="$(lock_age || echo 99999)"
    who="$(head -1 "$HOLDER" 2>/dev/null || echo "unknown")"
    if [ "$who" = "$label" ]; then
      # Re-entrant for the same label: a retried step must not deadlock itself.
      write_holder
      echo "docker-lock: already held by $label (re-entered)"
      exit 0
    fi
    if [ "$age" -gt "$ttl" ]; then
      echo "docker-lock: STEALING a stale lock held by '$who' for ${age}s (ttl ${ttl}s)" >&2
      rm -rf "$LOCK"
      if mkdir "$LOCK" 2>/dev/null; then
        write_holder
        echo "docker-lock: acquired by $label after stealing a stale lock"
        exit 0
      fi
    fi
    echo "docker-lock: BUSY — held by '$who' for ${age}s. The Docker tiers bind fixed"
    echo "host ports and one shared compose project name, so they must not run concurrently."
    exit 3
    ;;
  release)
    if [ ! -d "$LOCK" ]; then
      echo "docker-lock: nothing to release"
      exit 0
    fi
    who="$(head -1 "$HOLDER" 2>/dev/null || echo "unknown")"
    if [ "$who" != "$label" ] && [ "$force" -eq 0 ]; then
      echo "docker-lock: not released — held by '$who', not '$label' (use --force to override)" >&2
      exit 0
    fi
    rm -rf "$LOCK"
    echo "docker-lock: released by $label"
    exit 0
    ;;
  status)
    if [ -d "$LOCK" ]; then
      echo "docker-lock: held by '$(head -1 "$HOLDER" 2>/dev/null || echo unknown)' for $(lock_age || echo '?')s"
      exit 3
    fi
    echo "docker-lock: free"
    exit 0
    ;;
  *)
    echo "usage: docker-lock.sh acquire <label> [--ttl N] | release <label> [--force] | status" >&2
    exit 4
    ;;
esac
