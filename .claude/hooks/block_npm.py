"""PreToolUse hook: block npm/npx/pnpm/yarn before they create stray lockfiles.

This repo uses bun (frontend) and uv (backend) exclusively.

Exit codes: 0 = allow, 2 = block (stderr is shown to the model).
"""

import json
import re
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # fail open on malformed input

    command = payload.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0

    if re.search(r"(^|[;&|\s])(npm|npx|pnpm|yarn)(\s|$)", command):
        print(
            "Blocked: use 'bun'/'bunx' (frontend) or 'uv'/'uvx' (backend) "
            "instead of npm/npx/pnpm/yarn.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
