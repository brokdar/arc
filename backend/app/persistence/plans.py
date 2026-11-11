"""Plans and their per-week tags.

`PlanRow` is the container every planned session belongs to
(`planned_sessions.plan_id`); the rules about it — phases, the half-open date
span, the overlap rule, the default plan — are in `app.domain.plans`.

**`is_default` is TRUE or NULL, never FALSE**, and unique. A unique constraint
ignores NULLs on both SQLite and Postgres, so "at most one default plan" is a
fact the database keeps without a partial index (which neither autogenerate
nor SQLite spells the same way). The ORM attribute reads it back as a plain
bool through :attr:`PlanRow.default`.

`PlanWeekTagRow` is one tag on one week of one plan: the week is keyed by its
Monday, and the triple is unique, so tagging a week twice is one row.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.plans import (
    DEFAULT_PLAN_NAME,
    MAX_PLAN_NAME_CHARS,
    PlanPhase,
    PlanWeekTag,
)
from app.persistence.db import Base, flush, refresh
from app.persistence.types import UtcDateTime, enum_column


class PlanRow(Base):
    """One plan."""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String(MAX_PLAN_NAME_CHARS))
    phase: Mapped[PlanPhase] = mapped_column(enum_column(PlanPhase))
    #: Stored here, enforced by each rule that must respect it (see
    #: `app.domain.plans`).
    follow_exactly: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: TRUE on the one default plan, NULL on every other. See the module
    #: docstring for why not FALSE.
    is_default: Mapped[bool | None] = mapped_column(Boolean, unique=True)
    #: First day of the plan, inclusive; NULL is unbounded.
    starts_on: Mapped[dt.date | None] = mapped_column(Date)
    #: First day *after* the plan; NULL is unbounded.
    ends_before: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def default(self) -> bool:
        """Whether this is the default plan, as a plain bool."""
        return bool(self.is_default)


class PlanWeekTagRow(Base):
    """One tag on one week of one plan."""

    __tablename__ = "plan_week_tags"
    __table_args__ = (UniqueConstraint("plan_id", "week_start", "tag"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the unique constraint leads on it.
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE")
    )
    #: The Monday of the tagged week.
    week_start: Mapped[dt.date] = mapped_column(Date)
    tag: Mapped[PlanWeekTag] = mapped_column(enum_column(PlanWeekTag))


class PlanRepository:
    """SQLAlchemy repository for plans and their week tags."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, plan_id: uuid.UUID) -> PlanRow | None:
        """One plan, or None."""
        return await self._session.get(PlanRow, plan_id)

    async def list(self) -> Sequence[PlanRow]:
        """Every plan, in creation order (uuid7, not the wall clock)."""
        result = await self._session.execute(select(PlanRow).order_by(PlanRow.id))
        return list(result.scalars())

    async def default(self) -> PlanRow | None:
        """The default plan, or None on a database that has none yet."""
        result = await self._session.execute(
            select(PlanRow).where(PlanRow.is_default.is_(True))
        )
        return result.scalar_one_or_none()

    async def ensure_default(self) -> PlanRow:
        """The default plan, created if this database has none.

        Migration `0028` creates it, so in a migrated database this only
        reads. A database built by ``create_all`` — the unit suite — has none
        until the first session needs one.
        """
        found = await self.default()
        if found is not None:
            return found
        return await self.add(
            PlanRow(
                name=DEFAULT_PLAN_NAME,
                phase=PlanPhase.BASE,
                follow_exactly=False,
                active=True,
                is_default=True,
            )
        )

    async def active_dated(self) -> Sequence[PlanRow]:
        """Every active plan but the default, in creation order."""
        result = await self._session.execute(
            select(PlanRow)
            .where(PlanRow.active.is_(True), PlanRow.is_default.is_(None))
            .order_by(PlanRow.id)
        )
        return list(result.scalars())

    async def add(self, row: PlanRow) -> PlanRow:
        """Persist a plan (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def week_tags(
        self, plan_id: uuid.UUID, week_start: dt.date
    ) -> Sequence[PlanWeekTag]:
        """The tags on one week of one plan."""
        result = await self._session.execute(
            select(PlanWeekTagRow.tag).where(
                PlanWeekTagRow.plan_id == plan_id,
                PlanWeekTagRow.week_start == week_start,
            )
        )
        return list(result.scalars())

    async def replace_week_tags(
        self, plan_id: uuid.UUID, week_start: dt.date, tags: Iterable[PlanWeekTag]
    ) -> None:
        """Make one week's tags exactly ``tags``."""
        await self._session.execute(
            delete(PlanWeekTagRow).where(
                PlanWeekTagRow.plan_id == plan_id,
                PlanWeekTagRow.week_start == week_start,
            )
        )
        for tag in tags:
            self._session.add(
                PlanWeekTagRow(plan_id=plan_id, week_start=week_start, tag=tag)
            )
        await flush(self._session)
