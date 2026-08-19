"""Ingest configuration the athlete changed in the app, overriding the file.

One table, `ingest_settings`, holding the values that used to be reachable only
through `.env` and a restart. The pattern is the one the Dropbox app key
established: **the environment seeds it and a stored row overrides it**, so an
instance that has never opened Settings behaves exactly as it did before, and
an athlete who changes the sweep interval does not have to find a shell.

**A table rather than a column somewhere.** The one thing configured here
belongs to the local drop, and the local drop has no row — it is synthesized by
`IntegrationService.list` precisely so nobody can delete the sweep that has run
since WP-4.3 (see `app.persistence.integrations`). There is no row to hang a
column off, and inventing one would re-create the thing that design refuses.

**A `scope` discriminator rather than a hardcoded primary key.** The next value
that wants to move out of `.env` — a cloud feed's poll cadence, say — is a
second row here, not a second table and not a widening of this one. The unique
constraint on `scope` is what makes "at most one row per surface" a fact the
database holds rather than a convention the service remembers.
"""

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import Integer, String, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db import Base, flush
from app.persistence.types import UtcDateTime

#: Longest `scope` accepted. Room for a name, not for a sentence.
MAX_SCOPE_LENGTH = 40

#: The scope the `data/inbox/` sweep's configuration is stored under.
INBOX_SCOPE: Final = "inbox"


class IngestSettingsRow(Base):
    """The app-set ingest configuration for one ingest surface.

    At most one row per :attr:`scope`, held by the database. Absent means
    "nothing has been set here", which is not the same as "set to the default":
    the read reports which of the two sources is in force, and a row written
    with today's environment value would turn a seed the operator can still
    change in `.env` into a frozen copy of it.
    """

    __tablename__ = "ingest_settings"
    __table_args__ = (UniqueConstraint("scope", name="uq_ingest_settings_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    scope: Mapped[str] = mapped_column(String(MAX_SCOPE_LENGTH))
    #: How often this surface is swept. Bounded by the service, not by the
    #: column: `MIN_SCAN_INTERVAL_SECONDS` is a rule about what is useful, and
    #: a CHECK constraint carrying it would need a migration to move.
    scan_interval_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class IngestSettingsRepository:
    """SQLAlchemy repository for the app-set ingest configuration."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, scope: str = INBOX_SCOPE) -> IngestSettingsRow | None:
        """The stored configuration for a scope, or None when there is none."""
        result = await self._session.execute(
            select(IngestSettingsRow).where(IngestSettingsRow.scope == scope)
        )
        return result.scalars().first()

    async def set_scan_interval(
        self, seconds: int, *, scope: str = INBOX_SCOPE
    ) -> IngestSettingsRow:
        """Store a sweep interval, replacing any earlier one for this scope.

        Update-or-insert on the existing row rather than delete-and-add, so
        `created_at` keeps saying when the athlete first took this setting out
        of the environment's hands while `updated_at` moves — the shape
        `ConnectionRepository.replace_authorization` uses for a value that
        supersedes rather than accumulates.
        """
        row = await self.get(scope)
        if row is None:
            row = IngestSettingsRow(scope=scope, scan_interval_seconds=seconds)
            self._session.add(row)
        else:
            row.scan_interval_seconds = seconds
        await flush(self._session)
        return row
