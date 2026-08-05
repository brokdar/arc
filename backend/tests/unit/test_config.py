import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    REPO_ROOT,
    AuthSettings,
    McpSettings,
    PostgresSettings,
    SessionSettings,
    Settings,
)

REAL_HASH = "$2b$12$" + "x" * 53


def test_repo_root_anchor_points_at_the_repository() -> None:
    """`REPO_ROOT` is where `just init` writes `.env`, found by parent count.

    Miscount the parents and settings silently fall back to defaults for every
    host process (`just dev-api` would connect as `postgres`/`postgres`), so
    pin the arithmetic to two files that only ever live at the repo root.
    """
    assert (REPO_ROOT / ".env.example").is_file()
    assert (REPO_ROOT / "justfile").is_file()


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the auth env the global fixture injects.

    This module tests the settings model itself, so constructor arguments
    must be the only input — `_env_file=None` silences the .env file but not
    the process environment.
    """
    for key in ("AUTH__PASSWORD_HASH", "AUTH__SESSION__SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_production_rejects_default_credentials() -> None:
    with pytest.raises(ValidationError, match="Insecure production configuration"):
        Settings(environment="production", _env_file=None)


def test_production_lists_every_missing_secret() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment="production", _env_file=None)

    message = str(excinfo.value)
    assert "AUTH__PASSWORD_HASH is empty" in message
    assert "AUTH__SESSION__SECRET_KEY is empty" in message
    assert "POSTGRES__PASSWORD is empty or the dev default" in message


def test_production_rejects_a_missing_password_hash() -> None:
    with pytest.raises(ValidationError, match="AUTH__PASSWORD_HASH is empty"):
        Settings(
            environment="production",
            auth=AuthSettings(session=SessionSettings(secret_key=SecretStr("x" * 64))),
            postgres=PostgresSettings(password=SecretStr("a-real-password")),
            _env_file=None,
        )


def test_production_rejects_the_placeholder_password_hash() -> None:
    """`just init` without a TTY leaves the placeholder, which is non-empty.

    The stack would boot cleanly on a login that can never succeed, so the
    guard checks for the placeholder text as well as for emptiness.
    """
    with pytest.raises(
        ValidationError, match="AUTH__PASSWORD_HASH is still the placeholder"
    ):
        Settings(
            environment="production",
            auth=AuthSettings(
                password_hash=SecretStr("change-me-to-a-bcrypt-hash"),
                session=SessionSettings(secret_key=SecretStr("x" * 64)),
            ),
            postgres=PostgresSettings(password=SecretStr("a-real-password")),
            _env_file=None,
        )


def test_validation_errors_do_not_echo_the_input() -> None:
    """`hide_input_in_errors` keeps secrets out of the boot failure log.

    Pydantic otherwise renders `input_value=` — for a settings model that is
    the whole env-derived dict, and a truncated but real `MCP__API_KEYS` was
    observed in container logs when the production guard tripped.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            environment="production",
            auth=AuthSettings(session=SessionSettings(secret_key=SecretStr("x" * 64))),
            postgres=PostgresSettings(password=SecretStr("a-real-password")),
            mcp=McpSettings(api_keys=SecretStr("coach:write:" + "s" * 40)),
            _env_file=None,
        )

    message = str(excinfo.value)
    assert "input_value" not in message
    assert "coach:write" not in message
    assert "a-real-password" not in message


def test_production_rejects_a_missing_session_secret() -> None:
    with pytest.raises(ValidationError, match="AUTH__SESSION__SECRET_KEY is empty"):
        Settings(
            environment="production",
            auth=AuthSettings(password_hash=SecretStr(REAL_HASH)),
            postgres=PostgresSettings(password=SecretStr("a-real-password")),
            _env_file=None,
        )


def test_production_boots_with_real_secrets() -> None:
    settings = Settings(
        environment="production",
        auth=AuthSettings(
            password_hash=SecretStr(REAL_HASH),
            session=SessionSettings(secret_key=SecretStr("x" * 64)),
        ),
        postgres=PostgresSettings(password=SecretStr("a-real-password")),
        _env_file=None,
    )

    assert settings.environment == "production"


def test_development_allows_dev_defaults() -> None:
    settings = Settings(environment="development", _env_file=None)

    assert settings.postgres.password.get_secret_value() == "postgres"
    assert settings.auth.session.cookie_name == "arc_session"
    assert settings.auth.session.max_age_seconds == 1_209_600
    assert settings.auth.session.https_only is False
