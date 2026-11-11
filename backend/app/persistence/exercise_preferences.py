"""The athlete's preferences per exercise, and their repository. No logic here.

One row per exercise the athlete has said something about: never offer it
(``avoid``), offer it after everything else (``less_often``), the swap they
picked last time (``remembered_swap_id``), and where they stand on *Ease back
in* (``ease_back_step``, null when not easing back). Keyed by the exercise
slug like `exercise_notes`, which is what makes it *one* row per exercise.

``ease_back_step`` is an index into the ladder `app.domain.swaps.ease_back_ladder`
computes, not a stored copy of it: the ladder is derived from the catalogue
and the avoid list, so storing it would let the two drift. A ladder that has
shrunk since the step was stored is clamped by the service when read.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db import Base, flush, refresh
from app.persistence.exercises import MAX_SLUG_LENGTH
from app.persistence.types import UtcDateTime


class ExercisePreferenceRow(Base):
    """What the athlete prefers about one exercise."""

    __tablename__ = "exercise_preferences"

    exercise_id: Mapped[str] = mapped_column(
        String(MAX_SLUG_LENGTH),
        ForeignKey("exercises.id", ondelete="CASCADE"),
        primary_key=True,
    )
    avoid: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    less_often: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    remembered_swap_id: Mapped[str | None] = mapped_column(
        String(MAX_SLUG_LENGTH),
        ForeignKey("exercises.id", ondelete="SET NULL"),
        default=None,
    )
    ease_back_step: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class ExercisePreferenceRepository:
    """SQLAlchemy repository for :class:`ExercisePreferenceRow`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, exercise_id: str) -> ExercisePreferenceRow | None:
        """The preferences on one exercise, or None when none were stated."""
        return await self._session.get(ExercisePreferenceRow, exercise_id)

    async def all(self) -> Sequence[ExercisePreferenceRow]:
        """Every stored row, by slug. Unpaged: at most one per catalogue entry."""
        result = await self._session.scalars(
            select(ExercisePreferenceRow).order_by(ExercisePreferenceRow.exercise_id)
        )
        return result.all()

    async def get_or_new(self, exercise_id: str) -> ExercisePreferenceRow:
        """The stored row, or a new all-default one added to the session."""
        row = await self.get(exercise_id)
        if row is None:
            row = ExercisePreferenceRow(
                exercise_id=exercise_id,
                avoid=False,
                less_often=False,
                remembered_swap_id=None,
                ease_back_step=None,
            )
            self._session.add(row)
        return row

    async def save(self, row: ExercisePreferenceRow) -> ExercisePreferenceRow:
        """Write ``row`` and read back its server-set columns."""
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row
