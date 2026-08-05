"""PreToolUse hook: block npm/npx/pnpm/yarn before they create stray lockfiles.

This repo uses bun (frontend) and uv (backend) exclusively.

Only *command position* counts. A previous version regex-scanned the whole
command string, which blocked `git commit -m "chore: drop npm from ci"` and
`gh pr create --body "replaces npm with bun"` — the words appear as data, not
as programs. The command is tokenized with shlex (quoting-aware, so text
inside quotes stays one token), split on shell separators, and only the first
real word of each segment is checked.

Deliberate blind spot: a package manager hidden inside a quoted string handed
to another interpreter (`bash -c "npm ci"`) is not seen. Blocking it would
mean re-blocking every quoted mention, which is the bug this replaced.

Exit codes: 0 = allow, 2 = block (stderr is shown to the model).
"""

import json
import os
import shlex
import sys

BLOCKED = frozenset({"npm", "npx", "pnpm", "yarn"})

# Tokens after which the next word is a command again. shlex emits runs of
# punctuation as standalone tokens (`&&`, `;`, `\n\n`), so separators are
# recognized by their characters; grouping tokens are listed literally.
SEPARATOR_CHARS = frozenset(";&|\n")
GROUPING = frozenset({"(", ")", "{", "}"})

# Words that precede the real command instead of being one.
TRANSPARENT = frozenset(
    {
        "!",
        "command",
        "do",
        "elif",
        "else",
        "env",
        "exec",
        "if",
        "nohup",
        "sudo",
        "then",
        "time",
        "until",
        "while",
        "xargs",
    }
)


def is_assignment(token: str) -> bool:
    """True for a leading `VAR=value` environment assignment."""
    name, sep, _ = token.partition("=")
    return bool(sep) and name.isidentifier()


def blocked_command(command: str) -> str | None:
    """Return the offending program name, or None if the command is fine."""
    # `\n` is a separator, not whitespace: without this a newline-joined
    # command reads as one long argument list and only its first word counts.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    try:
        tokens = list(lexer)
    except ValueError:
        return None  # unbalanced quotes — fail open

    at_command_position = True
    for token in tokens:
        if token in GROUPING or (token and set(token) <= SEPARATOR_CHARS):
            at_command_position = True
            continue
        if not at_command_position:
            continue
        if is_assignment(token) or token in TRANSPARENT:
            continue  # still looking for the program name
        at_command_position = False
        if os.path.basename(token) in BLOCKED:
            return os.path.basename(token)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # fail open on malformed input

    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0

    offender = blocked_command(command)
    if offender is not None:
        print(
            f"Blocked: '{offender}' is not used in this repo — use 'bun'/'bunx' "
            "(frontend) or 'uv'/'uvx' (backend).",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
