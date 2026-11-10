"""PreToolUse hook: GitHub writes go out as the agent, never as the operator.

An agent that runs a bare `git push` or `gh pr merge` acts with the operator's
own login — their `gh` token and credential helper — so GitHub sees the
operator. That defeats the owner lane (`.github/CODEOWNERS` + the code-owner
review on `protect-main`): a bare `gh pr review --approve` is an approval GitHub
counts as the code owner's, and a bare `gh api -X PUT .../rulesets` can switch
the lane off, because the operator's token has admin rights and the bot's does
not. `scripts/as-agent.sh <command>` puts the bot's identity on one command;
this hook is what makes using it the only way through, instead of a sentence in
a prompt that a model can misread or never have seen.

Three verdicts, all exit 2 (stderr goes to the model):

  BARE WRITE — a GitHub write run without the wrapper. Refused; the message
  says to run it through `scripts/as-agent.sh`. Reads (`gh pr view`, `gh pr
  checks`, `gh api` GET, `git fetch`) are untouched: they change nothing.

  NEVER — refused even through the wrapper, because the agent has no business
  doing it under any identity: approving a PR (the owner lane's whole point),
  `--admin` merges, repository settings, rulesets, secrets, access, releases and
  tags (`release.yml` publishes images on a `v*` tag, and an App token, unlike
  GITHUB_TOKEN, does trigger it), and `gh auth` (it can print a token).

  HIDDEN — a shell fed its commands on stdin (`bash <<EOF`, `... | bash`,
  `bash -s`). Whatever the script writes, this hook cannot see it.

PARSING. Only COMMAND POSITION counts, as in `block_npm.py`, so `git commit -m
"never git push"` passes: the words are data. The command is cleaned the way a
shell reads it before it is split — backslash-newline joined, heredoc bodies
removed (a `<<` only opens one OUTSIDE quotes, comments and `$((…))`) — then
tokenized with shlex and split on separators, and each segment's first real
word is classified. Beyond block_npm it also reads INTO `bash -c`, `eval`,
`$(…)` and backticks (outside single quotes, and inside an unquoted heredoc
body, where a shell really does expand them), and it skips the OPTIONS of
`env`, `sudo`, `xargs`, `timeout`, `time`, `command` and `nice`, so
`env -u HOME git push` is a `git push`. Flags are matched the way gh's parser
reads them: `--approve`, `--approve=true`, and a short cluster such as `-ab`
(but not `-ba`, where `b` swallows the rest as its value).

THIS IS A GUARD AGAINST MISTAKES, NOT A SANDBOX. It does not see a script that
shells out, `python -c "subprocess..."`, `curl` with a token read from
`~/.config/gh/hosts.yml`, a push of a branch-looking name that is really a tag
(only a server-side tag ruleset stops that), or a `scripts/as-agent.sh` an agent
wrote itself. A real barrier would keep the operator's credentials out of the
agent's reach entirely (another OS user, a container); this stops the honest
slip, which is what actually happened before the wrapper existed.

Unbalanced quotes fail OPEN: the shell refuses that command too.

Exit codes: 0 = allow, 2 = block.
"""

import json
import os
import re
import shlex
import sys

WRAPPER = "as-agent.sh"

PUNCT = frozenset(";&|<>()\n")
SEPARATOR_ONLY = frozenset(";&|()\n")
GROUPING = frozenset({"{", "}"})
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})

# Keywords that precede the real command without being one.
KEYWORDS = frozenset({"!", "do", "elif", "else", "if", "then", "until", "while"})
# Wrappers that precede the real command, and the options of theirs that take a
# value (so `sudo -u root git push` reads `root` as a value, not a program).
WRAPPERS = {
    "command": set(),
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "exec": {"-a"},
    "nice": {"-n", "--adjustment"},
    "nohup": set(),
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "--user", "--group"},
    "time": {"-f", "-o", "--format", "--output"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a", "-l", "-i"},
}

# `gh <group> <verb>` pairs that write. Anything not listed is a read or local.
PR_WRITE = frozenset({"create", "edit", "comment", "merge", "review", "close",
                      "ready", "reopen", "lock", "unlock", "update-branch"})
ISSUE_WRITE = frozenset({"create", "edit", "comment", "close", "reopen", "delete",
                         "transfer", "pin", "unpin", "lock", "unlock", "develop"})
OTHER_WRITE = {
    "workflow": frozenset({"run", "enable", "disable"}),
    "run": frozenset({"rerun", "cancel", "delete"}),
    "label": frozenset({"create", "edit", "delete", "clone"}),
    "gist": frozenset({"create", "edit", "delete", "rename"}),
}
# Never, wrapped or not.
REPO_NEVER = frozenset({"create", "edit", "delete", "rename", "archive",
                        "unarchive", "sync", "deploy-key", "autolink"})
SECRET_NEVER = frozenset({"set", "delete", "remove"})
RELEASE_NEVER = frozenset({"create", "edit", "delete", "upload", "delete-asset"})
# A non-GET `gh api` to any of these changes who can do what, or ships a release.
API_NEVER = re.compile(
    r"rulesets|/protection|collaborators|invitations|actions/(secrets|variables|"
    r"permissions|runners)|dependabot/secrets|/hooks|/keys|environments|/transfer|"
    r"/visibility|(^|/)installation|/releases|/git/refs/tags|/tags"
)
# `repos/<owner>/<repo>` itself: PATCH changes the default branch, visibility, ...
API_REPO_ROOT = re.compile(r"^/?repos/[^/]+/[^/]+/?$")
API_REVIEWS = re.compile(r"(^|/)pulls/\d+/reviews")


def is_assignment(token: str) -> bool:
    name, sep, _ = token.partition("=")
    return bool(sep) and name.isidentifier()


# ── cleaning the command the way a shell reads it ─────────────────────────────


def _heredoc_delimiter(text: str, i: int):
    """Parse `<<[-]WORD` at text[i]. Returns (delimiter, strip_tabs, quoted, end)."""
    j = i + 2
    strip = j < len(text) and text[j] == "-"
    if strip:
        j += 1
    while j < len(text) and text[j] in " \t":
        j += 1
    if j < len(text) and text[j] in "'\"":
        end = text.find(text[j], j + 1)
        if end == -1:
            return None
        return text[j + 1 : end], strip, True, end + 1
    match = re.match(r"\\?([A-Za-z0-9_.\-]+)", text[j:])
    if not match:
        return None
    return match.group(1), strip, text[j] == "\\", j + match.end()


def strip_heredocs(command: str) -> tuple[str, list[str]]:
    """Drop heredoc bodies. Returns (command without them, bodies a shell EXPANDS).

    A `<<` opens a heredoc only outside single/double quotes, outside a comment
    and outside `$((…))`. Matching the shell here matters in both directions: a
    `<<WORD` inside a commit message must not swallow the lines after it (a real
    command hiding there), and a real heredoc must end at its exact delimiter
    line, which may contain `-`.
    """
    out: list[str] = []
    expanding: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    i, n = 0, len(command)
    in_s = in_d = False
    while i < n:
        c = command[i]
        if in_s:
            out.append(c)
            in_s = c != "'"
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
            continue
        if in_d:
            out.append(c)
            in_d = c != '"'
            i += 1
            continue
        if c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            end = command.find("\n", i)
            end = n if end == -1 else end
            out.append(command[i:end])
            i = end
            continue
        elif command.startswith("$((", i):
            end = command.find("))", i + 3)
            end = n if end == -1 else end + 2
            out.append(command[i:end])
            i = end
            continue
        elif command.startswith("<<", i) and not command.startswith("<<<", i) and (i == 0 or command[i - 1] != "<"):
            parsed = _heredoc_delimiter(command, i)
            if parsed:
                word, strip, quoted, end = parsed
                pending.append((word, strip, quoted))
                out.append(command[i:end])
                i = end
                continue
        elif c == "\n" and pending:
            out.append("\n")
            i += 1
            for word, strip, quoted in pending:
                body: list[str] = []
                while i < n:
                    end = command.find("\n", i)
                    line = command[i : n if end == -1 else end]
                    i = n if end == -1 else end + 1
                    if (line.lstrip("\t") if strip else line) == word:
                        break
                    body.append(line)
                if not quoted:
                    expanding.append("\n".join(body))
            pending = []
            continue
        out.append(c)
        i += 1
    return "".join(out), expanding


def substitutions(text: str, quotes: bool = True) -> list[str]:
    """The commands inside `$(…)` and backticks that a shell would RUN.

    Single-quoted text is literal, so `'$(git push)'` is skipped; double-quoted
    text is not, so `"$(git push)"` is found. `quotes=False` is for heredoc
    bodies, where quote characters mean nothing.
    """
    found: list[str] = []
    i, n = 0, len(text)
    in_s = in_d = False
    while i < n:
        c = text[i]
        if quotes and in_s:
            in_s = c != "'"
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if quotes and c == "'" and not in_d:
            in_s = True
        elif quotes and c == '"':
            in_d = not in_d
        elif quotes and c == "#" and not in_d and (i == 0 or text[i - 1] in " \t\n;&|("):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        elif text.startswith("$((", i):
            end = text.find("))", i + 3)
            i = n if end == -1 else end + 2
            continue
        elif text.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            found.append(text[i + 2 : j - 1] if depth == 0 else text[i + 2 :])
            i = j
            continue
        elif c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            found.append(text[i + 1 : j])
            i = j + 1
            continue
        i += 1
    return found


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    try:
        tokens = list(lexer)
    except ValueError:
        return []  # unbalanced quotes: the shell refuses it as well

    out: list[list[str]] = []
    current: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token and set(token) <= PUNCT:
            if set(token) <= SEPARATOR_ONLY:
                out.append(current)
                current = []
            else:  # a redirection: drop it, its target, and a leading fd number
                if current and current[-1].isdigit():
                    current.pop()
                skip_next = True
            continue
        if token in GROUPING:
            out.append(current)
            current = []
            continue
        current.append(token)
    out.append(current)
    return [s for s in out if s]


def program_and_args(segment: list[str]) -> list[str]:
    """Strip `VAR=x`, keywords and wrappers (with their options) down to the program."""
    i = 0
    while i < len(segment):
        token = segment[i]
        if is_assignment(token) or token in KEYWORDS:
            i += 1
        elif token in WRAPPERS:
            valued = WRAPPERS[token]
            i += 1
            while i < len(segment) and segment[i].startswith("-") and segment[i] != "--":
                i += 2 if segment[i] in valued else 1
            if token == "timeout":
                i += 1  # the duration
        else:
            break
    return segment[i:]


# ── classifying a command ─────────────────────────────────────────────────────


def has_flag(args: list[str], long: str, short: str | None = None, value_letters: str = "") -> bool:
    """`--long`, `--long=x`, or a short cluster holding `short` before any value letter."""
    for a in args:
        if a == long or a.startswith(long + "="):
            return True
        if short and a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for ch in a[1:]:
                if ch == short:
                    return True
                if ch in value_letters:
                    break  # the rest of the cluster is that flag's value
    return False


def git_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    takes_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--exec-path", "--super-prefix", "--config-env"}
    i = 0
    while i < len(args):
        if args[i] in takes_value:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return args[i], args[i + 1 :]
    return None, []


def gh_words(args: list[str]) -> list[str]:
    """The non-flag words of a gh command, skipping flags that take a value."""
    takes_value = {"-R", "--repo", "--hostname"}
    words: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in takes_value:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            words.append(args[i])
            i += 1
    return words


def api_request(args: list[str]) -> tuple[str, str | None, str, bool]:
    """(effective method, path, all text, reads its body from a file)."""
    method = None
    body = False
    from_file = False
    path = None
    takes_value = {"-H", "--header", "-q", "--jq", "-t", "--template", "--cache",
                   "--hostname", "-p", "--preview"}
    i = 0
    while i < len(args):
        a = args[i]
        value = args[i + 1] if i + 1 < len(args) else ""
        if a in ("-X", "--method"):
            method = value.upper() or None
            i += 2
        elif a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
            i += 1
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].upper()
            i += 1
        elif a in ("-f", "-F", "--field", "--raw-field", "--input"):
            body = True
            from_file = from_file or a == "--input" or value.partition("=")[2].startswith("@")
            i += 2
        elif a.startswith(("--field=", "--raw-field=", "--input=")) or (
            a.startswith(("-f", "-F")) and len(a) > 2 and not a.startswith("--")
        ):
            body = True
            from_file = from_file or a.startswith("--input=") or "=@" in a
            i += 1
        elif a in takes_value:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            path = path or a
            i += 1
    return method or ("POST" if body else "GET"), path, " ".join(args), from_file


def pushes_a_tag(rest: list[str]) -> bool:
    """A push that creates, moves or deletes a tag. `v*` names are treated as tags:
    release.yml publishes on them, and a branch of that shape is not worth the risk."""
    for a in rest:
        if a in ("--tags", "--mirror", "--follow-tags", "tag") or "refs/tags" in a:
            return True
        if not a.startswith("-"):
            destination = a.split(":")[-1].lstrip("+")
            if re.fullmatch(r"v\d[^/]*", destination):
                return True
    return False


def inspect(cmd: list[str]) -> tuple[str | None, str | None]:
    """(write, never): what this command writes, and whether it is never allowed."""
    program = os.path.basename(cmd[0])
    args = cmd[1:]

    if program == "git":
        sub, rest = git_subcommand(args)
        if sub == "push":
            if pushes_a_tag(rest):
                return "git push", "pushing a tag publishes a release"
            return "git push", None
        return None, None

    if program != "gh":
        return None, None

    words = gh_words(args)
    group = words[0] if words else ""
    verb = words[1] if len(words) > 1 else ""

    if group == "pr":
        if verb == "review" and has_flag(args, "--approve", "a", value_letters="bF"):
            return "gh pr review", "approving a PR is the operator's, not an agent's"
        if verb == "merge" and has_flag(args, "--admin"):
            return "gh pr merge", "--admin skips the branch rules"
        return (f"gh pr {verb}" if verb in PR_WRITE else None), None
    if group == "issue":
        return (f"gh issue {verb}" if verb in ISSUE_WRITE else None), None
    if group == "api":
        # `args` still starts with the group word; the request is what follows it.
        method, path, text, from_file = api_request(args[args.index("api") + 1 :])
        lowered = text.lower()
        if path == "graphql":
            if "addpullrequestreview" in lowered or "submitpullrequestreview" in lowered:
                if "approve" in lowered:
                    return "gh api graphql", "approving a PR is the operator's, not an agent's"
            if "mutation" in lowered or from_file:
                return "gh api graphql (mutation)", None
            return None, None
        if method == "GET":
            return None, None
        if API_REVIEWS.search(path or "") and "approve" in lowered:
            return f"gh api -X {method}", "approving a PR is the operator's, not an agent's"
        if "refs/tags" in text or (path and (API_NEVER.search(path) or API_REPO_ROOT.match(path))):
            return f"gh api -X {method}", "it changes repository settings, access, tags or releases"
        return f"gh api -X {method}", None
    if group == "repo" and verb in REPO_NEVER:
        return f"gh repo {verb}", "repository settings are the operator's"
    if group in ("secret", "variable") and verb in SECRET_NEVER:
        return f"gh {group} {verb}", "secrets and variables are the operator's"
    if group == "release" and verb in RELEASE_NEVER:
        return f"gh release {verb}", "a release publishes images"
    if group == "auth":
        if verb != "status":
            return f"gh auth {verb}", "it exposes or changes the operator's credentials"
        if has_flag(args, "--show-token", "t", value_letters="h"):
            return "gh auth status", "it prints the operator's token"
    if group in OTHER_WRITE and verb in OTHER_WRITE[group]:
        return f"gh {group} {verb}", None
    return None, None


# ── the verdict ───────────────────────────────────────────────────────────────


def violations(command: str, wrapped: bool = False) -> list[str]:
    command = command.replace("\\\n", "")  # a shell joins a continued line first
    cleaned, expanding = strip_heredocs(command)
    found: list[str] = []
    for segment in segments(cleaned):
        found.extend(segment_violations(segment, wrapped))
    for inner in substitutions(cleaned):
        found.extend(violations(inner, False))
    for body in expanding:
        for inner in substitutions(body, quotes=False):
            found.extend(violations(inner, False))
    return found


def shell_input(args: list[str]) -> tuple[bool, str | None]:
    """(was -c given, the first positional): the command string or the script file."""
    has_c = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if a == "--":
            i += 1
            break
        if a[:1] in "-+" and len(a) > 1:
            has_c = has_c or (a[0] == "-" and not a.startswith("--") and "c" in a[1:])
            i += 1
            continue
        break
    return has_c, (args[i] if i < len(args) else None)


def segment_violations(segment: list[str], wrapped: bool) -> list[str]:
    cmd = program_and_args(segment)
    if not cmd:
        return []
    program = os.path.basename(cmd[0])

    if program == WRAPPER:
        return segment_violations(cmd[1:], True) if len(cmd) > 1 else []
    if program == "eval":
        return violations(" ".join(cmd[1:]), wrapped)
    if program in SHELLS:
        args = cmd[1:]
        if "--version" in args or "--help" in args:
            return []
        has_c, positional = shell_input(args)
        if has_c:
            return violations(positional, wrapped) if positional else []
        if positional is None:
            return [
                f"Blocked: `{program}` with no script reads its commands from stdin "
                "(a heredoc, a pipe, `-s`), which hides what it runs from this guard. "
                "Pass the commands directly, or write them to a script file and run that."
            ]
        if os.path.basename(positional) == WRAPPER:
            rest = args[args.index(positional) + 1 :]
            return segment_violations(rest, True) if rest else []
        return []

    write, never = inspect(cmd)
    if never:
        return [
            f"Blocked: `{write}` is not something an agent does, wrapped or not — "
            f"{never}. Leave it to the operator and say so in your report."
        ]
    if write and not wrapped:
        return [
            f"Blocked: `{write}` run bare acts as the operator's GitHub login. Run it as "
            f"the agent: `scripts/as-agent.sh {' '.join(shlex.quote(c) for c in cmd)}`. "
            "If that exits 3 there is no agent identity on this host: stop and report "
            "it — never retry the command without the wrapper."
        ]
    return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # fail open on malformed input, as block_npm does

    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str):
        return 0

    found = violations(command)
    if found:
        print("\n".join(dict.fromkeys(found)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
