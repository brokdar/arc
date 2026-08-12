"""Plan-change proposals: one table, and no logic.

Named `plan_proposals`, never just "proposals": WP-6 already owns the word for
a *match* proposal (`session_matches` with status `pending`), and two things
called the same in one schema is a bug waiting for whoever reads it next.

**Both halves of the suggestion are stored, and they are not the same thing.**
``changes`` is what the agent asked for, in the domain's tagged-union wire form
(`app.domain.proposals.changes_to_json`) — it is the instruction, and it is
what `accept` replays. ``diff`` is what that would *do*, computed against the
plan as it stood when the proposal was written: per entity, before and after.
The athlete answers the diff; the service applies the changes; and because the
diff was computed at write time, a stale one is a visible fact rather than a
surprise — accepting re-checks every concurrency token before anything moves.

**The supersede link is two columns, not a chain table.** A proposal about a
session that already has an open proposal replaces it, and both rows record
the relationship: the old one points forward, the new one points back. Neither
is a foreign key, for the reason `planned_session_intents.superseded_by` is
not one — the two rows are written in a single flush, and a self-referential
FK would order them for no benefit.

**No target index.** "Which pending proposal touches this session?" is
answered by reading the pending proposals and looking at their diffs, not by a
join table. There is one athlete, proposals expire, and the pending set is
small enough to scan; a JSON-containment query would have to be written twice
(SQLite and Postgres disagree) to save a round trip nobody is waiting on.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import String, Uuid, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.proposals import (
    MAX_RATIONALE_CHARS,
    MAX_RESOLUTION_NOTE_CHARS,
    ProposalStatus,
)
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest actor string stored on a proposal. Matches `audit_log.actor`: it is
#: the same vocabulary (`athlete` / `agent:<label>` / `system`).
MAX_ACTOR_LENGTH = 120


class PlanProposalRow(Base):
    """One suggested change to the committed plan."""

    __tablename__ = "plan_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus),
        default=ProposalStatus.PENDING,
        index=True,
    )
    #: Why the agent is suggesting this. Required and non-empty — invariant 6
    #: says every proposal carries a rationale, and a proposal the athlete
    #: cannot evaluate is one they can only guess at.
    rationale: Mapped[str] = mapped_column(String(MAX_RATIONALE_CHARS))
    #: The instruction: the domain's tagged-union wire form, in order.
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list)
    #: What the instruction would do, per entity, before and after — computed
    #: when the proposal was written. See the module docstring.
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list)
    #: When this proposal stops being answerable. Required: "default on expiry
    #: = the committed plan stands" is only a policy if there is a deadline.
    #: Indexed because the expiry sweep filters on it.
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, index=True)
    #: `Actor` in its stored string form — `agent:<key-label>` for everything
    #: the coaching agent writes.
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )
    #: When the proposal left `pending`; NULL while it stands.
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: The athlete's own words on rejecting it. Free text, never parsed — it
    #: is testimony, and the seed of the coach-quality loop.
    resolution_note: Mapped[str | None] = mapped_column(
        String(MAX_RESOLUTION_NOTE_CHARS)
    )
    #: The proposal this one replaced, and the one that replaced this one. See
    #: the module docstring for why neither is a foreign key.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class PlanProposalRepository:
    """SQLAlchemy repository for plan-change proposals."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, proposal_id: uuid.UUID) -> PlanProposalRow | None:
        """Return one proposal, or None."""
        return await self._session.get(PlanProposalRow, proposal_id)

    async def list(
        self,
        *,
        status: ProposalStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[PlanProposalRow], int]:
        """Return a page of proposals, newest first, plus the total.

        Newest first because this is an inbox: the thing to answer is the
        thing that just arrived, not the one from three weeks ago.
        """
        criteria: list[Any] = []
        if status is not None:
            criteria.append(PlanProposalRow.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(PlanProposalRow).where(*criteria)
        )
        result = await self._session.execute(
            select(PlanProposalRow)
            .where(*criteria)
            .order_by(PlanProposalRow.created_at.desc(), PlanProposalRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def pending(self) -> Sequence[PlanProposalRow]:
        """Every proposal still standing, oldest first.

        Unpaged on purpose: the callers are "does this session already have an
        open proposal?" and "did the athlete just settle one by training?",
        and both are wrong if they see only a page. The set is bounded by the
        expiry sweep, which is what keeps that honest.
        """
        result = await self._session.execute(
            select(PlanProposalRow)
            .where(PlanProposalRow.status == ProposalStatus.PENDING)
            .order_by(PlanProposalRow.created_at.asc(), PlanProposalRow.id.asc())
        )
        return list(result.scalars())

    async def expired(
        self, *, now: dt.datetime, limit: int
    ) -> Sequence[PlanProposalRow]:
        """Pending proposals whose deadline has passed, **oldest deadline first**.

        The batch is taken after the deadline filter and in the order the
        sweep works through, not over the newest pending ones, in the
        letter: paging before filtering starves exactly the rows the sweep
        exists for whenever the backlog is larger than one batch.
        """
        result = await self._session.execute(
            select(PlanProposalRow)
            .where(
                PlanProposalRow.status == ProposalStatus.PENDING,
                PlanProposalRow.expires_at <= now,
            )
            .order_by(PlanProposalRow.expires_at.asc(), PlanProposalRow.id.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def add(self, row: PlanProposalRow) -> PlanProposalRow:
        """Persist a proposal (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row
