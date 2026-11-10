#!/usr/bin/env bash
# Run ONE command as the agent's GitHub identity, `brokdar-agent[bot]`:
#
#   scripts/as-agent.sh git push -u origin feat/x
#   scripts/as-agent.sh gh pr create --base main --title "…" --body-file body.md
#
# WHY THIS EXISTS. Agents used to push, open PRs and merge with the operator's
# own `gh` login and SSH key, so every agent PR was authored by `brokdar`. That
# made three things impossible: telling agent work from the operator's, the
# operator approving an agent's PR (GitHub refuses an author's approval of their
# own PR), and an owner lane — a CODEOWNERS review the agent cannot satisfy
# because it IS the owner. A GitHub App gives the agent its own identity; this
# script is the one place that identity is put on.
#
# WHAT IT SETS, for the wrapped command only:
#   - GH_TOKEN: a 1-hour installation token from `agent-token` (a per-host
#     script that signs a JWT with the App's private key; see
#     ~/.local/bin/agent-token). `gh` prefers GH_TOKEN over its stored login.
#   - git author and committer: the bot, so a commit made here is its own.
#   - push over HTTPS with that token. An SSH push authenticates as the
#     operator's key — the App's missing `workflows` permission, which makes
#     GitHub refuse an agent push that edits `.github/workflows/`, would never
#     be consulted. `pushInsteadOf` rewrites the two common SSH forms, and
#     `protocol.ssh.allow=never` makes every other form fail instead of
#     falling through to the operator's key. The credential helper list is
#     emptied first so the operator's own helper cannot answer instead, and the
#     bot's helper answers for github.com only.
#   All of it is environment (GIT_CONFIG_COUNT), never `git config`: the
#   repository config is shared by every worktree and by the operator.
#
# IT NEVER FALLS BACK. No `agent-token`, a failing one, or one that prints
# anything but an installation token (`ghs_…`) is exit 3 with the command NOT
# run. A silent fallback to the operator's login is exactly the state this
# replaces, and it would be invisible — the PR would open either way.
#
# WHAT IT RUNS. Only `gh` and `git`, by those literal names: no path, no `env`,
# `nice` or `bash -c` in front. Everything below that reads a command for what it
# does (is it a merge? does it touch the breaker?) can only read argv it can
# parse, and `scripts/as-agent.sh env gh pr merge 5` or `... bash -c 'gh pr merge'`
# is argv it cannot. `gh alias` is refused for the same reason: an alias runs a
# different command under a name nothing here inspects.
#
# THE MERGE BREAKER. `gh pr merge` is refused (exit 4) while an issue labelled
# `automerge-breaker` is open: scripts/review-breaker.mjs opens one when too many
# recent agent merges were reverted or patched at once, and the operator closes it
# to re-enable merging. This is the one choke point every agent merge passes
# through (the PreToolUse hook forces it), so the check lives here and not in each
# prompt. A merge is read by what it does, not by one spelling: `gh pr ... merge`
# with the verb anywhere after `pr`, a REST call to `pulls/N/merge`, and the
# GraphQL mutations mergePullRequest, enablePullRequestAutoMerge and
# enqueuePullRequest. `--disable-auto` is NOT a merge and stays allowed: when the
# breaker trips, disarming an armed auto-merge is exactly what an agent should do.
# It FAILS CLOSED: if the issue list cannot be read, the merge is refused too,
# because "could not check" must never read as "all clear" for a kill switch.
# It is exit 4, not 3, so a seat can tell "merging is paused" from "no identity".
#
# THE BREAKER CANNOT BE DEFUSED FROM HERE. The bot has issues and labels
# permission, and closing the breaker issue is how merging is re-enabled, so an
# agent "tidying up" the issue it was told to report on would switch the kill
# switch off. Refused (exit 4): closing, editing, deleting, transferring or
# locking an OPEN breaker issue; editing or deleting the `automerge-breaker` label
# at all; and a mutating `gh api` call that names either. Only the operator closes
# the issue.
#
# Overrides (tests, another bot): AGENT_TOKEN_CMD, AGENT_BOT_NAME, AGENT_BOT_EMAIL.
set -euo pipefail

die() {
  printf 'as-agent: %s\n' "$*" >&2
  printf 'as-agent: the command was NOT run, and it must not be retried without this wrapper.\n' >&2
  exit 3
}

[ "$#" -gt 0 ] || die "usage: scripts/as-agent.sh <command> [args...]"
case "$1" in
  gh | git) ;;
  *)
    printf 'as-agent: runs `gh` and `git` only, by those literal names (got: %s). The command was NOT run.\n' "$1" >&2
    exit 3
    ;;
esac

token_cmd="${AGENT_TOKEN_CMD:-agent-token}"
command -v "$token_cmd" >/dev/null 2>&1 ||
  die "'$token_cmd' is not on PATH, so there is no agent identity on this host."
token="$("$token_cmd")" || die "'$token_cmd' failed to mint a token."
# An installation token is the only kind an App mints. A `gho_`/`ghp_` here
# means someone pointed the token command at a user's credentials. The body is
# not just [A-Za-z0-9_]: GitHub's current installation tokens are ~390 characters
# with `.` separators, and a pattern without `.` refused every real token while
# the stub-fed suite stayed green — so match the prefix and a token-safe charset,
# not a length or a shape GitHub is free to change.
[[ "$token" =~ ^ghs_[A-Za-z0-9_.-]+$ ]] ||
  die "'$token_cmd' did not print a GitHub App installation token (ghs_…)."

name="${AGENT_BOT_NAME:-brokdar-agent[bot]}"
email="${AGENT_BOT_EMAIL:-354782613+brokdar-agent[bot]@users.noreply.github.com}"

export GH_TOKEN="$token"
export GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email"
export GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email"

# Appended after any entries the caller already passes through the environment.
n="${GIT_CONFIG_COUNT:-0}"
add() {
  export "GIT_CONFIG_KEY_$n=$1" "GIT_CONFIG_VALUE_$n=$2"
  n=$((n + 1))
}
# Reset the helper list (clears the operator's helpers, including URL-scoped
# ones), then answer for github.com ONLY: an unscoped helper hands the token to
# whatever HTTPS host git asks about during the command, and the pre-push hooks
# that run inside a wrapped push inherit this environment for minutes.
add credential.helper ""
# shellcheck disable=SC2016  # $1 and $GH_TOKEN expand when git runs the helper.
add credential.https://github.com.helper '!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$GH_TOKEN"; }; f'
add url.https://github.com/.pushInsteadOf git@github.com:
add url.https://github.com/.pushInsteadOf ssh://git@github.com/
# FAIL CLOSED. pushInsteadOf is ignored when a remote sets an explicit pushurl,
# and it only knows the literal prefixes above (not `github.com:o/r`, an
# `ssh://…:22/` form, or an `url.…insteadOf` the operator keeps to prefer SSH).
# Any of those would push over SSH as the operator, inside the wrapper, with
# exit 0 — the silent fallback this script exists to prevent. Refusing the
# transport turns every one of them into a loud failure. Cost: a hook that
# fetches over SSH inside a wrapped command fails too; none does today.
add protocol.ssh.allow never
export GIT_CONFIG_COUNT="$n"

BREAKER_LABEL=automerge-breaker

breaker_refuse() { # <what was refused> <why>
  printf 'as-agent: %s The command was NOT run.\n' "$1" >&2
  printf 'as-agent: %s\n' "$2" >&2
  exit 4
}

open_breakers=""
load_open_breakers() { # sets $open_breakers to "12 14" (empty = none); fails CLOSED
  local out
  out="$(gh issue list ${repo:+--repo "$repo"} --label "$BREAKER_LABEL" --state open --json number \
    --jq 'map(.number|tostring)|join(" ")' 2>/dev/null)" || breaker_refuse \
    "could not check for an open $BREAKER_LABEL issue, so this was refused (it fails closed)." \
    "Stop and report it. Do not retry."
  open_breakers="$out"
}

if [ "$1" = gh ]; then
  # Read the command: the repo it targets, its non-flag words, and whether it mutates.
  repo=""
  words=()
  mutating=0
  skip=""
  for a in "${@:2}"; do
    if [ -n "$skip" ]; then
      [ "$skip" = repo ] && repo="$a"
      skip=""
      continue
    fi
    case "$a" in
      -R | --repo) skip=repo ;;
      --repo=*) repo="${a#--repo=}" ;;
      -R?*) repo="${a#-R}" ;;
      --hostname) skip=host ;;
      -X | --method | --method=* | -X?* | -f | -F | --field | --raw-field | --input | --field=* | --raw-field=* | --input=*)
        mutating=1
        [ "$a" = -X ] || [ "$a" = --method ] || [ "$a" = -f ] || [ "$a" = -F ] || [ "$a" = --field ] || [ "$a" = --raw-field ] || [ "$a" = --input ] && skip=value
        ;;
      -*) ;;
      *) words+=("$a") ;;
    esac
  done
  lowered="$(printf '%s ' "$@" | tr '[:upper:]' '[:lower:]')"
  group="${words[0]:-}"
  verb="${words[1]:-}"

  if [ "$group" = alias ]; then
    printf 'as-agent: `gh alias` is refused: an alias runs a different command under a name nothing here inspects. The command was NOT run.\n' >&2
    exit 3
  fi

  is_merge=0
  case "$group" in
    pr)
      # The verb is searched for, not assumed to be the second word: cobra treats a
      # flag it does not know as taking a value, so `gh pr --subject s merge 5` is
      # a merge whose second word is `s`.
      for w in ${words[@]+"${words[@]:1}"}; do [ "$w" = merge ] && is_merge=1; done
      ;;
    api)
      if [[ "$lowered" =~ pulls/[0-9]+/merge|mergepullrequest|enablepullrequestautomerge|enqueuepullrequest ]]; then
        is_merge=1
      fi
      ;;
  esac
  # Disarming an armed auto-merge is the right thing to do while paused.
  case " $* " in *" --disable-auto "*) is_merge=0 ;; esac

  if [ "$is_merge" = 1 ]; then
    load_open_breakers
    if [ -n "$open_breakers" ]; then
      breaker_refuse "merging is paused: $BREAKER_LABEL issue(s) open: #${open_breakers// / #}." \
        "Stop and report it. Do not retry or look for another way to merge; only the operator re-enables merging, by closing that issue. (gh pr merge --disable-auto is still allowed.)"
    fi
  fi

  # Not defusing the breaker: its label, and its issue while it is open.
  tamper=""
  case "$group $verb" in
    "label edit" | "label delete")
      for w in ${words[@]+"${words[@]:2}"}; do [ "$w" = "$BREAKER_LABEL" ] && tamper="the $BREAKER_LABEL label"; done
      ;;
    "issue close" | "issue edit" | "issue delete" | "issue transfer" | "issue lock" | "issue unlock")
      load_open_breakers
      for w in ${words[@]+"${words[@]:2}"}; do
        num="${w##*/}"
        num="${num#\#}"
        for o in $open_breakers; do [ "$num" = "$o" ] && tamper="the open breaker issue #$o"; done
      done
      ;;
  esac
  if [ "$group" = api ] && [ "$mutating" = 1 ]; then
    if [[ "$lowered" =~ labels/$BREAKER_LABEL ]]; then
      tamper="the $BREAKER_LABEL label"
    elif [[ "$lowered" =~ issues/([0-9]+) ]]; then
      load_open_breakers
      for o in $open_breakers; do [ "${BASH_REMATCH[1]}" = "$o" ] && tamper="the open breaker issue #$o"; done
    fi
  fi
  if [ -n "$tamper" ]; then
    breaker_refuse "that would change $tamper, which is how the merge breaker is switched off." \
      "Only the operator closes the breaker issue. Stop and report it."
  fi
fi

exec "$@"
