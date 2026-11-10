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
check "a missing token command refuses"                    3 "NOT run" -- env AGENT_TOKEN_CMD=no-such-cmd bash "$SCRIPT" true
check "a failing token command refuses"                    3 "failed" -- env AGENT_TOKEN_CMD=tok-fail bash "$SCRIPT" true
check "an empty token refuses"                             3 "installation token" -- env AGENT_TOKEN_CMD=tok-empty bash "$SCRIPT" true
check "a user token (gho_) refuses"                        3 "installation token" -- env AGENT_TOKEN_CMD=tok-user bash "$SCRIPT" true
check "a token with whitespace refuses"                    3 "installation token" -- env AGENT_TOKEN_CMD=tok-space bash "$SCRIPT" true
absent "a refusal does not run the command"                "RAN" -- env AGENT_TOKEN_CMD=tok-fail bash "$SCRIPT" echo RAN

echo "== as-agent: the wrapped command acts as the bot =="
export AGENT_TOKEN_CMD=tok-ok
check "gh gets the installation token, dots and all"       0 "ghs_stubInstallationToken123.eyJhbGciOiJSUzI1NiJ9.c2lnbmF0dXJl-x_y" -- bash "$SCRIPT" bash -c 'echo "$GH_TOKEN"'
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
check "the command's exit code is passed through"          7 "" -- bash "$SCRIPT" bash -c 'exit 7'

echo "== as-agent: nothing outlives the command =="
absent "no git config was written to the repository"       "pushinsteadof" -- git config --list --local
absent "  …or to the global config"                        "x-access-token" -- cat "$GIT_CONFIG_GLOBAL"
check "outside the wrapper, git still pushes over SSH"     0 "git@github.com:brokdar/arc.git" -- git remote get-url --push origin

printf '\n  %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
