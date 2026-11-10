#!/usr/bin/env bash
# Validation harness for scripts/as-agent.sh
#
# WHY: the wrapper's whole value is two promises — the wrapped command acts as
# the bot, and it never quietly acts as the operator instead. Both break
# invisibly (a PR opens either way), so both are proven here, offline: a stub
# token command stands in for the App, and a scratch repository with a scratch
# global config stands in for the operator's checkout and credential helper.
set -uo pipefail

# HERMETIC, as in docker-lock.test.sh: a hook exports GIT_* into this process,
# and a suite that keeps them operates on the real repository.
for _v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$_v"; done
unset GH_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/as-agent.sh"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0
export STUB_LOG="$ROOT/gh-calls.log"
: > "$STUB_LOG"
trap 'rm -rf "$ROOT"' EXIT

# The operator's world: a global config whose credential helper answers for
# github.com with the operator's password. The bot must never get that answer.
export HOME="$ROOT/home" GIT_CONFIG_GLOBAL="$ROOT/home/.gitconfig" GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME" "$ROOT/bin"
cat > "$GIT_CONFIG_GLOBAL" <<'EOF'
[credential]
	helper = "!f() { echo username=operator; echo password=OPERATOR-SECRET; }; f"
[credential "https://github.com"]
	helper = "!f() { echo username=operator; echo password=OPERATOR-GH; }; f"
EOF

stub() { # name body — a token command on PATH
  printf '#!/usr/bin/env bash\n%s\n' "$2" > "$ROOT/bin/$1"
  chmod +x "$ROOT/bin/$1"
}
# The shape GitHub actually issues (observed 2026-11-10): long, with `.`
# separators. The first stub here was `ghs_` + alphanumerics, which is why a
# wrapper that refused every REAL token passed this suite.
stub tok-ok 'echo ghs_stubInstallationToken123.eyJhbGciOiJSUzI1NiJ9.c2lnbmF0dXJl-x_y'
stub tok-space 'echo "ghs_two words"'
stub tok-user 'echo gho_operatorsOwnToken'
stub tok-empty 'true'
stub tok-fail 'exit 1'
# A stub `gh` for the merge breaker: STUB_BREAKER is what the issue list prints
# (a "#12" string, or empty for none), STUB_GH_FAIL makes it fail, and it logs
# whatever else it is asked to run so a merge that should not have run is visible.
stub gh 'case "$1 $2" in
  "issue list") [ -n "${STUB_GH_FAIL:-}" ] && exit 1; printf "%s" "${STUB_BREAKER:-}"; echo "$*" >> "$STUB_LOG"; exit 0 ;;
esac
case "$1" in
  probe-token) printf "%s" "$GH_TOKEN"; exit 0 ;;
  exit-code) exit "$2" ;;
esac
echo "GH-RAN: $*"'
export PATH="$ROOT/bin:$PATH"

cd "$ROOT" || exit 1
git init -q repo && cd repo || exit 1
git remote add origin git@github.com:brokdar/arc.git

check() { # name expected_exit expected_output_substring -- command...
  local name="$1" want="$2" needle="$3"; shift 4
  out="$("$@" 2>&1)"; got=$?
  if [ "$got" = "$want" ] && [[ "$out" == *"$needle"* ]]; then
    printf '  PASS  %-62s (exit %s)\n' "$name" "$got"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-62s (exit %s, wanted %s; wanted output containing %q)\n' "$name" "$got" "$want" "$needle"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}
absent() { # name forbidden_substring -- command...
  local name="$1" needle="$2"; shift 3
  out="$("$@" 2>&1)"
  if [[ "$out" != *"$needle"* ]]; then
    printf '  PASS  %-62s\n' "$name"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-62s (output contains %q)\n' "$name" "$needle"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}

echo "== as-agent: it never falls back to the operator =="
check "no command is a usage error"                        3 "usage" -- bash "$SCRIPT"
check "a missing token command refuses"                    3 "NOT run" -- env AGENT_TOKEN_CMD=no-such-cmd bash "$SCRIPT" gh noop
check "a failing token command refuses"                    3 "failed" -- env AGENT_TOKEN_CMD=tok-fail bash "$SCRIPT" gh noop
check "an empty token refuses"                             3 "installation token" -- env AGENT_TOKEN_CMD=tok-empty bash "$SCRIPT" gh noop
check "a user token (gho_) refuses"                        3 "installation token" -- env AGENT_TOKEN_CMD=tok-user bash "$SCRIPT" gh noop
check "a token with whitespace refuses"                    3 "installation token" -- env AGENT_TOKEN_CMD=tok-space bash "$SCRIPT" gh noop
absent "a refusal does not run the command"                "GH-RAN" -- env AGENT_TOKEN_CMD=tok-fail bash "$SCRIPT" gh echo-ran

echo "== as-agent: the wrapped command acts as the bot =="
export AGENT_TOKEN_CMD=tok-ok
check "gh gets the installation token, dots and all"       0 "ghs_stubInstallationToken123.eyJhbGciOiJSUzI1NiJ9.c2lnbmF0dXJl-x_y" -- bash "$SCRIPT" gh probe-token
check "commits are authored by the bot"                    0 "brokdar-agent[bot]" -- bash "$SCRIPT" git var GIT_AUTHOR_IDENT
check "  …and committed by it"                             0 "354782613+brokdar-agent[bot]@users.noreply.github.com" -- bash "$SCRIPT" git var GIT_COMMITTER_IDENT
check "an SSH origin pushes over HTTPS"                    0 "https://github.com/brokdar/arc.git" -- bash "$SCRIPT" git remote get-url --push origin
check "  …while fetches keep the SSH URL"                  0 "git@github.com:brokdar/arc.git" -- bash "$SCRIPT" git remote get-url origin
CRED_REQ=$'protocol=https\nhost=github.com\npath=brokdar/arc.git\n\n'
check "github.com credentials are the bot's token"         0 "password=ghs_stubInstallationToken123.eyJhbGciOiJSUzI1NiJ9" -- bash -c "printf '%s' \"\$1\" | bash '$SCRIPT' git credential fill" _ "$CRED_REQ"
absent "  …never the operator's helpers"                   "OPERATOR" -- bash -c "printf '%s' \"\$1\" | bash '$SCRIPT' git credential fill" _ "$CRED_REQ"
absent "  …and nothing is handed to any other host"          "ghs_" -- bash -c "printf 'protocol=https\nhost=evil.example\n\n' | bash '$SCRIPT' git credential fill"
# Every form that would otherwise push over SSH as the operator must fail, not
# fall through: pushInsteadOf is ignored under an explicit pushurl and does not
# know the scp form without a user or an `ssh://…:22/` URL.
for url in "git@github.com:brokdar/arc.git" "github.com:brokdar/arc.git" "ssh://git@github.com:22/brokdar/arc.git"; do
  git remote add via-ssh "$url"
  check "an ssh remote ($url) is refused, not pushed" 128 "not allowed" -- bash "$SCRIPT" git ls-remote --heads via-ssh
  git remote remove via-ssh
done
# A push needs something to push, or git fails on the refspec before it ever
# reaches the transport and the case proves nothing.
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m probe
git remote add pushurl-ssh https://github.com/brokdar/arc.git
git config remote.pushurl-ssh.pushurl git@github.com:brokdar/arc.git
check "an explicit ssh pushurl is refused, not pushed" 128 "not allowed" -- bash "$SCRIPT" git push --dry-run pushurl-ssh HEAD
git remote remove pushurl-ssh
check "config the caller passed through the env survives"  0 "kept" -- env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=arc.probe GIT_CONFIG_VALUE_0=kept bash "$SCRIPT" git config --get arc.probe
check "the command's exit code is passed through"          7 "" -- bash "$SCRIPT" gh exit-code 7

echo "== as-agent: it runs gh and git, by those literal names =="
check "an absolute path to gh is refused"                    3 "only, by those literal names" -- bash "$SCRIPT" /usr/bin/gh pr merge 5
check "env in front is refused"                              3 "only, by those literal names" -- bash "$SCRIPT" env gh pr merge 5
check "bash -c is refused"                                   3 "only, by those literal names" -- bash "$SCRIPT" bash -c 'gh pr merge 5'
check "nice in front is refused"                             3 "only, by those literal names" -- bash "$SCRIPT" nice gh pr merge 5
absent "  …and none of them ran"                              "GH-RAN" -- bash "$SCRIPT" env gh pr merge 5
check "gh alias is refused (it runs another command)"        3 "gh alias" -- bash "$SCRIPT" gh alias set pm 'pr merge'
check "  …and gh alias even when merging is not paused"      3 "NOT run" -- env STUB_BREAKER= bash "$SCRIPT" gh alias set x 'pr view'

echo "== as-agent: the merge breaker =="
M() { env STUB_BREAKER="$1" bash "$SCRIPT" "${@:2}"; }   # M "<open issue numbers>" gh ...
check "a merge runs when no breaker issue is open"           0 "GH-RAN: pr merge 5 --squash" -- env STUB_BREAKER= bash "$SCRIPT" gh pr merge 5 --squash
check "--auto is a merge too, and runs when clear"           0 "GH-RAN: pr merge 5 --squash --auto" -- env STUB_BREAKER= bash "$SCRIPT" gh pr merge 5 --squash --auto
check "an open breaker issue refuses the merge"              4 "paused: automerge-breaker issue(s) open: #12" -- M 12 gh pr merge 5 --squash
check "  …and names every open one"                          4 "#12 #14" -- M "12 14" gh pr merge 5 --squash
check "  …and tells the agent not to retry or look elsewhere" 4 "Do not retry or look for another way" -- M 12 gh pr merge 5 --squash
absent "  …and does not run the merge"                       "GH-RAN" -- M 12 gh pr merge 5 --squash
check "--auto is refused too"                                4 "merging is paused" -- M 12 gh pr merge 5 --squash --auto
# Every spelling of a merge, found by an adversarial review of the first version.
check "a -R flag before the verb is still a merge"           4 "merging is paused" -- M 12 gh pr merge -R brokdar/arc 5
check "-R before pr is still a merge"                        4 "merging is paused" -- M 12 gh -R brokdar/arc pr merge 5
check "--repo=x is still a merge"                            4 "merging is paused" -- M 12 gh pr merge --repo=brokdar/arc 5
check "an unknown flag with a value before the verb"         4 "merging is paused" -- M 12 gh pr --subject s merge 5
check "a short flag with a value before the verb"            4 "merging is paused" -- M 12 gh pr -t s merge 5
check "the REST merge route"                                 4 "merging is paused" -- M 12 gh api -X PUT repos/brokdar/arc/pulls/5/merge
check "the REST route, spelled with --method"                4 "merging is paused" -- M 12 gh api --method PUT repos/brokdar/arc/pulls/5/merge
check "GraphQL mergePullRequest"                             4 "merging is paused" -- M 12 gh api graphql -f query='mutation { mergePullRequest(input:{pullRequestId:"x"}) { clientMutationId } }'
check "GraphQL enablePullRequestAutoMerge"                   4 "merging is paused" -- M 12 gh api graphql -f query='mutation { enablePullRequestAutoMerge(input:{pullRequestId:"x"}) { clientMutationId } }'
check "GraphQL enqueuePullRequest"                           4 "merging is paused" -- M 12 gh api graphql -f query='mutation { enqueuePullRequest(input:{pullRequestId:"x"}) { clientMutationId } }'
check "GraphQL in any letter case"                           4 "merging is paused" -- M 12 gh api graphql -f query='mutation { MergePullRequest(input:{}) { id } }'
check "it FAILS CLOSED when the issue list cannot be read"   4 "fails closed" -- env STUB_GH_FAIL=1 bash "$SCRIPT" gh pr merge 5 --squash
absent "  …without running the merge"                        "GH-RAN" -- env STUB_GH_FAIL=1 bash "$SCRIPT" gh pr merge 5 --squash
check "the issue list is read from the merge's own -R repo"  4 "merging is paused" -- M 12 gh pr merge -R other/repo 5
if grep -q -- "--repo other/repo" "$STUB_LOG"; then
  printf '  PASS  %-62s\n' "  …by passing it to gh issue list"; PASS=$((PASS+1))
else
  printf '  FAIL  %-62s\n' "  …by passing it to gh issue list"; FAIL=$((FAIL+1))
fi
# Disarming is what an agent SHOULD do when paused; it is not a merge.
check "--disable-auto is allowed while paused"               0 "GH-RAN: pr merge 5 --disable-auto" -- M 12 gh pr merge 5 --disable-auto
# Only merging pauses: an agent must still be able to push a revert and say why.
check "a plain gh command is not paused"                     0 "GH-RAN: noop" -- M 12 gh noop
check "a PR comment is not paused"                           0 "GH-RAN: pr comment 5" -- M 12 gh pr comment 5 --body x
check "a PR create is not paused"                            0 "GH-RAN: pr create" -- M 12 gh pr create --title t
check "a push is not paused"                                 0 "" -- M 12 git var GIT_AUTHOR_IDENT
check "a new issue is not paused"                            0 "GH-RAN: issue create" -- M 12 gh issue create --title t
check "a pr view mentioning merge is not a merge"            0 "GH-RAN: pr view 5 --json mergeStateStatus" -- M 12 gh pr view 5 --json mergeStateStatus
check "a body that says 'pr merge' is not a merge"           0 "GH-RAN: pr comment 5" -- M 12 gh pr comment 5 --body "gh pr merge later"
check "another group's merge verb is not a merge"            0 "GH-RAN: queue merge 5" -- M 12 gh queue merge 5
check "even a GET of the merge route is read as a merge (the path names it)" 4 "merging is paused" -- M 12 gh api repos/brokdar/arc/pulls/5/merge

echo "== as-agent: the breaker cannot be defused from here =="
check "closing the open breaker issue is refused"            4 "open breaker issue #12" -- M 12 gh issue close 12
check "  …by number with a hash"                             4 "open breaker issue #12" -- M 12 gh issue close '#12'
check "  …by URL"                                            4 "open breaker issue #12" -- M 12 gh issue close https://github.com/brokdar/arc/issues/12
check "  …editing it (removing the label)"                   4 "open breaker issue #12" -- M 12 gh issue edit 12 --remove-label automerge-breaker
check "  …deleting it"                                       4 "open breaker issue #12" -- M 12 gh issue delete 12 --yes
check "  …transferring it"                                   4 "open breaker issue #12" -- M 12 gh issue transfer 12 other/repo
check "  …locking it"                                        4 "open breaker issue #12" -- M 12 gh issue lock 12
check "  …through the API"                                   4 "open breaker issue #12" -- M 12 gh api -X PATCH repos/brokdar/arc/issues/12 -f state=closed
check "  …through the API with --method"                     4 "open breaker issue #12" -- M 12 gh api --method PATCH repos/brokdar/arc/issues/12 -f state=closed
check "deleting the breaker label is refused"                4 "the automerge-breaker label" -- M 12 gh label delete automerge-breaker --yes
check "editing the breaker label is refused"                 4 "the automerge-breaker label" -- M 12 gh label edit automerge-breaker --name x
check "  …even when no breaker issue is open"                4 "the automerge-breaker label" -- M "" gh label delete automerge-breaker --yes
check "  …through the API"                                   4 "the automerge-breaker label" -- M "" gh api -X DELETE repos/brokdar/arc/labels/automerge-breaker
check "it fails closed when it cannot tell which issues are open" 4 "fails closed" -- env STUB_GH_FAIL=1 bash "$SCRIPT" gh issue close 12
absent "  …without running it"                               "GH-RAN" -- env STUB_GH_FAIL=1 bash "$SCRIPT" gh issue close 12
check "closing an UNRELATED issue is fine"                   0 "GH-RAN: issue close 99" -- M 12 gh issue close 99
check "commenting on the breaker issue is fine"              0 "GH-RAN: issue comment 12" -- M 12 gh issue comment 12 --body "report"
check "closing an issue when no breaker is open is fine"     0 "GH-RAN: issue close 12" -- M "" gh issue close 12
check "reading the breaker issue is fine"                    0 "GH-RAN: issue view 12" -- M 12 gh issue view 12
check "a GET of an issue through the API is fine"            0 "GH-RAN: api repos/brokdar/arc/issues/12" -- M 12 gh api repos/brokdar/arc/issues/12
check "another label can be edited"                          0 "GH-RAN: label edit bug" -- M 12 gh label edit bug --color ff0000

echo "== as-agent: nothing outlives the command =="
absent "no git config was written to the repository"       "pushinsteadof" -- git config --list --local
absent "  …or to the global config"                        "x-access-token" -- cat "$GIT_CONFIG_GLOBAL"
check "outside the wrapper, git still pushes over SSH"     0 "git@github.com:brokdar/arc.git" -- git remote get-url --push origin

printf '\n  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
