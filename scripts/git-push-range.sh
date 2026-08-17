#!/usr/bin/env bash
# Print the `<from> <to>` commit range a pre-push hook should reason about, or
# nothing if it cannot be determined.
#
# WHY THIS EXISTS: prek/pre-commit computes the changed-file set itself and
# applies each hook's `files:` filter to it — but when the branch has no remote
# counterpart (its first push, and every retry after a failed one, since a
# failed push never creates the branch) that set widens to the whole tree. Every
# `files:`-gated pre-push hook then fires on every new branch regardless of what
# it touched. A hook that computes its own range is immune, so the pre-push
# hooks that are expensive enough to care source this instead of trusting
# `files:`.
#
# Usage:  read -r FROM TO < <(scripts/git-push-range.sh)
#         [ -n "$FROM" ] || exit 0
set -uo pipefail

ZERO="0000000000000000000000000000000000000000"

from_ref="${PRE_COMMIT_FROM_REF:-}"
to_ref="${PRE_COMMIT_TO_REF:-HEAD}"
[ -n "$to_ref" ] || to_ref="HEAD"

if [ -z "$from_ref" ] || [ "$from_ref" = "$ZERO" ]; then
  # New branch: compare against where it left the default branch. Prefer the
  # remote's main so a stale local main cannot widen or narrow the range.
  from_ref="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  [ -n "$from_ref" ] || from_ref="$(git merge-base HEAD main 2>/dev/null || true)"
fi

# Verify both ends actually resolve — a shallow clone or an unusual detached
# state should make a hook skip, never crash or block.
git rev-parse --verify --quiet "${from_ref}^{commit}" >/dev/null 2>&1 || exit 0
git rev-parse --verify --quiet "${to_ref}^{commit}" >/dev/null 2>&1 || exit 0

printf '%s %s\n' "$from_ref" "$to_ref"
