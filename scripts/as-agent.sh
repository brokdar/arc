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
# Overrides (tests, another bot): AGENT_TOKEN_CMD, AGENT_BOT_NAME, AGENT_BOT_EMAIL.
set -euo pipefail

die() {
  printf 'as-agent: %s\n' "$*" >&2
  printf 'as-agent: the command was NOT run, and it must not be retried without this wrapper.\n' >&2
  exit 3
}

[ "$#" -gt 0 ] || die "usage: scripts/as-agent.sh <command> [args...]"

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

exec "$@"
