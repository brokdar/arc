"""Planned sessions and their append-only intent versions.

Two tables, because a planned session and its intent have different
lifetimes. The session is an identity on the calendar — a date, a discipline,
a status — and it is mutable in the ordinary way. The **intent** is what the
session is *for*, and invariant 4 makes it immutable: an edit writes a new
version and leaves the old one readable, because a score computed against the
old one has to stay explicable.

`PlannedSessionIntentRow` therefore carries WP-1's versioning vocabulary
verbatim — ``version``, ``as_of``, ``superseded_by``, ``recompute_reason`` —
and satisfies `app.domain.versioning.VersionRecord` structurally, so the
domain's `current_version` and `next_version` helpers work on it without the
domain knowing SQLAlchemy exists. ``artefact_id`` is the planned session's id:
all the versions of one session's intent are versions of one artefact.

The prescription is **snapshotted** into every intent version
(``structure``), even when it came from the library. ``workout_id`` records
where it came from and is nulled if that library entry is later deleted; the
snapshot is what makes the frozen prescription survive an edit to the library.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.athlete import Discipline
from app.domain.purpose import Purpose
from app.domain.sessions import MAX_INTENT_CHARS, SessionStatus
from app.domain.versioning import current_version
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest a recompute reason may be.
MAX_REASON_LENGTH = 200


class PlannedSessionIntentRow(Base):
    """One immutable version of a planned session's intent."""

    __tablename__ = "planned_session_intents"
    __table_args__ = (
        # One row per (session, version). The version chain is what the freeze
        # rule is enforced through, so a duplicate version number is a
        # corruption of it, not a cosmetic problem.
        UniqueConstraint("planned_session_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the unique constraint above leads on this column,
    #: so "the versions of one session" already has one.
    planned_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="CASCADE")
    )
    #: 1-based, strictly increasing within a planned session.
    version: Mapped[int] = mapped_column(Integer)
    #: When this version was written (aware UTC).
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, server_default=func.now())
    #: Id of the version that replaced this one; NULL on the tip of the chain.
    #: Not a foreign key: the two rows are written in one flush and a
    #: self-referential FK would order them for no benefit.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: Why this version exists. NULL on version 1.
    recompute_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LENGTH))
    #: Set when this version was written after the session had been matched
    #: (invariant 4). WP-7 surfaces it beside any score derived from it.
    edited_post_hoc: Mapped[bool] = mapped_column(Boolean, default=False)

    purpose: Mapped[Purpose] = mapped_column(enum_column(Purpose))
    intent_text: Mapped[str | None] = mapped_column(String(MAX_INTENT_CHARS))
    coach_notes: Mapped[str | None] = mapped_column(String(MAX_INTENT_CHARS))
    #: The success criteria, in the domain's tagged-union wire form.
    success_criteria: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, default=list
    )
    #: Anchor type -> anchor version id, both as strings. Frozen when this
    #: version was written; this is what invariant 4 calls the pin.
    pinned_anchor_versions: Mapped[dict[str, str]] = mapped_column(
        JSONColumn, default=dict
    )
    #: Where the prescription came from, if the library. Nulled rather than
    #: cascaded when that entry is deleted: the snapshot below still stands.
    #: Indexed because that SET NULL is a write against every intent row
    #: referencing the deleted workout, and unindexed it is a full scan.
    workout_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workouts.id", ondelete="SET NULL"), index=True
    )
    #: The prescription as frozen at this version.
    structure: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)

    @property
    def artefact_id(self) -> uuid.UUID:
        """The versioned artefact's stable identity: the planned session.

        Present so the row satisfies `app.domain.versioning.VersionRecord` and
        the domain's chain helpers apply to it unchanged.
        """
        return self.planned_session_id


class PlannedSessionRow(Base):
    """One session on the calendar."""

    __tablename__ = "planned_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: The athlete-local date the session belongs to (WP-4 assigns recordings
    #: to days the same way). A date, not an instant.
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    discipline: Mapped[Discipline] = mapped_column(enum_column(Discipline), index=True)
    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus), default=SessionStatus.PLANNED, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    # `passive_deletes`: the intent rows' foreign key carries ON DELETE
    # CASCADE, so the database is what removes the chain — the ORM must not go
    # looking for rows to delete itself. (Rows already in the session are still
    # deleted by the unit of work, which is why the clause is proved by a
    # statement that goes around the ORM entirely; see the CASCADE tests in
    # tests/unit/test_planned_sessions_api.py.)
    intents: Mapped[list[PlannedSessionIntentRow]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
        order_by=PlannedSessionIntentRow.version,
    )

    @property
    def current_intent(self) -> PlannedSessionIntentRow:
        """The intent version in force.

        The tip of the chain: the highest version nothing supersedes. A
        session always has one — it is created with version 1 in the same
        transaction — so this raises rather than returning ``None``, which
        would push a "cannot happen" branch into every caller.

        Raises:
            ValueError: When the chain is empty or every link is superseded,
                which means the version chain is broken.
        """
        # pyrefly: ignore[bad-specialization]
        # The row satisfies `VersionRecord` at runtime (asserted in
        # tests/unit/test_planned_sessions_api.py), but pyrefly does not see
        # through SQLAlchemy's `Mapped[X]` descriptors when structurally
        # matching a protocol — a plain class with the same five attributes
        # checks clean. Suppressed rather than worked around: reimplementing
        # "which version is in force" here is exactly the duplication
        # `app.domain.versioning` exists to prevent.
        tip = current_version(self.intents)
        if tip is None:
            raise ValueError(
                f"planned session {self.id} has no intent version in force; "
                "its version chain is broken"
            )
        return tip


class PlannedSessionRepository:
    """SQLAlchemy repository for planned sessions and their intent versions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, planned_session_id: uuid.UUID) -> PlannedSessionRow | None:
        """Return one planned session with its intent chain, or None."""
        return await self._session.get(PlannedSessionRow, planned_session_id)

    async def list(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        discipline: Discipline | None = None,
        status: SessionStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[PlannedSessionRow], int]:
        """Return a page of planned sessions, oldest first, plus the total.

        Oldest first because these are read as a calendar, where the natural
        order is the order they happen in. ``start`` and ``end`` are both
        inclusive.
        """
        criteria: list[Any] = []
        if start is not None:
            criteria.append(PlannedSessionRow.date >= start)
        if end is not None:
            criteria.append(PlannedSessionRow.date <= end)
        if discipline is not None:
            criteria.append(PlannedSessionRow.discipline == discipline)
        if status is not None:
            criteria.append(PlannedSessionRow.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(PlannedSessionRow).where(*criteria)
        )
        result = await self._session.execute(
            select(PlannedSessionRow)
            .where(*criteria)
            .order_by(PlannedSessionRow.date.asc(), PlannedSessionRow.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def by_ids(
        self, planned_session_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PlannedSessionRow]:
        """Several planned sessions by id, in one query, with their intents.

        For the pages that join *onto* planned sessions rather than list them:
        WP-6's proposal inbox is a page of links, and each row names the
        planned session its proposal is about.
        """
        if not planned_session_ids:
            return {}
        result = await self._session.execute(
            select(PlannedSessionRow).where(
                PlannedSessionRow.id.in_(planned_session_ids)
            )
        )
        return {row.id: row for row in result.scalars()}

    async def add(self, row: PlannedSessionRow) -> PlannedSessionRow:
        """Persist a session (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row

    async def delete(self, row: PlannedSessionRow) -> None:
        """Remove a planned session and its whole intent chain."""
        await self._session.delete(row)
        await flush(self._session)

    async def intent(
        self, planned_session_id: uuid.UUID, version: int
    ) -> PlannedSessionIntentRow | None:
        """Return one intent version of one session, or None."""
        result = await self._session.execute(
            select(PlannedSessionIntentRow).where(
                PlannedSessionIntentRow.planned_session_id == planned_session_id,
                PlannedSessionIntentRow.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def append_intent(
        self, row: PlannedSessionIntentRow
    ) -> PlannedSessionIntentRow:
        """Append an intent version.

        Raises:
            ConflictError: When the write violates a database constraint —
                including two writers appending the same version number.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row
