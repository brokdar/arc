#!/usr/bin/env bash
# Run a command only when the commits being pushed touch a matching path.
#
#   scripts/run-if-changed.sh '^frontend/' -- bash -c 'cd frontend && bun run build'
#
# This is the `files:` filter a pre-push hook actually wants. prek's own filter
# widens to the whole tree whenever the branch has no remote counterpart — its
# first push, and every retry after a failed one — so a `files:`-gated build
# runs on branches that never touched the frontend. See scripts/git-push-range.sh.
#
# Fails open: if the range cannot be determined, the command RUNS. A gate that
# silently skips the check it exists to run is worse than one that runs it twice.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pattern="${1:-}"
[ -n "$pattern" ] || { echo "run-if-changed.sh: no pattern given" >&2; exit 2; }
shift
[ "${1:-}" = "--" ] || { echo "run-if-changed.sh: expected -- before the command" >&2; exit 2; }
shift
[ "$#" -gt 0 ] || { echo "run-if-changed.sh: no command given" >&2; exit 2; }

range="$("$here/git-push-range.sh")"
if [ -z "$range" ]; then
  exec "$@"
fi
read -r from to <<<"$range"

if git diff --name-only "$from" "$to" 2>/dev/null | grep -qE "$pattern"; then
  exec "$@"
fi

exit 0
