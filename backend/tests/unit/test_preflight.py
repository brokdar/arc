"""`app.preflight`: boot failures name the .env problem, never a traceback."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import REPO_ROOT, REQUIRED_ENV_KEYS, Settings, settings_env_keys
from app.preflight import (
    check_file,
    file_findings,
    lint_mcp_api_keys,
    main,
    validation_findings,
)

HASH = "'$2b$12$" + "x" * 53 + "'"
KEY_A = "a" * 40
KEY_B = "b" * 40

GOOD = {
    "POSTGRES__PASSWORD": "pw-123",
    "AUTH__PASSWORD_HASH": HASH,
    "AUTH__SESSION__SECRET_KEY": "s" * 64,
    "SECRETS__ENCRYPTION_KEY": "k" * 43 + "=",
    "MCP__API_KEYS": f"'coach:read+write:{KEY_A},ro:read:{KEY_B}'",
    "ENVIRONMENT": "production",
}


def write_env(path: Path, values: dict[str, str], extra: str = "") -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()) + extra)
    return path


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process env is a Settings input too; start from none of it."""
    for key in settings_env_keys() | {"ENVIRONMENT"}:
        monkeypatch.delenv(key, raising=False)


def test_required_keys_are_real_settings_and_the_guard_demands_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert settings_env_keys() >= REQUIRED_ENV_KEYS
    # Each one the production guard enforces must trip it when absent, so the
    # constant cannot silently list a key the model stopped requiring.
    for key in REQUIRED_ENV_KEYS - {"MCP__API_KEYS"}:
        for name, value in GOOD.items():
            monkeypatch.setenv(name, "" if name == key else value.strip("'"))
        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)
        assert key in [k for k, _ in validation_findings(excinfo.value)], key


def test_env_example_has_no_unknown_keys_and_every_required_one() -> None:
    errors, _ = file_findings(REPO_ROOT / ".env.example")
    assert not [e for e in errors if "unknown" in e[1]]
    assert not [e for e in errors if "required but" in e[1]]


def test_valid_file_is_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = write_env(tmp_path / ".env", GOOD)
    assert check_file(env) == 0
    assert capsys.readouterr().out.strip() == "ok: .env matches the settings model"


def test_missing_required_key_is_named(tmp_path: Path) -> None:
    values = {k: v for k, v in GOOD.items() if k != "SECRETS__ENCRYPTION_KEY"}
    errors, _ = file_findings(write_env(tmp_path / ".env", values))
    assert [k for k, _ in errors] == ["SECRETS__ENCRYPTION_KEY"]


def test_commented_out_key_counts_as_missing(tmp_path: Path) -> None:
    values = {k: v for k, v in GOOD.items() if k != "SECRETS__ENCRYPTION_KEY"}
    env = write_env(tmp_path / ".env", values, "# SECRETS__ENCRYPTION_KEY=abc\n")
    errors, _ = file_findings(env)
    assert "SECRETS__ENCRYPTION_KEY" in [k for k, _ in errors]


def test_unknown_key_is_named_but_foreign_keys_are_not(tmp_path: Path) -> None:
    extra = "LOG__FILE_PATH=x\nCADDY_SITE_ADDRESS=a\nNEXT_PUBLIC_API_BASE_URL=\nCOMPOSE_PROJECT_NAME=x\nE2E_PASSWORD=x\nNEXT_PUBLIC_API_PATH=x\n"
    errors, _ = file_findings(write_env(tmp_path / ".env", GOOD, extra))
    unknown = {k for k, m in errors if "unknown" in m}
    # NEXT_PUBLIC_* is a family, so even the stale NEXT_PUBLIC_API_PATH from
    # an old run-through passes; only a key outside every family is flagged.
    assert unknown == {"LOG__FILE_PATH"}


def test_empty_file_lists_every_required_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("")
    errors, _ = file_findings(env)
    assert {k for k, _ in errors} >= REQUIRED_ENV_KEYS


def test_nonzero_exit_for_unknown_and_for_missing(tmp_path: Path) -> None:
    assert check_file(write_env(tmp_path / "a", GOOD, "LOG__FILE_PATH=x\n")) == 1
    values = {k: v for k, v in GOOD.items() if k != "POSTGRES__PASSWORD"}
    assert check_file(write_env(tmp_path / "b", values)) == 1


def test_no_env_file_says_run_just_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_file(tmp_path / ".env") == 1
    assert "just init" in capsys.readouterr().out


def test_write_without_read_warns_and_quotes_the_syntax(tmp_path: Path) -> None:
    values = {**GOOD, "MCP__API_KEYS": f"'coach:write:{KEY_A}'"}
    errors, warnings = file_findings(write_env(tmp_path / ".env", values))
    assert not errors
    [(key, message)] = warnings
    assert key == "MCP__API_KEYS"
    assert "coach" in message
    assert "label:scope[+scope]:key" in message
    assert KEY_A not in message
    assert check_file(tmp_path / ".env") == 1


def test_unknown_scope_is_unparseable_and_lists_valid_scopes() -> None:
    [(_, message)] = lint_mcp_api_keys(f"coach:admin:{KEY_A}")
    assert "admin" in message
    assert "read" in message
    assert "write" in message
    assert KEY_A not in message


def test_unquoted_hash_is_warned(tmp_path: Path) -> None:
    values = {**GOOD, "AUTH__PASSWORD_HASH": HASH.strip("'")}
    _, warnings = file_findings(write_env(tmp_path / ".env", values))
    assert any(k == "AUTH__PASSWORD_HASH" and "single-quoted" in m for k, m in warnings)


def test_values_are_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = {
        **GOOD,
        "MCP__API_KEYS": f"'coach:write:{KEY_A}'",
        "INGEST__SCAN_INTERVAL_SECONDS": "1",
    }
    check_file(write_env(tmp_path / ".env", values))
    out = capsys.readouterr().out
    for secret in (KEY_A, "s" * 64, "pw-123", "x" * 53):
        assert secret not in out
    assert "INGEST__SCAN_INTERVAL_SECONDS" in out


def test_boot_is_silent_on_a_valid_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in GOOD.items():
        monkeypatch.setenv(key, value.strip("'"))
    assert main([]) == 0
    assert capsys.readouterr().out == ""


def test_boot_dev_defaults_pass(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert capsys.readouterr().out == ""


def test_boot_failure_names_key_env_and_remedy_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in GOOD.items():
        if key != "SECRETS__ENCRYPTION_KEY":
            monkeypatch.setenv(key, value.strip("'"))
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "SECRETS__ENCRYPTION_KEY" in out
    assert ".env" in out
    assert ".env.example" in out
    assert "Traceback" not in out
    assert 'File "' not in out


def test_module_entrypoint_exits_one_without_traceback() -> None:
    """The real `python -m app.preflight`, as compose runs it."""
    # A real env var beats the repo-root .env the child process would read, so
    # blanking the key is what keeps this independent of the developer's file.
    env = {k: v for k, v in os.environ.items() if k not in settings_env_keys()}
    env.update(ENVIRONMENT="production", AUTH__PASSWORD_HASH="")
    backend = Path(__file__).parents[2]
    done = subprocess.run(
        [sys.executable, "-m", "app.preflight"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert "AUTH__PASSWORD_HASH" in done.stdout
    assert "Traceback" not in done.stdout + done.stderr


def test_compose_runs_preflight_ahead_of_alembic_and_the_mcp_server() -> None:
    """The gate is only a gate if nothing can start before it."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    [api] = re.findall(r'^\s+command: (bash -c ".*alembic.*")$', compose, re.MULTILINE)
    assert api.index("app.preflight") < api.index("alembic upgrade head")
    assert "app.preflight &&" in api
    [mcp] = re.findall(r"^\s+command: (.*app\.mcp\.main.*)$", compose, re.MULTILINE)
    assert mcp.index("app.preflight") < mcp.index("app.mcp.main")
    assert "app.preflight &&" in mcp


def test_bak_files_are_gitignored() -> None:
    lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "*.bak" in lines
