"""Behavior matrix for block_npm.py — run directly: python3 .claude/hooks/test_block_npm.py

Package-manager names are assembled at runtime so this file's own commands
never trip the live PreToolUse hook when the suite is invoked from a shell.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).with_name("block_npm.py")

PM = ["n" + "pm", "n" + "px", "p" + "npm", "y" + "arn"]
NPM, NPX, PNPM, YARN = PM

CASES = [
    # --- quoted mentions (the false positives the old regex hit) ---
    (f'git commit -m "chore: drop {NPM} from ci"', "allow"),
    (f'gh pr create --body "replaces {NPM} with bun"', "allow"),
    (f"rg '{NPM}|{YARN}' frontend/", "allow"),
    (f'echo "{NPM} install" > notes.txt', "allow"),
    (f"python3 -c \"print('{NPM}')\"", "allow"),
    (f'gh issue comment 1 --body "we do not use {YARN}; bun only"', "allow"),
    # --- real invocations, must block ---
    (f"{NPM} install", "block"),
    (f"{NPX} create-next-app", "block"),
    (f"cd frontend && {NPM} ci", "block"),
    (f"echo hi; {YARN} add x", "block"),
    (f"FOO=1 {PNPM} i", "block"),
    (f"sudo {NPM} install -g typescript", "block"),
    (f"/usr/local/bin/{NPM} ci", "block"),
    (f"cd frontend; {NPX} tsc", "block"),
    (f"just lint || {NPM} run lint", "block"),
    (f"cd frontend\n{NPM} run build", "block"),
    (f"env FOO=1 {NPM} test", "block"),
    (f"({NPM} install)", "block"),
    # --- allowed tooling ---
    ("bun install", "allow"),
    ("bunx playwright install --with-deps chromium", "allow"),
    ("just check && bun run build", "allow"),
    ("uv run pytest -n auto", "allow"),
    # --- fail open on junk ---
    (f'echo "{NPM}', "allow"),  # unbalanced quote
]

MALFORMED = [
    ("not json", "}{"),
    ("no tool_input", "{}"),
    ("command not a string", json.dumps({"tool_input": {"command": ["x"]}})),
]


def run(payload: str) -> int:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True
    ).returncode


def main() -> int:
    failures = 0
    for cmd, expected in CASES:
        code = run(json.dumps({"tool_input": {"command": cmd}}))
        got = "block" if code == 2 else "allow"
        ok = got == expected
        failures += not ok
        print(f"{'OK ' if ok else 'FAIL'} [{got:5}] expected={expected:5} :: {cmd!r}")
    for label, payload in MALFORMED:
        code = run(payload)
        failures += code != 0
        print(f"{'OK ' if code == 0 else 'FAIL'} [exit {code}] expected=allow :: {label}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
