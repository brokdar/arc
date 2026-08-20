"""What the watched folder did with each file: quarantine records and the log.

Two tables that together answer "why is this ride not in my sessions?" — the
only question the inbox page exists to answer.

`quarantine_records` is the athlete's queue: one row per file (or per sport
within a file) the pipeline refused, holding the machine-readable reason, the
path the file was moved to, and — for the duplicate case — the session it
looks like a copy of. It is a **queue**, so it has a status the athlete
resolves; the original of an already-ingested twin is never touched by either
outcome.

`ingest_events` is the append-only log: one row per file the pipeline looked
at, including the ones it ingested and the ones it had already seen. Nothing
updates it and nothing deletes from it, for the same reason the audit log
offers no update — a trail that can be edited is not one.

The two are separate because they have different lifetimes and different
readers: a quarantine record is resolved and stops mattering, while the log
is how a disappeared file is traced a month later.
"""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import ForeignKey, Integer, String, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.activity import IngestOutcome, QuarantineReason, QuarantineStatus
from app.persistence.activity import (
    FILE_HASH_LENGTH,
    MAX_PATH_LENGTH,
)
from app.persistence.db import Base, flush, refresh
from app.persistence.types import UtcDateTime, enum_column

#: Longest original filename kept. Filenames come from the athlete's own
#: devices and exports, so they are bounded but not short.
MAX_FILENAME_LENGTH = 512

#: Longest human-readable detail string on a quarantine or an ingest event.
MAX_DETAIL_LENGTH = 1_000


class QuarantineRecordRow(Base):
    """One file the pipeline refused, and what the athlete decided about it."""

    __tablename__ = "quarantine_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    original_filename: Mapped[str] = mapped_column(String(MAX_FILENAME_LENGTH))
    #: sha256 of the refused file, hex. Indexed because the pipeline checks
    #: pending quarantine as well as ingested recordings before it re-parses a
    #: file it has already rejected.
    file_hash: Mapped[str] = mapped_column(String(FILE_HASH_LENGTH), index=True)
    #: Which sport within the file this record is about, when the file parsed
    #: far enough to tell (A4.5). Null when it did not.
    file_sport_index: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[QuarantineReason] = mapped_column(enum_column(QuarantineReason))
    detail: Mapped[str | None] = mapped_column(String(MAX_DETAIL_LENGTH))
    #: Where the file was moved to under ``data/quarantine/``. The file is kept
    #: until the athlete resolves the record: quarantine is the catch-all that
    #: makes "a failure mid-pipeline must not lose the file" true.
    quarantined_path: Mapped[str] = mapped_column(String(MAX_PATH_LENGTH))
    status: Mapped[QuarantineStatus] = mapped_column(
        enum_column(QuarantineStatus),
        default=QuarantineStatus.PENDING,
        index=True,
    )
    #: The session this file looks like a duplicate of. Nulled rather than
    #: cascaded if that session is deleted — the record still explains itself.
    suspected_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class IngestEventRow(Base):
    """One file the pipeline looked at. Append-only."""

    __tablename__ = "ingest_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    filename: Mapped[str] = mapped_column(String(MAX_FILENAME_LENGTH))
    #: Null only when the file could not be read at all, which is the one
    #: outcome that can precede hashing.
    file_hash: Mapped[str | None] = mapped_column(String(FILE_HASH_LENGTH), index=True)
    outcome: Mapped[IngestOutcome] = mapped_column(
        enum_column(IngestOutcome), index=True
    )
    detail: Mapped[str | None] = mapped_column(String(MAX_DETAIL_LENGTH))
    #: The session that was created, for an `ingested` outcome. Nulled if the
    #: session is later deleted; the event stays, because the log has to
    #: survive what it describes.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )


class QuarantineRepository:
    """SQLAlchemy repository for quarantine records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, record_id: uuid.UUID) -> QuarantineRecordRow | None:
        """Return one quarantine record, or None."""
        return await self._session.get(QuarantineRecordRow, record_id)

    async def pending_for_hash(self, file_hash: str) -> QuarantineRecordRow | None:
        """Return an unresolved record for this file, if one exists.

        The pipeline checks this alongside the recordings' dedup key: a file
        the athlete has not yet ruled on must not be quarantined again on the
        next scan.
        """
        result = await self._session.execute(
            select(QuarantineRecordRow).where(
                QuarantineRecordRow.file_hash == file_hash,
                QuarantineRecordRow.status == QuarantineStatus.PENDING,
            )
        )
        return result.scalars().first()

    async def list(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[QuarantineRecordRow], int]:
        """Return a page of records, pending first then newest, plus the total.

        Pending first because the page is a queue: what is still waiting on
        the athlete outranks what they have already dealt with, whenever it
        arrived.
        """
        total = await self._session.scalar(
            select(func.count()).select_from(QuarantineRecordRow)
        )
        pending_first = case(
            (QuarantineRecordRow.status == QuarantineStatus.PENDING, 0), else_=1
        )
        result = await self._session.execute(
            select(QuarantineRecordRow)
            .order_by(pending_first, QuarantineRecordRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def add(self, row: QuarantineRecordRow) -> QuarantineRecordRow:
        """Persist a quarantine record (new or resolved) and refresh it.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row


class IngestEventRepository:
    """SQLAlchemy repository for the ingest log — append and read only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        filename: str,
        file_hash: str | None,
        outcome: IngestOutcome,
        detail: str | None = None,
        session_id: uuid.UUID | None = None,
    ) -> IngestEventRow:
        """Append one event.

        Flushed, not committed: the event joins the transaction of the ingest
        it describes, so a rolled-back ingest cannot leave a log line claiming
        it happened.
        """
        row = IngestEventRow(
            filename=filename,
            file_hash=file_hash,
            outcome=outcome,
            detail=detail,
            session_id=session_id,
        )
        self._session.add(row)
        await flush(self._session)
        return row

    async def list(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[IngestEventRow], int]:
        """Return a page of events, newest first, plus the total count."""
        total = await self._session.scalar(
            select(func.count()).select_from(IngestEventRow)
        )
        result = await self._session.execute(
            select(IngestEventRow)
            .order_by(IngestEventRow.at.desc(), IngestEventRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0
