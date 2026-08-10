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


class IngestSettings(BaseModel):
    """The watched folder and the two thresholds the pipeline reads."""

    scan_interval_seconds: int = 30
    """How often the scheduler sweeps `data/inbox/` (build-plan WP-4.3)."""

    settle_seconds: float = 2.0
    """How long a file must have sat unchanged before it is read.

    A file still being copied is a truncated file, and reading it would
    quarantine a perfectly good ride. The scan skips anything modified more
    recently than this *and* anything whose size changed since the last sweep.
    """

    overlap_threshold: float = 0.70
    """Fraction of an activity's time range that may overlap an existing
    session before it is quarantined as a suspected duplicate (B-2).

    Not 1.0: the same ride exported twice by two platforms differs at both
    ends by a few seconds, and two genuinely different sessions do not overlap
    by two thirds of their length.
    """


class MatchingSettings(BaseModel):
    """WP-6: the athlete's own timezone, and how often the missed sweep runs."""

    timezone: str = "UTC"
    """The athlete's local timezone, for the missed-session rule (WP-6.7).

    "No link by the end of day+1" is a statement about the athlete's own clock,
    and this is the only place the application can learn what that is: a
    *recording* carries the zone its device was in, but a planned session
    carries a bare date and a plan with no rides in it carries nothing at all.

    An IANA name (``Europe/Berlin``), a fixed offset (``UTC+02:00``) or ``UTC``
    — the three forms `app.domain.activity.parse_timezone` accepts. Prefer the
    IANA name: a fixed offset is wrong for half the year wherever there is
    daylight saving, and being an hour out on a day boundary is exactly what
    this setting exists to get right.
    """

    missed_scan_interval_seconds: int = 3600
    """How often the missed-session sweep runs.

    Hourly rather than daily: the sweep's job is to notice a day boundary
    passing in the athlete's zone, and a daily job would have to be scheduled
    *in* that zone to do it. An hourly idempotent sweep needs no such
    agreement — it marks whatever has run out of grace since the last one.
    """

    missed_scan_batch: int = 200
    """Most planned sessions one sweep will mark missed.

    A bound rather than pagination: the sweep is idempotent and the next run is
    an hour away, so a first run after a long absence catches up over a few
    passes instead of loading the whole plan history into one transaction.
    """


class ScoringSettings(BaseModel):
    """WP-7: how often the evening prompts are swept for expiry."""

    prompt_expiry_interval_seconds: int = 3600
    """How often the evening-prompt expiry sweep runs.

    Hourly, for the reason the missed sweep is hourly: the deadline is stored
    on each prompt (`expires_at`, 72 h after it was raised), so the job's only
    duty is to notice a deadline passing, and noticing within the hour is
    close enough for something the athlete has had three days to answer.
    """

    prompt_expiry_batch: int = 200
    """Most prompts one sweep will expire.

    A bound rather than pagination: the sweep is idempotent and the next run is
    an hour away, so a first run after a long absence catches up over a few
    passes instead of in one transaction.
    """


class ProposalSettings(BaseModel):
    """WP-8: how often standing plan-change proposals are swept for expiry."""

    expiry_interval_seconds: int = 3600
    """How often the proposal expiry sweep runs.

    Hourly, for the reason the other two sweeps are hourly: each proposal
    carries its own `expires_at`, so the job's only duty is to notice a
    deadline passing. Default-on-expiry is that the committed plan stands, so
    noticing late costs nothing but an inbox row that should have gone grey.
    """

    expiry_batch: int = 200
    """Most proposals one sweep will lapse.

    A bound rather than pagination: the sweep is idempotent and the next run
    is an hour away, so a first run after a long absence catches up over a few
    passes instead of in one transaction.
    """


class McpSettings(BaseModel):
    """MCP server and coaching-agent settings.

    `api_keys` is used only by the MCP server (`python -m app.mcp.main`); the
    API ignores it. See `app/mcp/auth.py` for the key format.

    `write_cap_per_hour` is **not** MCP-only: it is enforced in the service
    layer (`app.services.guardrails`), so it binds every path an agent actor
    can write through, including anything the API process runs on its behalf.
    It lives here because it is a property of the agent surface, which is what
    this section is about.
    """

    api_keys: SecretStr = SecretStr("")
    """Comma-separated `label:scope:key` entries; scope is `read` or `write`."""

    write_cap_per_hour: int = 60
    """Most writes an agent actor may make in a trailing hour (WP-8.3).

    A circuit breaker, not a quota: a coaching agent in a loop can rewrite a
    training plan faster than the athlete can read the inbox, and this is the
    bound on how much of that reaches the database before someone notices.
    Counted over `audit_log` rows whose actor starts `agent:`, so every agent
    key shares one budget and a dry run — which writes nothing — costs
    nothing. Athlete and system writes are not counted and never capped.
    """


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
    ingest: IngestSettings = IngestSettings()
    matching: MatchingSettings = MatchingSettings()
    scoring: ScoringSettings = ScoringSettings()
    proposals: ProposalSettings = ProposalSettings()
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
