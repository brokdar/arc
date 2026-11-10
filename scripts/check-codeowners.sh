#!/usr/bin/env bash
# Fail if any path in .github/CODEOWNERS no longer matches a tracked file, or any
# owner is not a known one.
#
# WHY. CODEOWNERS is how the owner lane is enforced: `protect-main` requires a
# code owner's review on the paths it lists. A line that matches nothing is
# protection that silently stopped existing — rename `backend/app/api/session.py`
# and its line keeps sitting there, GitHub reports no error, and the next agent
# PR to touch the renamed file merges without the operator. So every entry is a
# literal path (a file, or a directory ending in `/`) and this script proves
# each still names something. Globs are refused rather than interpreted: one
# that matches nothing is the same silent hole, and proving a glob matches is a
# reimplementation of GitHub's matcher this file has no need for.
#
# Runs at commit time on every commit (it costs milliseconds), because the edit
# that breaks it is a rename elsewhere, not an edit of CODEOWNERS.
#
# Usage: scripts/check-codeowners.sh [path/to/CODEOWNERS]   (run from the repo root)
set -uo pipefail

file="${1:-.github/CODEOWNERS}"
[ -f "$file" ] || { echo "check-codeowners: $file not found"; exit 1; }

# The only owner this repository has. GitHub silently ignores a line whose owner
# is not a user or team with write access, so a typo (`@brokdr`) is an owner lane
# that does not exist — the same silent hole as a renamed path. Adding an owner
# means adding it here, on purpose.
OWNERS="@brokdar"

bad=0
n=0
while read -r pattern owners; do
  case "$pattern" in '' | '#'*) continue ;; esac
  n=$((n + 1))
  if [ -z "$owners" ]; then
    echo "check-codeowners: '$pattern' has no owner — that line UNSETS ownership"; bad=1; continue
  fi
  for o in $owners; do
    case " $OWNERS " in *" $o "*) ;; *) echo "check-codeowners: '$pattern' names owner '$o', not one of: $OWNERS"; bad=1 ;; esac
  done
  if [[ "$pattern" != /* ]]; then
    echo "check-codeowners: '$pattern' is not anchored at the root (start it with /)"; bad=1; continue
  fi
  if [[ "$pattern" == *[\*\?\[]* ]]; then
    echo "check-codeowners: '$pattern' is a glob — use a literal path so it can be proven to match"; bad=1; continue
  fi
  # Ask git, with the path taken literally, rather than grepping a listing: the
  # first version piped `git ls-files` into `grep -q`, which exits at its first
  # match, so under pipefail the writer's SIGPIPE failed every entry that sorted
  # early in this repo's listing — while a five-file test repository never
  # filled the pipe. A trailing `/` keeps a directory from matching a file that
  # merely shares its prefix.
  if [ -z "$(git --literal-pathspecs ls-files -- "${pattern#/}")" ]; then
    echo "check-codeowners: '$pattern' matches no tracked file (renamed or removed?)"; bad=1
  fi
done < "$file"

[ "$n" -gt 0 ] || { echo "check-codeowners: $file has no entries"; exit 1; }
[ "$bad" -eq 0 ] && echo "check-codeowners: all $n entries match tracked files"
exit "$bad"
