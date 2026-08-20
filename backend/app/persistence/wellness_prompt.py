"""The daily wellness prompt row and its repository. No business logic here.

One row per athlete-local date, **unique on the date**. This is the whole of
"one prompt a day": the sweep that raises it runs hourly and is meant to fire
over the same date many times, so what stops a second prompt from existing is a
constraint the database refuses to violate — not a scheduler that happens not
to double-fire, and not a pre-check a future caller could forget.

Deliberately the same shape as `app.persistence.matching.EveningPromptRow`: a
dated row, a stored deadline, a nullable ``resolved_at`` and a status whose
terminal members a sweep writes. The daily prompt is that machinery again, and
a second prompt pattern would be two things to keep in step.

There is no follow-up row and no reminder count. See
`app.services.wellness.WellnessService.expire_prompts` for why the day simply
closes.
"""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import Date, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.wellness import WellnessPromptStatus
from app.persistence.db import Base, flush, refresh
from app.persistence.types import UtcDateTime, enum_column


class WellnessPromptRow(Base):
    """One day's question: raised once, then answered or closed unanswered."""

    __tablename__ = "wellness_prompts"
    __table_args__ = (
        # `uq_wellness_prompts_local_date` — one prompt per date, and **this
        # constraint is the promise**, not the sweep's discipline. The sweep is
        # idempotent and runs hourly over the same day; without this, a day the
        # athlete left alone would collect a prompt an hour, and the record
        # would show a fortnight of questions nobody was ever asked. The same
        # shape `EveningPromptRow` uses, named by the `Base` convention, and it
        # is a unique constraint rather than a unique index for that
        # symmetry — Postgres backs it with an index either way, which is what
        # the "is a prompt standing today" read on every surface scans.
        UniqueConstraint("local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: The day being asked about, on the athlete's own clock — the same date
    #: `app.persistence.wellness.WellnessDayRow.local_date` carries, which is
    #: what lets an answer resolve the question by date alone. No index of its
    #: own: the unique constraint above leads on this column.
    local_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[WellnessPromptStatus] = mapped_column(
        enum_column(WellnessPromptStatus),
        default=WellnessPromptStatus.PENDING,
        index=True,
    )
    #: When this prompt stops being answerable, stamped when it was raised
    #: (`WELLNESS__PROMPT_EXPIRY_HOURS` after). Stored rather than derived so
    #: the deadline is a fact about the prompt instead of a constant the sweep,
    #: the API and the Today view all have to agree on — the same reason
    #: `EveningPromptRow.expires_at` is stored.
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    #: When it reached a terminal status: answered, or closed unanswered. Null
    #: while pending.
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class WellnessPromptRepository:
    """SQLAlchemy repository for :class:`WellnessPromptRow`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, local_date: dt.date) -> WellnessPromptRow | None:
        """The prompt raised for ``local_date``, or None if none ever was."""
        return await self._session.scalar(
            select(WellnessPromptRow).where(WellnessPromptRow.local_date == local_date)
        )

    async def expired(
        self, *, now: dt.datetime, limit: int
    ) -> Sequence[WellnessPromptRow]:
        """Pending prompts whose deadline has passed, **oldest deadline first**.

        The filter comes before the batch, and the order is the one the sweep
        wants to work through — the lesson `EveningPromptRepository.expired`
        records: paging first starves the overdue prompts the sweep exists for
        whenever the pending backlog is larger than one batch.

        ``expires_at <= now`` because the answerable window is half-open
        ``[raised_at, expires_at)``, like every range in this codebase: the
        instant named by the deadline is already outside it.
        """
        result = await self._session.execute(
            select(WellnessPromptRow)
            .where(
                WellnessPromptRow.status == WellnessPromptStatus.PENDING,
                WellnessPromptRow.expires_at <= now,
            )
            .order_by(WellnessPromptRow.expires_at.asc(), WellnessPromptRow.id.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def add(self, row: WellnessPromptRow) -> WellnessPromptRow:
        """Persist a new or edited prompt and refresh server-generated fields.

        Raises:
            ConflictError: When the write violates the one-prompt-per-date
                constraint — which is a race, since the service pre-checks.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row
