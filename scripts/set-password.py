#!/usr/bin/env python3
"""Rewrite AUTH__PASSWORD_HASH in .env for a password typed at a prompt.

Run via `just set-password` (`uv run`, so `bcrypt` comes from the backend's
lock). Replaces the print-it-and-paste-it ritual of `just hash-password`,
whose one foot-gun was the manual step: a bcrypt hash is full of `$`, an
unquoted paste is expanded by the shell and .env parsers, and the operator is
locked out. Here the writer owns the quoting, so there is no step to get wrong.

Choices that bind, each pinned by scripts/set-password.test.sh:

* Temp file in the same directory, then `os.replace`. A crash mid-write leaves
  the old `.env` whole; `sed -i` would have truncated it.
* One fixed backup name, `.env.set-password.bak`, mode 600. Timestamped
  backups would pile up copies of a secrets file. It is gitignored; the script
  warns if git would see it anyway.
* Only live `AUTH__PASSWORD_HASH=` lines are touched. A commented example
  stays, every other byte of the file stays. Several live lines (a past bad
  paste) collapse to one, and the output says so.
* A missing `.env` is an error naming `just init`; this never creates a bare
  one, because a bare `.env` lacks the secrets `init` generates.
* Prompts go to stderr and the password is read with echo off when stdin is a
  terminal. It never travels through argv or the environment, and neither it
  nor the hash is printed.
* The hash is `bcrypt.hashpw(pw, bcrypt.gensalt())`, the same call as
  `hash-password` and `bootstrap-env.sh`.
"""

import getpass
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import bcrypt

KEY = "AUTH__PASSWORD_HASH"
BACKUP_NAME = ".env.set-password.bak"
# bcrypt silently ignores everything past 72 bytes; refuse rather than let the
# operator believe a longer password is in force.
BCRYPT_MAX_BYTES = 72


def read_secret(prompt: str) -> str:
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    # No terminal (tests, a pipe): read stdin, not /dev/tty as getpass would.
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().rstrip("\n")


def rewrite(text: str, new_line: str) -> tuple[str, int]:
    """Return `text` with its live hash lines collapsed to `new_line`.

    Everything else is returned untouched. The count is how many live lines
    there were; 0 means the line was appended.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    found = 0
    for line in lines:
        if line.startswith(f"{KEY}="):
            found += 1
            if found > 1:
                continue
            ending = line[len(line.rstrip("\r\n")) :] or "\n"
            out.append(f"{new_line}{ending}")
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{new_line}\n")
    return "".join(out), found


def git_would_see(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=path.parent,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    # 1 = a repository, and the path is not ignored. 128 = not a repository.
    return result.returncode == 1


def main(argv: list[str]) -> int:
    env_file = (
        Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / ".env"
    ).absolute()
    if not env_file.is_file():
        print(
            f"ERROR: no .env at {env_file}. Run `just init` first; "
            "set-password never creates a bare one.",
            file=sys.stderr,
        )
        return 1

    try:
        first = read_secret("New password: ")
        if not first:
            print("ERROR: the password must not be empty, nothing changed.", file=sys.stderr)
            return 1
        if len(first.encode()) > BCRYPT_MAX_BYTES:
            print(
                f"ERROR: bcrypt ignores everything past {BCRYPT_MAX_BYTES} bytes. "
                "Pick a shorter password, nothing changed.",
                file=sys.stderr,
            )
            return 1
        second = read_secret("Confirm password: ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted, nothing changed.", file=sys.stderr)
        return 130
    if first != second:
        print("ERROR: entries did not match, nothing changed.", file=sys.stderr)
        return 1

    password_hash = bcrypt.hashpw(first.encode(), bcrypt.gensalt()).decode()
    new_line = f"{KEY}='{password_hash}'"
    backup = env_file.with_name(BACKUP_NAME)

    try:
        original = env_file.read_bytes()
        updated, found = rewrite(original.decode("utf-8"), new_line)

        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
        os.chmod(backup, 0o600)

        fd, tmp_name = tempfile.mkstemp(dir=env_file.parent, prefix=".env.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
            os.chmod(tmp_name, stat.S_IMODE(env_file.stat().st_mode))
            os.replace(tmp_name, env_file)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except OSError as err:
        print(f"ERROR: {err} ({err.filename or env_file}). Nothing was changed.", file=sys.stderr)
        return 1

    print(f"Updated {KEY} in {env_file} (previous file at {backup}).")
    if found > 1:
        print(
            f"Note: {env_file.name} had {found} live {KEY} lines "
            "(a past bad paste); they are now one."
        )
    elif found == 0:
        print(f"Note: {env_file.name} had no {KEY} line; one was appended.")
    if git_would_see(backup):
        print(
            f"WARNING: git does not ignore {backup}. It holds your previous hash; "
            "do not commit it.",
            file=sys.stderr,
        )
    print("Restart the api to apply: docker compose restart api")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
