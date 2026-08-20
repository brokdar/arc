"""The link between what was planned and what was done, and the missed prompt.

Two tables.

**`session_matches` is a link table, not a column** (build plan WP-6.5). A
foreign key on `sessions` would have been shorter and is the shape the whole
work package exists to avoid: the generality that matters — a set of recordings
answering a set of planned sessions — is a *schema* property, and re-shaping it
later is the expensive kind of change. So the relationship lives in its own
row, with its own status, its own score and its own audit trail, and the MVP's
one-to-one restriction is expressed as two unique constraints that a later
increment drops rather than as a column another increment has to migrate.

The MVP restriction is exactly that: **one link per session and one per planned
session**. It is enforced in the database (`uq_session_matches_session_id`,
`uq_session_matches_planned_session_id`) rather than only in the service,
because the service's "does one already exist" check is a read that can always
lose a race, and a session with two links is a state nothing downstream knows
how to render.

**Unlink restores, so the link remembers what it displaced.**
`previous_session_status` and `previous_planned_status` hold the two statuses
as they stood the instant before the link was made, which is what makes
build-plan WP-6.8's reversibility exact rather than approximate: unlinking a
match on a session that was already `displaced` puts it back to `displaced`,
not to `unmatched`. The link row is **deleted** on unlink — the history
lives in `audit_log`, which is append-only and outlives the entity by design,
and a table that kept dead links would have to express "the active one" as a
partial index instead of a plain constraint.

**`evening_prompts`** is the record WP-6.7 leaves behind when a planned session
goes past its grace with nothing linked to it. WP-6 writes it and reads it back
for nothing; WP-7 consumes it (the verdict flow's reason prompt, and the 72-hour
expiry that turns an unanswered one into `not_provided`).
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import (
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.activity import SessionMatchStatus
from app.domain.athlete import Discipline
from app.domain.matching import (
    STICKY_STATUSES,
    EveningPromptKind,
    EveningPromptStatus,
    MatchLinkStatus,
)
from app.domain.sessions import SessionStatus
from app.persistence.db import Base, flush, refresh
from app.persistence.planned_sessions import PlannedSessionRow
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest stored actor string — the same width `audit_log.actor` uses, and
#: the same vocabulary (`app.domain.actor.Actor`).
MAX_ACTOR_LENGTH = 120


class SessionMatchRow(Base):
    """One link between a completed session and a planned session."""

    __tablename__ = "session_matches"
    __table_args__ = (
        # The MVP's one-to-one restriction, in the database rather than only in
        # the service — see the module docstring.
        UniqueConstraint("session_id"),
        UniqueConstraint("planned_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the unique constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: Likewise covered by its own unique constraint.
    planned_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="CASCADE")
    )
    #: What the link claims, and whether matching may revise it
    #: (`app.domain.matching.STICKY_STATUSES`). Indexed because the proposal
    #: inbox is a query for one value of it.
    status: Mapped[MatchLinkStatus] = mapped_column(
        enum_column(MatchLinkStatus), index=True
    )
    #: The score that produced the link, in ``[0, 1]``. **Null is not zero**:
    #: it means no component of the comparison could be assessed (a hand-typed
    #: gym session that logged no sets), and the breakdown says which.
    similarity: Mapped[float | None] = mapped_column(Float)
    #: `app.domain.matching.similarity_to_json` — every component, the weight
    #: applied to it and the two numbers it compared, plus the components that
    #: could not be assessed and why. Stored rather than recomputed: the score
    #: was taken against the anchor versions the intent pinned and the metric
    #: artefact in force at the time, and a later recompute of either must not
    #: silently rewrite the reason a link exists.
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: `app.domain.actor.Actor` in its stored string form — `system` for a link
    #: matching made on ingest, `athlete` for one the athlete made by hand.
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))
    #: The two statuses this link displaced, so unlinking restores them exactly
    #: (WP-6.8). Not derivable afterwards: "what would this session have been
    #: if the link had never existed" has no other answer.
    previous_session_status: Mapped[SessionMatchStatus] = mapped_column(
        enum_column(SessionMatchStatus)
    )
    previous_planned_status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus)
    )
    #: When the athlete confirmed the link; null while it is still a machine
    #: verdict. A `displaced` link carries one too — it is a deliberate act.
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def sticky(self) -> bool:
        """Whether matching must leave this link alone (WP-6.6)."""
        return self.status in STICKY_STATUSES


class EveningPromptRow(Base):
    """One prompt raised about a planned session (WP-6.7; WP-7 consumes it)."""

    __tablename__ = "evening_prompts"
    __table_args__ = (
        # One open prompt per planned session. The sweep is idempotent — it
        # runs hourly over the same backlog — and without this a session left
        # unmatched for a week would collect a prompt a day.
        UniqueConstraint("planned_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the unique constraint above leads on this column.
    planned_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="CASCADE")
    )
    kind: Mapped[EveningPromptKind] = mapped_column(enum_column(EveningPromptKind))
    #: WP-6 writes `pending` and nothing else; the terminal members are WP-7's.
    status: Mapped[EveningPromptStatus] = mapped_column(
        enum_column(EveningPromptStatus),
        default=EveningPromptStatus.PENDING,
        index=True,
    )
    #: When this prompt stops being answerable
    #: (`app.domain.matching.PROMPT_TTL_HOURS` after it was raised). Stored so
    #: the deadline is a fact about the prompt rather than a constant WP-7's
    #: expiry job has to agree with.
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class SessionMatchRepository:
    """SQLAlchemy repository for match links."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, link_id: uuid.UUID) -> SessionMatchRow | None:
        """Return one link, or None."""
        return await self._session.get(SessionMatchRow, link_id)

    async def for_session(self, session_id: uuid.UUID) -> SessionMatchRow | None:
        """The link on one completed session, or None."""
        result = await self._session.execute(
            select(SessionMatchRow).where(SessionMatchRow.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def for_planned_session(
        self, planned_session_id: uuid.UUID
    ) -> SessionMatchRow | None:
        """The link on one planned session, or None."""
        result = await self._session.execute(
            select(SessionMatchRow).where(
                SessionMatchRow.planned_session_id == planned_session_id
            )
        )
        return result.scalar_one_or_none()

    async def for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMatchRow]:
        """Links for several completed sessions, in one query, keyed by session."""
        held = list(session_ids)
        if not held:
            return {}
        result = await self._session.execute(
            select(SessionMatchRow).where(SessionMatchRow.session_id.in_(held))
        )
        return {row.session_id: row for row in result.scalars()}

    async def for_planned_sessions(
        self, planned_session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMatchRow]:
        """Links for several planned sessions, in one query, keyed by planned id."""
        held = list(planned_session_ids)
        if not held:
            return {}
        result = await self._session.execute(
            select(SessionMatchRow).where(SessionMatchRow.planned_session_id.in_(held))
        )
        return {row.planned_session_id: row for row in result.scalars()}

    async def list(
        self,
        *,
        status: MatchLinkStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionMatchRow], int]:
        """A page of links, newest first, plus the total.

        Newest first because the one screen this serves is the proposal inbox,
        and a proposal is about a ride that just arrived.
        """
        criteria: list[Any] = []
        if status is not None:
            criteria.append(SessionMatchRow.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(SessionMatchRow).where(*criteria)
        )
        result = await self._session.execute(
            select(SessionMatchRow)
            .where(*criteria)
            .order_by(SessionMatchRow.created_at.desc(), SessionMatchRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def candidates(
        self,
        *,
        for_session: uuid.UUID,
        discipline: Discipline,
        earliest: dt.date,
        latest: dt.date,
    ) -> Sequence[PlannedSessionRow]:
        """Planned sessions this session could answer to (build plan WP-6.1).

        Same discipline, inside the date window, and **not already linked** to
        *another* session. The unlinked filter is what makes the
        two-planned-one-done case work: the second ride of the day sees only
        the session the first one did not take, rather than re-proposing a
        session that is already answered for.

        ``for_session``'s own link is excluded from that filter, not honoured
        by it. A re-match has to be able to reach the session it is already
        proposed against — otherwise re-running matching over an unchanged
        session would find no candidates and conclude the ride was unplanned,
        which is the opposite of idempotent.

        `missed` sessions are candidates. The sweep marks a session missed at
        the end of day+1 and the window reaches a day either side, so a ride
        uploaded late can and should still claim the session it was.

        The status filter carries the same own-link exception: an `auto_high`
        link has already moved its planned session to `completed`, and
        filtering that row out would defeat the exclusion above it — the
        re-match would not see its own target, conclude the ride was
        unplanned, and drop a settled link.
        """
        linked = select(SessionMatchRow.planned_session_id).where(
            SessionMatchRow.session_id != for_session
        )
        own = select(SessionMatchRow.planned_session_id).where(
            SessionMatchRow.session_id == for_session
        )
        result = await self._session.execute(
            select(PlannedSessionRow)
            .where(
                PlannedSessionRow.discipline == discipline,
                PlannedSessionRow.date >= earliest,
                PlannedSessionRow.date <= latest,
                or_(
                    PlannedSessionRow.status.in_(
                        (SessionStatus.PLANNED, SessionStatus.MISSED)
                    ),
                    PlannedSessionRow.id.in_(own),
                ),
                PlannedSessionRow.id.not_in(linked),
            )
            .order_by(PlannedSessionRow.date.asc(), PlannedSessionRow.id.asc())
        )
        return list(result.scalars())

    async def unanswered_planned_sessions(
        self, *, on_or_before: dt.date, limit: int
    ) -> Sequence[PlannedSessionRow]:
        """Still-open planned sessions whose grace has run out (WP-6.7).

        Open means `planned`: a session already marked missed has been swept,
        and one that is completed or displaced has been answered. **Any link
        at all keeps a session out of the sweep, a pending proposal included**:
        a proposal is a standing question in the UI, and marking the
        session missed underneath it would both nag the athlete about a ride
        the machine has already found and corrupt the statuses the link is
        holding for its restore. Answering the proposal settles the session
        either way; rejecting it puts the session back in the sweep's reach.
        Bounded because the sweep runs against the whole history of the plan,
        and a first run after a long absence must not load all of it at once.
        """
        linked = select(SessionMatchRow.planned_session_id)
        result = await self._session.execute(
            select(PlannedSessionRow)
            .where(
                PlannedSessionRow.date <= on_or_before,
                PlannedSessionRow.status == SessionStatus.PLANNED,
                PlannedSessionRow.id.not_in(linked),
            )
            .order_by(PlannedSessionRow.date.asc(), PlannedSessionRow.id.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def add(self, row: SessionMatchRow) -> SessionMatchRow:
        """Persist a link (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When either side is already linked.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def delete(self, row: SessionMatchRow) -> None:
        """Remove a link. The audit row is what survives it."""
        await self._session.delete(row)
        await flush(self._session)


class EveningPromptRepository:
    """SQLAlchemy repository for evening prompts. WP-7 is the consumer."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_planned_session(
        self, planned_session_id: uuid.UUID
    ) -> EveningPromptRow | None:
        """The prompt raised about one planned session, or None."""
        result = await self._session.execute(
            select(EveningPromptRow).where(
                EveningPromptRow.planned_session_id == planned_session_id
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        status: EveningPromptStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[EveningPromptRow], int]:
        """A page of prompts, newest first, plus the total."""
        criteria: list[Any] = []
        if status is not None:
            criteria.append(EveningPromptRow.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(EveningPromptRow).where(*criteria)
        )
        result = await self._session.execute(
            select(EveningPromptRow)
            .where(*criteria)
            .order_by(EveningPromptRow.created_at.desc(), EveningPromptRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def expired(
        self, *, now: dt.datetime, limit: int
    ) -> Sequence[EveningPromptRow]:
        """Pending prompts whose deadline has passed, **oldest deadline first**.

        The batch is taken *after* the deadline filter and in the order the
        sweep wants to work through, which :meth:`list` cannot do for it: that
        one pages newest-first for the inbox, so a sweep that borrowed it drew
        the freshest prompts — the ones least likely to have expired — and
        discarded most of them. With more pending prompts than the batch size,
        the genuinely overdue ones were never in the page at all and were
        starved until the backlog fell below the limit.
        """
        result = await self._session.execute(
            select(EveningPromptRow)
            .where(
                EveningPromptRow.status == EveningPromptStatus.PENDING,
                EveningPromptRow.expires_at <= now,
            )
            .order_by(EveningPromptRow.expires_at.asc(), EveningPromptRow.id.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def add(self, row: EveningPromptRow) -> EveningPromptRow:
        """Persist a prompt and refresh generated fields.

        Raises:
            ConflictError: When the session already has one.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row
