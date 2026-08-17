#!/usr/bin/env bash
# Every script the hook config runs as an executable must be executable IN GIT.
#
# WHY THIS EXISTS. This repo is checked out with `core.fileMode = false`, so git
# ignores the filesystem's +x bit entirely: a script added without
# `git update-index --chmod=+x` is committed 100644, `git status` shows nothing,
# and `chmod +x` fixes it locally while every clone stays broken.
#
# That is not hypothetical. `scripts/git-push-range.sh` was committed 100644 in
# e17b946, so `check-migration-required.sh` exited 126 ("Permission denied") on
# every invocation and its own 11-case suite failed 11/11 — a guard against
# model/migration drift that could not run, and therefore a push that goes green
# for the wrong reason. Nothing surfaced it until `just gate` ran the chain end to
# end on 17 Aug 2026.
#
# Invoked as `bash scripts/check-exec-bits.sh` deliberately: a check for missing
# executable bits must not need one.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 4
CONFIG=".pre-commit-config.yaml"
[ -f "$CONFIG" ] || { echo "no $CONFIG here"; exit 0; }

# Hook entries of the form `entry: ./scripts/foo.sh …` are exec'd directly.
mapfile -t needed < <(
  grep -oE '^\s*entry:\s*\./[^ ]+' "$CONFIG" | sed -E 's/^\s*entry:\s*\.\///' | sort -u
)

bad=0
for f in "${needed[@]}"; do
  if [ ! -f "$f" ]; then
    echo "MISSING: $CONFIG runs ./$f, which does not exist"
    bad=1
    continue
  fi
  mode="$(git ls-files -s -- "$f" | awk '{print $1}')"
  if [ -z "$mode" ]; then
    echo "UNTRACKED: ./$f is run by $CONFIG but is not tracked"
    bad=1
  elif [ "$mode" != "100755" ]; then
    echo "NOT EXECUTABLE IN GIT: ./$f is mode $mode but $CONFIG runs it directly"
    echo "    fix with:  git update-index --chmod=+x $f"
    bad=1
  fi
done

if [ "$bad" -ne 0 ]; then
  echo
  echo "This repo has core.fileMode=false, so \`chmod +x\` alone changes nothing git"
  echo "will record. Use \`git update-index --chmod=+x <file>\` and commit the mode."
  exit 1
fi

echo "exec bits OK (${#needed[@]} directly-invoked script(s))"
