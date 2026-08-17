#!/usr/bin/env bash
# Every script that is executed directly must be executable IN GIT.
#
# WHY THIS EXISTS. `scripts/git-push-range.sh` was committed mode 100644 in
# e17b946, so `check-migration-required.sh` exited 126 ("Permission denied") on
# every invocation and its own eleven-case suite failed eleven times — a guard
# against model/migration drift that could not run, and therefore a push that went
# green for the wrong reason. Nothing surfaced it until `just gate` ran the chain
# end to end on 17 Aug 2026. `scripts/run-if-changed.sh` was in the same state,
# which is the entry for the `frontend-build` and `zizmor` pre-push hooks.
#
# The mode is easy to lose and hard to see: `git status` shows nothing when
# `core.fileMode` is false (this checkout has been observed BOTH ways during a
# single day, so it cannot be relied on either way), and a file created without
# `+x` is staged 100644 regardless. So the rule is checked here rather than
# trusted.
#
# TWO RULES, because the motivating file was not a hook entry at all — it was
# invoked by another script:
#   1. every `entry:` in .pre-commit-config.yaml that names a repo path directly
#      (rather than `bash x` / `node x`) must be 100755 in the index;
#   2. every file under scripts/ with a `#!` shebang must be 100755 in the index —
#      a shebang IS the declaration that a file is run, not sourced.
#
# Invoked as `bash scripts/check-exec-bits.sh`, and registered at the pre-push
# stage: a check for missing executable bits must not need one, and during a
# commit prek swaps in a temporary index holding only the staged subset, where
# every other tracked file reads as untracked.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 4
CONFIG=".pre-commit-config.yaml"

bad=0
declare -A NEEDED=()

if [ -f "$CONFIG" ]; then
  # Every `entry:` value, quotes stripped, first token only. A `language: script`
  # entry is exec'd whether or not it is written with a leading `./`, and a quoted
  # or folded scalar is the same command — the old grep required a literal `./`
  # right after the colon and missed all three.
  while IFS= read -r token; do
    [ -n "$token" ] || continue
    case "$token" in
      bash|sh|node|python|python3|uv|uvx|bun|bunx|npx|env) continue ;;  # interpreter prefixes
    esac
    token="${token#./}"
    case "$token" in
      scripts/*|.claude/hooks/*) NEEDED["$token"]="$CONFIG" ;;
    esac
  done < <(
    sed -nE 's/^[[:space:]]*entry:[[:space:]]*//p' "$CONFIG" |
      sed -E "s/^['\"]//; s/['\"][[:space:]]*$//" |
      awk '{print $1}'
  )
fi

# Rule 2: a shebang is a declaration of intent.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  head -c 2 "$f" 2>/dev/null | grep -q '^#!' && NEEDED["$f"]="shebang"
done < <(git ls-files -- 'scripts/*' '.claude/hooks/*' 2>/dev/null)

for f in "${!NEEDED[@]}"; do
  why="${NEEDED[$f]}"
  if [ ! -f "$f" ]; then
    echo "MISSING: $why runs ./$f, which does not exist"
    bad=1
    continue
  fi
  mode="$(git ls-files -s -- "$f" | awk '{print $1}')"
  if [ -z "$mode" ]; then
    echo "UNTRACKED: ./$f is run by $why but is not tracked"
    bad=1
  elif [ "$mode" != "100755" ]; then
    echo "NOT EXECUTABLE IN GIT: ./$f is mode $mode ($why)"
    echo "    fix with:  git update-index --chmod=+x $f"
    bad=1
  fi
done

if [ "$bad" -ne 0 ]; then
  echo
  echo "A file can be executable on disk and 100644 in git — \`chmod +x\` alone does not"
  echo "always record. Use \`git update-index --chmod=+x <file>\` and commit the mode."
  exit 1
fi

echo "exec bits OK (${#NEEDED[@]} directly-invoked script(s))"
