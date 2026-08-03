"""Application configuration.

Settings are loaded from environment variables (and a `.env` file in
development). Nested models map to double-underscore env keys:
``POSTGRES__HOST`` -> ``settings.postgres.host``. Keep `.env.example` at the
repository root in sync — `tests/unit/test_env_example_completeness.py`
enforces it.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PostgresSettings(BaseModel):
    """PostgreSQL connection settings."""

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: SecretStr = SecretStr("postgres")
    db: str = "app"

    @property
    def async_url(self) -> str:
        """SQLAlchemy async connection URL."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )


class RedisSettings(BaseModel):
    """Redis connection settings."""

    url: str = "redis://localhost:6379/0"


class JwtSettings(BaseModel):
    """JWT signing and lifetime settings."""

    secret_key: SecretStr = SecretStr("")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7


class AuthSettings(BaseModel):
    """Authentication settings."""

    jwt: JwtSettings = JwtSettings()


class LogSettings(BaseModel):
    """Logging settings."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file_path: str | None = None


class Settings(BaseSettings):
    """Root application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    application_name: str = "arc"
    environment: Literal["development", "test", "production"] = "development"
    api_path: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    postgres: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()
    auth: AuthSettings = AuthSettings()
    log: LogSettings = LogSettings()

    @model_validator(mode="after")
    def _no_insecure_defaults_in_production(self) -> Self:
        """Refuse to boot in production with dev-convenience credentials."""
        if self.environment != "production":
            return self
        problems = []
        if not self.auth.jwt.secret_key.get_secret_value():
            problems.append("AUTH__JWT__SECRET_KEY is empty")
        if self.postgres.password.get_secret_value() in ("", "postgres"):
            problems.append("POSTGRES__PASSWORD is empty or the dev default")
        if problems:
            raise ValueError(
                f"Insecure production configuration: {'; '.join(problems)}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
