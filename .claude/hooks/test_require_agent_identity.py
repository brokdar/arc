"""Behavior matrix for require_agent_identity.py — run directly:
    python3 .claude/hooks/test_require_agent_identity.py

WHY THE MATRIX IS SHAPED LIKE THIS. The hook has two ways to be wrong and both
are silent. A false negative is a bare write that reaches GitHub as the operator
(or an approval an agent gave as the code owner). A false positive blocks a
legitimate command — a `git commit -m "never git push"`, a PR body in a heredoc,
a read — with a message about identity, and the agent learns to route around the
hook instead of through the wrapper. So every case below is a command that was
actually run, or nearly run, in this repo's agent flows.

The last group pins the WIRING, not the parser: a hook that is not registered in
`.claude/settings.json`, or an allow list that lets the bare forms through
unprompted, is a guard that is not there.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).with_name("require_agent_identity.py")
SETTINGS = Path(__file__).resolve().parents[1] / "settings.json"

W = "scripts/as-agent.sh"

ALLOW = [
    # --- reads and local work: nothing here changes GitHub ---
    "git status",
    "git diff main...HEAD --stat",
    "git log --oneline -5",
    "git fetch origin",
    'git commit -m "feat(x): a thing"',
    "git switch -c feat/x",
    "git -C .claude/worktrees/x status --porcelain -uall",
    "git ls-remote --heads origin feat/x",
    "git worktree add .claude/worktrees/x -b feat/x origin/main",
    "gh pr view 5 --json state,mergeStateStatus",
    "gh pr checks 5",
    "gh pr list --state open",
    "gh pr diff 5",
    "gh pr checkout 5",
    "gh issue view 3",
    "gh issue list --state all --search cache",
    "gh run list --limit 5",
    "gh repo view --json nameWithOwner",
    "gh auth status",
    "gh api repos/brokdar/arc/pulls --jq length",
    "gh api -X GET repos/brokdar/arc/rulesets",
    'gh api graphql -f query="{ viewer { login } }"',
    "gh api repos/brokdar/arc/rulesets/1 --jq .rules",
    # --- the words appear as data, not as a program ---
    'git commit -m "docs: never git push bare"',
    'echo "gh pr merge 5"',
    "rg 'git push|gh pr merge' .claude",
    'gh pr view 5 --jq \'.body | contains("gh pr merge")\'',
    'python3 -c "print(1)"',
    # --- a write through the wrapper ---
    f"{W} git push -u origin feat/x",
    f"./{W} git push",
    f'cd .claude/worktrees/x && {W} gh pr create --base main --title "feat(x): y" --body-file b.md',
    f"{W} gh pr merge 5 --squash --auto",
    f"{W} gh pr merge 5 --squash",
    f"{W} gh pr comment 5 --body-file c.md",
    f"{W} gh pr edit 5 --title 'fix(x): y'",
    f"{W} gh pr review 5 --comment --body hi",
    f"{W} gh pr close 5 --delete-branch",
    f"{W} gh issue create --title t --label bug --body-file b.md",
    f"{W} gh api -X PATCH repos/brokdar/arc/pulls/5 -f title=x",
    f'{W} gh api graphql -f query="mutation {{ resolveReviewThread(input: {{threadId: \\"x\\"}}) {{ thread {{ id }} }} }}"',
    f"{W} git -C .claude/worktrees/x push",
    f"{W} git -c core.hooksPath=/dev/null push -q origin probe/x",
    f"{W} git push > /tmp/push.log 2>&1 &",
    f"cd wt && {W} git push -u origin feat/x > /tmp/push-feat-x.log 2>&1 &",
    f"GH_TOKEN=x {W} gh pr edit 5 --title t",
    f"bash {W} gh pr comment 5 --body hi",
    f"timeout 600 {W} git push",
    # --- heredoc bodies and here-strings are text ---
    "git commit -F - <<'EOF'\nfeat(x): y\n\ngh pr merge 5\ngit push\nEOF",
    f"{W} gh pr create --title t --body-file - <<EOF\ngit push\ngh pr review 5 --approve\nEOF",
    'cat <<< "git push"',
    # --- shapes that must not crash the hook ---
    "",
    'echo "unbalanced',
    "git",
    "gh",
    "gh pr",
    # --- the parser must not over-block: these are text, or are not writes ---
    'git commit -m "docs: explain <<EOF heredocs"',
    "echo $((1<<n))",
    "echo '$(git push)'",
    "echo hi # $(git push)",
    "git commit -m \"$(cat <<'EOF'\nsummary: mentions gh pr merge and `git push`\nEOF\n)\" && echo ok",
    "cat <<EOF\nplain text, no substitution\nEOF",
    "bash scripts/check.sh",
    "bash -c 'echo hi'",
    "bash -o pipefail -c 'echo hi'",
    'sh -c "git status"',
    "bash --version",
    W + " gh pr review 5 -b approve",
    W + " gh pr review 5 -ba x",
    W + " gh pr review 5 --body-file approve.md",
    W + " gh pr merge 5 --squash --delete-branch",
    W + " gh api repos/o/r/pulls/5/reviews",
    W + " gh api repos/o/r/pulls/5/comments -f body=x",
    W + " gh api -X PATCH repos/o/r/issues/5 -f state=closed",
    W + " git push origin feat/v2-parser",
    W + " git push -u origin ci/agent-write-guard",
    W + " gh pr review 5 --request-changes --body no",
]

BARE = [
    "git push",
    "git push -u origin HEAD",
    "git -C .claude/worktrees/x push",
    "git -c core.hooksPath=/dev/null push -q origin x",
    "cd .claude/worktrees/x && git push -u origin feat/x",
    "git add -u && git commit -q -m x && git push",
    "echo hi; git push",
    "sudo git push",
    "env FOO=1 git push",
    "timeout 30 git push",
    "FOO=1 git push",
    "(git push)",
    "echo $(git push)",
    "git push > /tmp/push.log 2>&1 &",
    'bash -c "git push"',
    'sh -lc "gh pr merge 5 --squash"',
    'eval "git push"',
    "gh pr create --title t --body-file b.md",
    "gh pr merge 5 --squash",
    "gh pr merge 5 --squash --auto",
    "gh pr merge -R brokdar/arc 5",
    "gh pr review 5 --comment --body hi",
    "gh pr comment 5 --body x",
    "gh pr edit 5 --title t",
    "gh pr close 5",
    "gh pr ready 5",
    "gh pr reopen 5",
    "gh issue create --title t",
    "gh issue comment 5 --body x",
    "gh issue close 5",
    "gh api repos/brokdar/arc/pulls/5 -f title=x",
    "gh api -X DELETE repos/brokdar/arc/git/refs/heads/foo",
    "gh api --method PATCH repos/brokdar/arc/pulls/5",
    'gh api graphql -f query="mutation { x }"',
    "gh workflow run ci.yml",
    "gh label create x",
    "gh gist create f.txt",
    "GH_TOKEN=$(agent-token) gh pr create --title t",  # the old, unwrapped pattern
    # a bare write behind a wrapped one is still bare
    f"{W} git status && git push",
    f"echo {W}; git push",
    # --- found by the first adversarial review ---
    'echo "$(git push)"',
    "echo `git push`",
    'echo "`git push`"',
    'echo "$(git -C x push)"',
    "env -i git push",
    "env -u HOME git push",
    "sudo -u root git push",
    "echo x | xargs -n1 git push",
    "timeout -s KILL 30 git push",
    "time -p git push",
    "command -p git push",
    "nice -n 5 git push",
    "gh pr \\\n  merge 5 --squash",
    "git \\\npush",
    "2>&1 git push",
    "bash -c -e 'git push'",
    'bash -o pipefail -c "git push"',
    'git commit -m "docs <<EOF"\ngit push',
    'echo "<<EOF"\ngit push',
    "# c <<EOF\ngit push",
    "echo $((1<<n))\ngit push",
    "cat <<EOF-BODY\nx\nEOF-BODY\ngit push",
    "cat <<EOF\n$(git push)\nEOF",
    "gh api graphql -F query=@q.graphql",
    "gh api graphql --input q.json",
]

# Refused even through the wrapper: the agent has no business doing these as anyone.
NEVER = [
    f"{W} gh pr review 5 --approve",
    f"{W} gh pr review 5 -a",
    f"{W} gh pr review 5 --approve --body lgtm",
    f"{W} gh pr merge 5 --squash --admin",
    f"{W} gh api -X PUT repos/brokdar/arc/rulesets/20303099 --input r.json",
    f"{W} gh api -X PATCH repos/brokdar/arc/branches/main/protection",
    f"{W} gh api -X PUT repos/brokdar/arc/collaborators/someone",
    f"{W} gh api -X PUT repos/brokdar/arc/actions/permissions",
    f"{W} gh repo edit --delete-branch-on-merge",
    f"{W} gh repo delete brokdar/arc --yes",
    f"{W} gh secret set X --body y",
    f"{W} gh variable delete X",
    f"{W} gh release create v1.0.0",
    f"{W} git push origin --tags",
    f"{W} git push origin refs/tags/v1.0.0",
    f"{W} git push --mirror",
    f"{W} gh auth token",
    f"{W} gh auth status --show-token",
    f"bash -c '{W} gh pr review 5 --approve'",
    # the wrapper does not launder these when they are bare either
    "gh pr review 5 --approve",
    "gh auth token",
    "gh api -X PUT repos/brokdar/arc/rulesets/1",
    # --- found by the first adversarial review: flag spellings gh accepts ---
    W + " gh pr review 5 --approve=true",
    W + " gh pr review 5 -ab lgtm",
    W + " gh pr review 5 \\\n  --approve",
    W + " gh pr merge 5 --admin=true",
    W + " gh pr merge 5 --squash \\\n  --admin",
    W + " gh auth status -t",
    "gh auth status -t",
    # --- every way to push a tag (release.yml publishes on v*) ---
    W + " git push origin v1.0.0",
    W + " git push origin tag v1.0.0",
    W + " git push --follow-tags",
    W + " git push origin HEAD:refs/tags/v1",
    W + " git push origin '+refs/tags/*:refs/tags/*'",
    W + " git push origin :refs/tags/v1",
    W + " git push origin HEAD:v2.0",
    W + " gh api -X POST repos/o/r/git/refs -f ref=refs/tags/v1 -f sha=abc",
    # --- the API routes to the same actions ---
    W + " gh api repos/o/r/pulls/5/reviews -f event=APPROVE",
    W + " gh api graphql -f query='mutation { addPullRequestReview(input:{pullRequestId:\"x\", event:APPROVE}) { clientMutationId } }'",
    W + " gh api -X PATCH repos/o/r -f default_branch=x",
]

# A shell fed its commands on stdin hides them from the guard.
STDIN = [
    "bash <<'EOF'\ngit push\nEOF",
    'echo "git push" | bash',
    'bash -s <<< "git push"',
    "sh",
    "echo x | bash",
]


def run(command):
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stderr


failures = []


def expect(command, want, needle=None, forbid=None):
    code, err = run(command)
    ok = code == (0 if want == "allow" else 2)
    if ok and needle and needle not in err:
        ok = False
    if ok and forbid and forbid in err:
        ok = False
    if not ok:
        failures.append((command, want, code, err.strip()))


for c in ALLOW:
    expect(c, "allow")
for c in BARE:
    expect(c, "block", needle="Run it as the agent")
# A NEVER must say so, not merely block: the bare-write message also contains the
# word "operator", so the needle is the sentence only a NEVER carries, and the
# bare-write instruction ("run it wrapped") must be absent — telling an agent to
# wrap an approval is the one message that makes the hook cause the harm.
for c in NEVER:
    expect(c, "block", needle="Leave it to the operator", forbid="Run it as the agent")
for c in STDIN:
    expect(c, "block", needle="stdin")

# The two messages must tell the model different things: "wrap it" for a bare
# write, "stop, it is the operator's" for a never. Swapping them sends an agent
# to retry an approval through the wrapper.
_, bare_msg = run("gh pr merge 5 --squash")
_, never_msg = run(f"{W} gh pr review 5 --approve")
if "Run it as the agent" not in bare_msg or "never retry the command without the wrapper" not in bare_msg:
    failures.append(("bare message", "says to wrap and never retry bare", 0, bare_msg))
if "Leave it to the operator" not in never_msg or "Run it as the agent" in never_msg:
    failures.append(("never message", "says it is the operator's, not to wrap", 0, never_msg))

# Malformed input fails open, as block_npm does.
for raw in ("", "not json", "[]", '{"tool_input": {"command": 5}}'):
    proc = subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True)
    if proc.returncode != 0:
        failures.append((f"stdin={raw!r}", "allow", proc.returncode, proc.stderr.strip()))

# --- the wiring: a hook that is not registered is a guard that is not there ----
settings = json.loads(SETTINGS.read_text())
hook_cmds = [
    h["command"]
    for group in settings["hooks"]["PreToolUse"]
    if group["matcher"] == "Bash"
    for h in group["hooks"]
]
for script in ("require_agent_identity.py", "block_npm.py"):
    if not any(script in c for c in hook_cmds):
        failures.append((".claude/settings.json", f"registers {script} on PreToolUse/Bash", 0, str(hook_cmds)))

allow = settings["permissions"]["allow"]
deny = settings["permissions"].get("deny", [])
if "Bash(scripts/as-agent.sh:*)" not in allow:
    failures.append((".claude/settings.json", "allows the wrapper", 0, str(allow)))
# Defence in depth, independent of the parser above: these prefixes stay denied
# even if a future edit to the hook's parsing misses a form.
for rule in (
    "Bash(git push:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr merge:*)",
    "Bash(gh pr review:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr comment:*)",
    "Bash(gh issue create:*)",
    "Bash(gh auth token:*)",
):
    if rule not in deny:
        failures.append((".claude/settings.json", f"denies {rule}", 0, "missing from permissions.deny"))
    if rule in allow:
        failures.append((".claude/settings.json", f"does not also allow {rule}", 0, "in permissions.allow"))

total = len(ALLOW) + len(BARE) + len(NEVER) + len(STDIN)
if failures:
    print(f"{len(failures)} FAILED of {total} commands plus the wiring checks:")
    for command, want, code, err in failures:
        print(f"  want {want!r}, exit {code}: {command!r}\n      {err}")
    sys.exit(1)
print(f"all {total} commands and the settings wiring behave ({len(ALLOW)} allow, {len(BARE)} bare, {len(NEVER)} never, {len(STDIN)} stdin)")
