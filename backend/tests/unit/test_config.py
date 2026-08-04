import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    AuthSettings,
    PostgresSettings,
    SessionSettings,
    Settings,
)

REAL_HASH = "$2b$12$" + "x" * 53


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
