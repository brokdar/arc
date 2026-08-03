import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import AuthSettings, JwtSettings, PostgresSettings, Settings


def test_production_rejects_default_credentials() -> None:
    with pytest.raises(ValidationError, match="Insecure production configuration"):
        Settings(environment="production", _env_file=None)


def test_production_boots_with_real_secrets() -> None:
    settings = Settings(
        environment="production",
        auth=AuthSettings(jwt=JwtSettings(secret_key=SecretStr("x" * 64))),
        postgres=PostgresSettings(password=SecretStr("a-real-password")),
        _env_file=None,
    )

    assert settings.environment == "production"


def test_development_allows_dev_defaults() -> None:
    settings = Settings(environment="development", _env_file=None)

    assert settings.postgres.password.get_secret_value() == "postgres"
