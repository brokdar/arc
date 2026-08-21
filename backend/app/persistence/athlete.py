"""The singleton athlete row and its repository. No business logic here.

There is exactly one athlete (single-user application, no user table), so
the row has a **fixed primary key**, :data:`SINGLETON_ATHLETE_ID`. That is what
makes "at most one athlete" true at the database level without a second column
or a dialect-specific check constraint: every write path goes through this
repository, which only ever addresses that id, so a race between two
bootstraps is a primary-key conflict (translated to a 409) rather than a
second athlete nobody notices.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, Date, Float, String, func
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.athlete import (
    MAX_RED_FLAG_NOTE_CHARS,
    AthleteProfile,
    RedFlagSeverity,
    Sex,
)
from app.domain.plan import PlanState
from app.persistence.db import Base, flush, refresh
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Primary key of the one and only athlete row.
#:
#: A constant rather than a generated uuid7 (the convention elsewhere): the id
#: of a singleton is not information, and a well-known one means every layer
#: can address the row without a lookup, and a duplicate insert collides.
SINGLETON_ATHLETE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Athlete(Base):
    """The athlete's profile. Exactly one row, id :data:`SINGLETON_ATHLETE_ID`."""

    __tablename__ = "athlete"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=SINGLETON_ATHLETE_ID
    )
    # Every profile field is nullable: the row is bootstrapped empty on first
    # access and filled in from the UI, so "not answered yet" is the normal
    # state, not an error.
    name: Mapped[str | None] = mapped_column(String(200))
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date)
    sex: Mapped[Sex] = mapped_column(enum_column(Sex), default=Sex.UNSPECIFIED)
    height_cm: Mapped[float | None] = mapped_column(Float)
    #: Free-form per-discipline capability stub (see `app.domain.athlete`).
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: Whether the plan is being enforced (`app.domain.plan`). Unlike the
    #: fields above it carries a `server_default`: it was added to a table that
    #: already had its one row, and "no answer yet" is not a state a plan can
    #: be in — an existing profile is on an active plan.
    plan_state: Mapped[PlanState] = mapped_column(
        enum_column(PlanState),
        default=PlanState.ACTIVE,
        server_default=PlanState.ACTIVE.value,
    )
    #: The illness/injury flag (WP-8.4). Carries a `server_default` for the
    #: reason `plan_state` does: the table already held its one row, and an
    #: existing profile is not flagged. The other two columns are nullable and
    #: need none — "no note" and "no grade" are the resting state.
    red_flag_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false()
    )
    red_flag_note: Mapped[str | None] = mapped_column(String(MAX_RED_FLAG_NOTE_CHARS))
    red_flag_severity: Mapped[RedFlagSeverity | None] = mapped_column(
        enum_column(RedFlagSeverity)
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> AthleteProfile:
        """Project the row onto the pure domain value object."""
        return AthleteProfile(
            name=self.name,
            date_of_birth=self.date_of_birth,
            sex=self.sex,
            height_cm=self.height_cm,
            capabilities=dict(self.capabilities or {}),
            plan_state=self.plan_state,
            red_flag_active=self.red_flag_active,
            red_flag_note=self.red_flag_note,
            red_flag_severity=self.red_flag_severity,
        )


class AthleteRepository:
    """SQLAlchemy repository for the singleton :class:`Athlete`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> Athlete | None:
        """Return the athlete row, or None before it is bootstrapped."""
        return await self._session.get(Athlete, SINGLETON_ATHLETE_ID)

    async def add(self, athlete: Athlete) -> Athlete:
        """Persist the row (new or modified) and refresh server-generated fields.

        Raises:
            ConflictError: When the write violates a database constraint —
                including two callers bootstrapping the singleton at once.
        """
        self._session.add(athlete)
        await flush(self._session)
        await refresh(self._session, athlete)
        return athlete
