"""The local drop's configuration: what is fixed, what the athlete may change.

Two answers live here, and the split between them is the whole use-case.

**The inbox path is reported, never set.** `DATA__ROOT` roots `originals/`,
`streams/` and `quarantine/` as well as `inbox/`, and in Compose it is a
mounted volume; moving it from a settings form would strand every original arc
has already filed and leave the streams behind. So the path is a fact the panel
displays with the reason it is fixed, and there is no method here that changes
it.

**The sweep interval is the athlete's.** It was reachable only through
`INGEST__SCAN_INTERVAL_SECONDS` and a restart, which is not a setting so much
as a deployment detail. Here the environment **seeds** it and a stored row
**overrides** it, and the read says which of the two is in force — the pattern
the Dropbox app key established, for the same reason: the two are undone in
different ways, and a panel that could not tell them apart would offer the
wrong remedy.

Retiming the **running** sweep is not done here. That is knowledge of the
scheduler job, which belongs to `app.ingest.inbox`, and `app.services` may not
import `app.ingest`; `app.ingest.inbox.set_scan_interval` is the use-case the
API calls, and it wraps this one.
"""

from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SettingSource,
    get_settings,
)
from app.core.exceptions import ValidationError
from app.domain.actor import Actor
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.ingest_settings import IngestSettingsRepository
from app.services.guardrails import check_write_cap

#: `entity_type` written on this use-case's audit rows.
INGEST_SETTINGS_ENTITY = "ingest_settings"

#: The action written when the athlete changes the sweep.
INTERVAL_UPDATED_ACTION = "ingest_settings.updated"

#: The subdirectory of `DATA__ROOT` the sweep watches (`app.ingest.inbox`).
#:
#: Defined here rather than in `app.services.integrations`, which imports it:
#: one spelling, so the path Settings shows and the path the sweep reads cannot
#: drift apart.
INBOX_DIRECTORY = "inbox"


@dataclass(frozen=True, slots=True)
class LocalDropSettings:
    """Where the local drop looks, how often, and who decided how often."""

    #: Resolved and absolute — a relative `DATA__ROOT` tells the athlete
    #: nothing about where on the server (or in the container) to drop a file.
    inbox_path: str
    scan_interval_seconds: int
    #: Which of the two sources the interval above came from.
    source: SettingSource
    #: The bounds the athlete's value must satisfy, reported rather than
    #: duplicated in the panel: the form states what the server will accept
    #: because the server is what accepts it.
    minimum_seconds: int = MIN_SCAN_INTERVAL_SECONDS
    maximum_seconds: int = MAX_SCAN_INTERVAL_SECONDS


class IngestSettingsService:
    """Reading and changing the local drop's configuration. Raises AppError."""

    def __init__(
        self,
        session: AsyncSession,
        repository: IngestSettingsRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, IngestSettingsRepository(session), AuditRepository(session))

    async def read(self) -> LocalDropSettings:
        """The whole configuration, and which source each part came from."""
        stored = await self._repository.get()
        settings = get_settings()
        return LocalDropSettings(
            inbox_path=str((settings.data.root / INBOX_DIRECTORY).resolve()),
            scan_interval_seconds=(
                settings.ingest.scan_interval_seconds
                if stored is None
                else stored.scan_interval_seconds
            ),
            source=(
                SettingSource.ENVIRONMENT if stored is None else SettingSource.STORED
            ),
        )

    async def set_scan_interval(
        self, seconds: int, *, actor: Actor
    ) -> LocalDropSettings:
        """Store how often the local drop is swept.

        The bounds are enforced **here** rather than as `ge`/`le` on the
        request body, and the difference is visible in the contract: the API
        publishes a plain integer, so a schema-valid `0` reaches this method
        and is answered with a sentence naming both limits instead of
        pydantic's `greater than or equal to 5`. The fuzzer is told about that
        refusal in `backend/schemathesis.toml`; the limits themselves are
        reported by :meth:`read`, so the form states them without knowing them.

        Raises:
            ValidationError: When the interval is outside the documented range.
        """
        await check_write_cap(self._session, actor)
        if not MIN_SCAN_INTERVAL_SECONDS <= seconds <= MAX_SCAN_INTERVAL_SECONDS:
            raise ValidationError(
                f"arc sweeps the drop folder every "
                f"{MIN_SCAN_INTERVAL_SECONDS}–{MAX_SCAN_INTERVAL_SECONDS} "
                f"seconds; {seconds} is outside that. Below "
                f"{MIN_SCAN_INTERVAL_SECONDS} seconds the sweep runs faster "
                "than a file can prove it has finished copying, and above "
                f"{MAX_SCAN_INTERVAL_SECONDS} the folder is not watched in any "
                "useful sense."
            )
        row = await self._repository.set_scan_interval(seconds)
        await self._audit.record(
            actor=actor,
            action=INTERVAL_UPDATED_ACTION,
            entity_type=INGEST_SETTINGS_ENTITY,
            entity_id=row.id,
            payload={"scope": row.scope, "scan_interval_seconds": seconds},
        )
        await commit(self._session)
        return await self.read()

    async def stored_scan_interval(self) -> int | None:
        """The stored interval, or None when the environment is still in force.

        Read by the sweep itself to reconcile the running job after a restart
        (`app.ingest.inbox.apply_stored_scan_interval`), which is why it is a
        bare integer rather than the whole view: nothing there needs the path.
        """
        stored = await self._repository.get()
        return None if stored is None else stored.scan_interval_seconds
