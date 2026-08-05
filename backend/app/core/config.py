"""Application configuration.

Settings are loaded from environment variables (and a `.env` file in
development). Nested models map to double-underscore env keys:
``POSTGRES__HOST`` -> ``settings.postgres.host``. Keep `.env.example` at the
repository root in sync — `tests/unit/test_env_example_completeness.py`
enforces it.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).parents[3]
"""Repository root: `<root>/backend/app/core/config.py` is three levels down.

`just init` writes one `.env` and it lives here, but every host process starts
in `backend/` (`just dev-api`, `uv run fastapi dev`, alembic), so a relative
`.env` alone would silently miss it. Guarded by
`tests/unit/test_config.py::test_repo_root_anchor_points_at_the_repository`.
In the Docker image this resolves to `/` (WORKDIR is `/app`) and no `.env` is
shipped — a missing env file is simply skipped, so containers are unaffected.
"""


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


class SessionSettings(BaseModel):
    """Signed session-cookie settings.

    The cookie is issued by Starlette's ``SessionMiddleware``, signed (not
    encrypted) with ``secret_key``. Rotating the key invalidates every
    outstanding session.
    """

    secret_key: SecretStr = SecretStr("")
    cookie_name: str = "arc_session"
    max_age_seconds: int = 1_209_600  # 14 days
    https_only: bool = False  # set true when Caddy serves TLS


class AuthSettings(BaseModel):
    """Authentication settings for the single-user login."""

    password_hash: SecretStr = SecretStr("")
    """bcrypt hash of the single user's password (`just hash-password`)."""

    session: SessionSettings = SessionSettings()


class DataSettings(BaseModel):
    """Runtime data settings."""

    root: Path = Path("data")
    """Root of the runtime data tree — inbox/, originals/, streams/, quarantine/."""


class McpSettings(BaseModel):
    """MCP server settings.

    Used only by the MCP server (`python -m app.mcp.main`); the API ignores
    them. See `app/mcp/auth.py` for the key format.
    """

    api_keys: SecretStr = SecretStr("")
    """Comma-separated `label:scope:key` entries; scope is `read` or `write`."""


class LogSettings(BaseModel):
    """Logging settings."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class Settings(BaseSettings):
    """Root application settings."""

    # Two env files, lowest precedence first: the repo-root `.env` that
    # `just init` writes, then a `.env` in whatever directory the process was
    # started from (later entries win). Real environment variables still beat
    # both, which is how the `dev-*`/`db-*` recipes override POSTGRES__HOST.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_nested_delimiter="__",
        extra="ignore",
        # A ValidationError normally renders `input_value=` — here the whole
        # env-derived settings dict, secrets and all (a truncated but real
        # MCP__API_KEYS was observed in container logs when the production
        # guard below tripped). The guard's own message names only env var
        # names, so nothing is lost by suppressing the input.
        hide_input_in_errors=True,
    )

    application_name: str = "arc"
    environment: Literal["development", "test", "production"] = "development"
    api_path: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    postgres: PostgresSettings = PostgresSettings()
    auth: AuthSettings = AuthSettings()
    data: DataSettings = DataSettings()
    log: LogSettings = LogSettings()
    mcp: McpSettings = McpSettings()

    @model_validator(mode="after")
    def _no_insecure_defaults_in_production(self) -> Self:
        """Refuse to boot in production with dev-convenience credentials.

        `mcp.api_keys` is deliberately not checked here: the MCP server is an
        optional, separately deployed service, and the API must boot without
        it. The MCP server does its own check and refuses to start with no
        keys (see `app/mcp/main.py`).
        """
        if self.environment != "production":
            return self
        problems = []
        password_hash = self.auth.password_hash.get_secret_value()
        if not password_hash:
            problems.append("AUTH__PASSWORD_HASH is empty")
        elif "change-me" in password_hash:
            problems.append("AUTH__PASSWORD_HASH is still the placeholder")
        if not self.auth.session.secret_key.get_secret_value():
            problems.append("AUTH__SESSION__SECRET_KEY is empty")
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
