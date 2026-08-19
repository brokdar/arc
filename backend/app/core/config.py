"""Application configuration.

Settings are loaded from environment variables (and a `.env` file in
development). Nested models map to double-underscore env keys:
``POSTGRES__HOST`` -> ``settings.postgres.host``. Keep `.env.example` at the
repository root in sync — `tests/unit/test_env_example_completeness.py`
enforces it.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
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


class SettingSource(StrEnum):
    """Which of the two places a setting arc is running on was fixed in.

    Reported rather than inferred, because the two are undone in different
    ways and a panel that said only "it is 120 seconds" would leave the
    athlete guessing which. ``stored`` was set in the app and takes effect at
    once; ``environment`` came from `.env` and needs a file edit and a restart
    to change. Every setting that follows this pattern — env seeds it, a
    stored row overrides it — reports one of these.
    """

    STORED = "stored"
    ENVIRONMENT = "environment"


#: The narrowest sweep the local drop will accept, in seconds.
#:
#: Below this the sweep runs faster than a file can prove it has settled
#: (`INGEST__SETTLE_SECONDS`, and a file is never taken on its first sighting),
#: so it buys no earlier ingest and costs a directory listing per second for
#: the life of the process. Enforced on the environment value here **and** on
#: the value the athlete may set in Settings, by
#: `app.services.ingest_settings.IngestSettingsService` — one rule, stated
#: once, wherever it is fixed from.
MIN_SCAN_INTERVAL_SECONDS = 5

#: The widest sweep the local drop will accept: one day.
#:
#: Past this the folder is not watched in any sense the word supports — a ride
#: dropped in on Monday would surface on Tuesday — and the honest way to stop
#: sweeping is to stop dropping files in, not to set the timer to a month.
MAX_SCAN_INTERVAL_SECONDS = 86_400


class IngestSettings(BaseModel):
    """The watched folder and the two thresholds the pipeline reads."""

    scan_interval_seconds: int = Field(
        30, ge=MIN_SCAN_INTERVAL_SECONDS, le=MAX_SCAN_INTERVAL_SECONDS
    )
    """How often the scheduler sweeps `data/inbox/` (build-plan WP-4.3).

    The **seed**, not the last word: the athlete sets the sweep interval in
    Settings, and a stored value overrides this one on the running scheduler
    without a restart (`app.ingest.inbox.set_scan_interval`). Bounded by
    :data:`MIN_SCAN_INTERVAL_SECONDS` and :data:`MAX_SCAN_INTERVAL_SECONDS` so
    a nonsense value in `.env` stops the boot rather than surfacing as a sweep
    that never runs.
    """

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


class WellnessSettings(BaseModel):
    """Increment 1: when the day's wellness prompt is raised, and when it closes.

    Beside `SCORING__*`, because this is the same machinery: a dated row with a
    stored deadline and an idempotent sweep. What is deliberately **not** here
    is the maturity thresholds, `WELLNESS_LATE_ENTRY_DAYS` and
    `MAX_BACKFILL_DAYS` — those are domain constants
    (`app.domain.wellness`, `app.domain.wellness_baseline`). They are statements
    about physiology and recall that must be identical in every deployment: a
    per-instance override would make an abstention message unreproducible and
    the late-entry flag mean different things in two places.
    """

    prompt_hour_local: int = 19
    """The hour, on the athlete's own clock, the day's prompt is raised at.

    Evening rather than morning: the prompt asks about the day that is ending,
    and the sweep that raises it also has to be able to close yesterday's. The
    clock is `MATCHING__TIMEZONE` — there is one athlete and therefore one local
    clock, and a second source of "what day is it" is how the plan and the
    wellness series come to disagree about Tuesday.
    """

    prompt_expiry_hours: int = 36
    """How long the athlete has to answer before the day closes unanswered.

    Long enough to cover the morning after — the readings this exists to
    capture are entered the next day as often as not — and short enough that a
    prompt still standing is a question about a day the athlete can remember.
    Stored on each prompt when it is raised, so changing this never re-dates a
    prompt that is already standing.
    """

    prompt_scan_interval_seconds: int = 3600
    """How often the raise-and-expire sweep runs.

    Hourly, for the reason the other three sweeps are hourly: the job's only
    duty is to notice an hour boundary and a deadline passing, and noticing
    within the hour is close enough for a question the athlete has a day and a
    half to answer.
    """

    prompt_scan_batch: int = 200
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

    max_horizon_days: int = 90
    """Furthest ahead a proposal's `expires_at` may be set.

    Without a bound an agent can date a proposal past any sweep, and the
    pending set — scanned on every propose and every recorded session — grows
    without ever draining. Ninety days is well past the horizon a training
    suggestion is meaningful over and far short of "never".
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
    """Comma-separated `label:scope[+scope]:key` entries; scopes `read`, `write`."""

    write_cap_per_hour: int = 60
    """Most writes an agent actor may make in a trailing hour (WP-8.3).

    A circuit breaker, not a quota: a coaching agent in a loop can rewrite a
    training plan faster than the athlete can read the inbox, and this is the
    bound on how much of that reaches the database before someone notices.
    Counted over `audit_log` rows whose actor starts `agent:`, so every agent
    key shares one budget and a dry run — which writes nothing — costs
    nothing. Athlete and system writes are not counted and never capped.
    """


class DropboxSettings(BaseModel):
    """The one thing the operator has to paste in to connect Dropbox.

    There is no app *secret*, and that absence is the design: the connect flow
    is PKCE (RFC 7636) with a code the athlete pastes back, so arc is a public
    OAuth client. A secret would buy nothing a self-hosted box can keep — and
    the redirect URI a confidential client needs is exactly what arc cannot
    register, because it is reached at whatever host the home network gives it.

    `SecretStr` even so: the app key is public by design, but it is still a
    credential-shaped value, and the wrapper is what keeps it out of the
    `input_value=` of a settings ValidationError alongside everything else.
    """

    app_key: SecretStr = SecretStr("")
    """The Dropbox app key from https://www.dropbox.com/developers/apps.

    Register the app as **Full Dropbox**, not "App folder": Wahoo's and
    HealthFit's own app folders are where the FIT files already are, and an
    app-folder app is structurally unable to see them.
    """

    poll_interval_seconds: int = 120
    """How often each enabled feed asks Dropbox what has changed.

    Two minutes, against a rate limit measured in thousands of calls a day:
    one cursor call per feed is ~720 requests/day, which is noise. Shortening
    it buys nothing the athlete can feel — the ELEMNT→phone→Dropbox leg costs
    minutes on its own, and arc does not control it.
    """

    max_batch_attempts: int = 5
    """Consecutive failures on one listing cursor before the batch is skipped.

    Five, at two-minute intervals, is ten minutes of trying before arc gives
    up on a page and writes down which entry it gave up on. See
    `app.ingest.feeds` for why giving up at all is the lesser evil.
    """


class SecretsSettings(BaseModel):
    """The key under which arc encrypts third-party credentials at rest.

    Distinct from `AUTH__SESSION__SECRET_KEY`, which signs a cookie arc issues
    to itself: this one protects a live key to *someone else's* file store, so
    rotating it does not log anybody out — it makes every stored credential
    unreadable, and the connection says so rather than silently failing to
    refresh.
    """

    encryption_key: SecretStr = SecretStr("")
    """A Fernet key: 32 random bytes, urlsafe-base64 encoded.

    Generate one with
    ``python -c "from cryptography.fernet import Fernet;
    print(Fernet.generate_key().decode())"``.
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
    wellness: WellnessSettings = WellnessSettings()
    proposals: ProposalSettings = ProposalSettings()
    dropbox: DropboxSettings = DropboxSettings()
    secrets: SecretsSettings = SecretsSettings()
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
        # Unlike the three above, this one does not gate the login: an instance
        # with no Dropbox connection never reads it. It is here anyway because
        # the failure it prevents is the worst-timed kind — the athlete would
        # complete the whole connect ritual, arc would store a credential it
        # cannot open, and nothing would say so until the first token refresh.
        if not self.secrets.encryption_key.get_secret_value():
            problems.append("SECRETS__ENCRYPTION_KEY is empty")
        if problems:
            raise ValueError(
                f"Insecure production configuration: {'; '.join(problems)}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
