"""Suggestion cards: one table, and no logic.

The budget lives in `app.domain.cards`; this module stores what it counts.
``first_shown`` and ``expires_at`` are both stored though one derives from the
other: ``expires_at`` is what the expiry sweep filters on, and an index on a
computed expression would have to be written twice (SQLite and Postgres
disagree on how).

``headline`` and ``because`` are columns, ``payload`` is a JSON document: every
card has the first two and the UI renders exactly them, while the payload is
the kind's own business (which exercise to swap, which import to look at) and
is read only by that kind's handler.

``priority`` is a plain integer (`CardPriority`'s value), not an
`enum_column`: that helper stores member values as strings, and a priority is
a number the queue sorts by.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import SmallInteger, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.activity import SessionLifecycle
from app.domain.cards import (
    MAX_BECAUSE_CHARS,
    MAX_HEADLINE_CHARS,
    MAX_KIND_CHARS,
    OPEN_STATES,
    CardState,
)
from app.persistence.activity import SessionRow
from app.persistence.db import Base, flush, refresh
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest actor string stored on a card. Matches `audit_log.actor`.
MAX_ACTOR_LENGTH = 120


class CardRow(Base):
    """One suggestion card."""

    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: The registry key (`app.services.cards`) naming who handles an answer.
    kind: Mapped[str] = mapped_column(String(MAX_KIND_CHARS), index=True)
    #: `app.domain.cards.CardPriority` by value; lower surfaces first.
    priority: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[CardState] = mapped_column(
        enum_column(CardState), default=CardState.QUEUED, index=True
    )
    headline: Mapped[str] = mapped_column(String(MAX_HEADLINE_CHARS))
    #: The one sentence that says why arc is showing this.
    because: Mapped[str] = mapped_column(String(MAX_BECAUSE_CHARS))
    #: The kind's own data. See the module docstring.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: When the card was first put in front of the athlete; NULL while queued.
    #: Never moved once set — the budget and the expiry both count from it.
    first_shown: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: ``first_shown`` + 7 days; NULL while queued. Indexed for the sweep.
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, index=True)
    #: Until when "Not now" hides it; NULL otherwise.
    snoozed_until: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: When it was accepted, dismissed or expired; NULL while open.
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: `Actor` in its stored string form.
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class CardRepository:
    """SQLAlchemy repository for suggestion cards."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, card_id: uuid.UUID) -> CardRow | None:
        """Return one card, or None."""
        return await self._session.get(CardRow, card_id)

    async def add(self, row: CardRow) -> CardRow:
        """Stage ``row`` and flush it; the service commits."""
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def budget_relevant(self, *, now: dt.datetime) -> Sequence[CardRow]:
        """Every open card, plus every card ever shown in the last 8 days.

        What the budget needs and no more: the open cards are the candidates,
        and an answered card counts against the budget only within the plan
        week it was first shown in, which never reaches back 8 days.
        """
        horizon = now - dt.timedelta(days=8)
        result = await self._session.execute(
            select(CardRow)
            .where(
                CardRow.state.in_(OPEN_STATES)
                | (CardRow.first_shown.is_not(None) & (CardRow.first_shown >= horizon))
            )
            .order_by(CardRow.id)
        )
        return result.scalars().all()

    async def expired(self, *, now: dt.datetime, limit: int) -> Sequence[CardRow]:
        """Open cards whose ``expires_at`` has passed, oldest deadline first."""
        result = await self._session.execute(
            select(CardRow)
            .where(
                CardRow.state.in_(OPEN_STATES),
                CardRow.expires_at.is_not(None),
                CardRow.expires_at <= now,
            )
            .order_by(CardRow.expires_at, CardRow.id)
            .limit(limit)
        )
        return result.scalars().all()

    async def training_active(self) -> bool:
        """Whether any session is between Start and Finish."""
        found = await self._session.scalar(
            select(SessionRow.id)
            .where(SessionRow.lifecycle == SessionLifecycle.ACTIVE)
            .limit(1)
        )
        return found is not None

    async def open_of_kind(self, kind: str) -> Sequence[CardRow]:
        """Every still-answerable card of one kind, in queue order."""
        result = await self._session.execute(
            select(CardRow)
            .where(CardRow.kind == kind, CardRow.state.in_(OPEN_STATES))
            .order_by(CardRow.id)
        )
        return result.scalars().all()

    async def delete(self, row: CardRow) -> None:
        """Remove a card outright; the service commits."""
        await self._session.delete(row)
        await flush(self._session)
