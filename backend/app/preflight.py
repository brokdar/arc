"""Say what is wrong with the configuration before anything crashes on it.

Two modes, one module:

* ``python -m app.preflight`` — the boot gate. Compose runs it in front of
  ``alembic upgrade head`` (api) and ``python -m app.mcp.main`` (mcp). It builds
  `Settings` from the process environment, and on failure prints one line per
  problem — key first, remedy last — and exits 1, with no traceback. On success
  it is silent.
* ``python -m app.preflight --file [PATH]`` (`just env-check`) — the same
  verdict on a `.env` file on demand, plus the two things a running process
  cannot see: required keys with no live line in the file, and keys in the file
  that no setting consumes. Any finding, warning or not, exits 1, so an
  operator or CI can gate on it.

Why a separate entrypoint and not a try/except in ``alembic/env.py``: Alembic is
one of several `Settings` consumers (api, mcp, scheduler). One gate in front of
all of them beats a catch site in each, and a preflight that itself crashes
still stops compose's ``&&`` chain before Alembic runs.

Choices that bind (each pinned by ``tests/unit/test_preflight.py``):

* **Key names only, never values.** A missing secret is not prompted for, and a
  present one is not echoed — the output ends up in container logs. The
  messages `parse_api_keys` raises are documented never to hold key material,
  and pydantic's ``hide_input_in_errors`` is already on.
* **Errors block boot, warnings do not.** A write-only MCP key breaks an agent,
  not the stack; refusing to boot over it would punish the wrong flow. In
  ``--file`` mode warnings still fail the command, because there the operator
  asked "is this file right".
* **The key sets come from the model** (`settings_env_keys`,
  `REQUIRED_ENV_KEYS`), not from a list kept here. Keys consumed outside
  `Settings` are the explicit allowlist below, not a heuristic.
* **The `.env` is parsed by hand**, live ``KEY=value`` lines only. A commented
  ``# KEY=`` is missing, which is what the real parser thinks too.
"""

import argparse
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.config import (
    REPO_ROOT,
    REQUIRED_ENV_KEYS,
    Settings,
    settings_env_keys,
)
from app.mcp.auth import FIELD_SEPARATOR, Scope, parse_api_keys

DEFAULT_ENV_FILE = REPO_ROOT / ".env"

#: Keys that legitimately live in `.env` without being a `Settings` field:
#: compose reads the first two families, Caddy and the Next.js build read the
#: next two, Playwright the last. Anything else unknown is a leftover.
_FOREIGN_PREFIXES = ("COMPOSE_", "NEXT_PUBLIC_", "E2E_")
_FOREIGN_KEYS = frozenset({"CADDY_SITE_ADDRESS"})

_GUARD_PREFIX = "Value error, Insecure production configuration: "
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_KEY_FIRST = re.compile(r"^([A-Z][A-Z0-9_]*) (.*)$")

_REMEDY = (
    "compare your .env with .env.example (`just env-check` lists the "
    "differences, `just init` writes a fresh file)"
)

#: One finding: (env key, message). The key leads the printed line.
Finding = tuple[str, str]


def _env_key(loc: tuple[int | str, ...]) -> str:
    return "__".join(str(part).upper() for part in loc)


def validation_findings(exc: ValidationError) -> list[Finding]:
    """Turn a `Settings` ValidationError into one finding per problem.

    The production guard raises a single ValueError whose message joins its
    problems with ``"; "`` and starts each with the env key; that message is
    split here so each one gets its own line. It is the only place that
    depends on that wording, and the guard's test in test_preflight.py fails
    if the two drift apart.
    """
    findings: list[Finding] = []
    for error in exc.errors():
        message = error["msg"]
        if message.startswith(_GUARD_PREFIX):
            for problem in message.removeprefix(_GUARD_PREFIX).split("; "):
                match = _KEY_FIRST.match(problem)
                key, text = match.groups() if match else ("(settings)", problem)
                findings.append((key, f"{text} — set it in .env"))
        elif error["loc"]:
            findings.append(
                (_env_key(error["loc"]), f"{message} — fix the value in .env")
            )
        else:
            findings.append(("(settings)", message))
    return findings


def lint_mcp_api_keys(raw: str) -> list[Finding]:
    """Warnings about an ``MCP__API_KEYS`` value that parses badly or oddly.

    Reuses the parser the MCP server itself runs, so this cannot disagree with
    what that service will accept. Its messages never hold key material.
    """
    if not raw.strip():
        return []
    syntax = f"label{FIELD_SEPARATOR}scope[+scope]{FIELD_SEPARATOR}key"
    try:
        keys = parse_api_keys(raw)
    except ValueError as exc:
        return [
            (
                "MCP__API_KEYS",
                f"{exc} — the mcp service will refuse to start; syntax is {syntax}",
            )
        ]
    return [
        (
            "MCP__API_KEYS",
            (
                f"{key.label!r} has write but not read, and write does not imply "
                f"read — that client cannot read anything; use "
                f"{key.label}:read+write:<key> (syntax {syntax})"
            ),
        )
        for key in keys
        if Scope.WRITE in key.scopes and Scope.READ not in key.scopes
    ]


def read_env_file(path: Path) -> dict[str, str]:
    """Live ``KEY=value`` lines of a `.env`, raw values, later lines winning."""
    live: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            live[match.group(1)] = match.group(2).strip()
    return live


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def _is_foreign(key: str) -> bool:
    return key in _FOREIGN_KEYS or key.startswith(_FOREIGN_PREFIXES)


def file_findings(path: Path) -> tuple[list[Finding], list[Finding]]:
    """Errors and warnings for a `.env` file: keys, values, then `Settings`."""
    live = read_env_file(path)
    errors: list[Finding] = []
    warnings: list[Finding] = []

    errors.extend(
        (key, "required but has no live line in the file — add it")
        for key in sorted(REQUIRED_ENV_KEYS - live.keys())
    )

    known = settings_env_keys()
    errors.extend(
        (key, "no setting reads this key any more — remove it (unknown)")
        for key in sorted(live.keys() - known)
        if not _is_foreign(key)
    )

    hash_raw = live.get("AUTH__PASSWORD_HASH", "")
    if "$" in hash_raw and not (hash_raw.startswith("'") and hash_raw.endswith("'")):
        warnings.append(
            (
                "AUTH__PASSWORD_HASH",
                (
                    "a bcrypt hash holds `$` and must be single-quoted, or shells "
                    "and dotenv parsers mangle it — `just hash-password` prints "
                    "the line already quoted"
                ),
            )
        )
    warnings.extend(lint_mcp_api_keys(_unquote(live.get("MCP__API_KEYS", ""))))

    try:
        Settings(_env_file=path)
    except ValidationError as exc:
        reported = {key for key, _ in errors}
        errors.extend(f for f in validation_findings(exc) if f[0] not in reported)
    return errors, warnings


def _print(findings: list[Finding], label: str) -> None:
    for key, message in findings:
        print(f"  {label} {key}: {message}")


def check_file(path: Path) -> int:
    """`just env-check`: the verdict on one file; 0 only when there is nothing to say."""
    if not path.is_file():
        print(f"no .env found at {path} — run `just init` to write one")
        return 1
    errors, warnings = file_findings(path)
    if not errors and not warnings:
        print("ok: .env matches the settings model")
        return 0
    print(f"{path} does not match the settings model:")
    _print(errors, "error  ")
    _print(warnings, "warning")
    print(f"Reference: .env.example; {_REMEDY}.")
    return 1


def boot_check() -> int:
    """The compose gate: silent on success, one line per problem on failure."""
    try:
        settings = Settings()
    except ValidationError as exc:
        print(
            "arc cannot start: the configuration in .env / the environment is invalid."
        )
        _print(validation_findings(exc), "error  ")
        print(f"Reference: .env.example; {_REMEDY}.")
        return 1
    warnings = lint_mcp_api_keys(settings.mcp.api_keys.get_secret_value())
    if warnings:
        print("arc is starting with configuration warnings:")
        _print(warnings, "warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: boot gate, or `--file` for the on-demand `.env` check."""
    parser = argparse.ArgumentParser(
        prog="python -m app.preflight", description=__doc__
    )
    parser.add_argument(
        "--file",
        nargs="?",
        const=DEFAULT_ENV_FILE,
        type=Path,
        help="check a .env file (default: the repo-root .env) instead of the environment",
    )
    args = parser.parse_args(argv)
    if args.file is not None:
        return check_file(args.file.absolute())
    return boot_check()


if __name__ == "__main__":
    sys.exit(main())
