"""Storage for agent notes — the interpretive record (invariant 7).

Its own table rather than columns on `sessions`, for the reason the invariant
exists: everything on `sessions` is computed from the recording, and a model's
opinion parked among those columns would be indistinguishable from a
measurement at read time. Here it is one join away and carries its author.

See `app.domain.agent_notes` for what the columns mean and why the target is
exactly one of two.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.agent_notes import (
    MAX_MODEL_ID_CHARS,
    MAX_NOTE_CHARS,
    DisputeRating,
    NoteKind,
)
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Width of the stored actor string (`app.domain.actor.Actor`), as everywhere
#: else that records one.
MAX_ACTOR_LENGTH = 120


class AgentNoteRow(Base):
    """One piece of interpretive text about a session or a plan week."""

    __tablename__ = "agent_notes"

    #: The check is spelled `<>` rather than a pair of `AND`s because that is
    #: the whole rule: exactly one target, and the database is where "exactly"
    #: is enforceable regardless of which caller wrote the row.
    __table_args__ = (
        CheckConstraint(
            "(session_id IS NULL) <> (plan_week IS NULL)", name="one_target"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)

    #: The session this note is about, or null when it is about a week.
    #: `CASCADE`, because a note is *about* its subject: when the session goes,
    #: the note is commentary on nothing. (Contrast `cites`, which is loose by
    #: design — see `app.domain.agent_notes.parse_cites`.)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )

    #: The Monday of the plan week this note is about, or null when it is
    #: about a session. Not a foreign key: a plan week is a date range the
    #: calendar defines, not a row (`app.domain.plan`).
    plan_week: Mapped[dt.date | None] = mapped_column(Date, index=True)

    kind: Mapped[NoteKind] = mapped_column(enum_column(NoteKind), index=True)

    #: The note itself, stored verbatim and never parsed by anything.
    text: Mapped[str] = mapped_column(String(MAX_NOTE_CHARS))

    #: Which model said it. Required — see the domain module.
    model_id: Mapped[str] = mapped_column(String(MAX_MODEL_ID_CHARS), index=True)

    #: The actor string of the key that wrote it (`agent:<label>`). Both this
    #: and `model_id` are kept: the key says *which integration*, the model id
    #: says *which mind*, and swapping either one is a thing a reader of an old
    #: note needs to be able to see.
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH), index=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )

    #: Artefact ids as strings, in the order the note gave them.
    cites: Mapped[list[str]] = mapped_column(JSONColumn, default=list)

    #: The athlete's one-tap answer, or null if they have not given one.
    #: Overwritable in place rather than an append-only chain: this is a
    #: toggle on a card, and its history is nobody's evidence (the audit log
    #: keeps that).
    dispute: Mapped[DisputeRating | None] = mapped_column(enum_column(DisputeRating))

    #: When the current dispute was last set; null whenever `dispute` is.
    disputed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class AgentNoteRepository:
    """Data access for agent notes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: AgentNoteRow) -> AgentNoteRow:
        """Persist a note (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint —
                including the one-target check, which is the last line of
                defence behind `AgentNoteService`.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row

    async def get(self, note_id: uuid.UUID) -> AgentNoteRow | None:
        """Return one note, or None."""
        return await self._session.get(AgentNoteRow, note_id)

    async def for_session(self, session_id: uuid.UUID) -> Sequence[AgentNoteRow]:
        """Every note about one session, oldest first."""
        return await self._list(AgentNoteRow.session_id == session_id)

    async def for_week(self, plan_week: dt.date) -> Sequence[AgentNoteRow]:
        """Every note about one plan week, oldest first."""
        return await self._list(AgentNoteRow.plan_week == plan_week)

    async def _list(self, where: Any) -> Sequence[AgentNoteRow]:
        """Run one ordered lookup.

        Oldest first, unlike the proposal inbox: notes are a conversation
        about a session and are read in the order they were written. The id
        tiebreaker keeps that order total when two land in one clock tick.
        """
        result = await self._session.execute(
            select(AgentNoteRow)
            .where(where)
            .order_by(AgentNoteRow.created_at.asc(), AgentNoteRow.id.asc())
        )
        return result.scalars().all()
