"""The exercise catalogue table and its repository. No business logic here.

The primary key is the exercise **slug**, not a uuid — the one place in this
schema that departs from the `uuid.uuid7` convention, deliberately. The
catalogue is bundled reference data, and prescriptions reference an exercise
from inside a workout's JSON structure, where no foreign key can reach: the
identifier therefore has to be stable, readable and reproducible across every
deployment, which is exactly what a generated id is not.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import Boolean, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.strength import Exercise, ExerciseCategory
from app.persistence.db import Base, flush
from app.persistence.search import contains
from app.persistence.types import UtcDateTime, enum_column

#: Longest slug the catalogue may carry.
MAX_SLUG_LENGTH = 80


class ExerciseRow(Base):
    """One catalogue movement, seeded from the bundled JSON."""

    __tablename__ = "exercises"

    #: The stable slug (`back_squat`); see the module docstring.
    id: Mapped[str] = mapped_column(String(MAX_SLUG_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[ExerciseCategory] = mapped_column(
        enum_column(ExerciseCategory), index=True
    )
    #: Whether the movement is performed one side at a time.
    unilateral: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> Exercise:
        """Project the row onto the pure domain value object."""
        return Exercise(
            id=self.id,
            name=self.name,
            category=self.category,
            unilateral=self.unilateral,
        )


class ExerciseRepository:
    """SQLAlchemy repository for :class:`ExerciseRow`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, exercise_id: str) -> ExerciseRow | None:
        """Return one catalogue entry by slug, or None."""
        return await self._session.get(ExerciseRow, exercise_id)

    async def all(self) -> Sequence[ExerciseRow]:
        """Return the whole catalogue, unordered.

        Unpaged: the catalogue is bundled reference data of a known, small
        size, and the seeder needs all of it to diff against the file.
        """
        result = await self._session.execute(select(ExerciseRow))
        return list(result.scalars())

    async def count(self) -> int:
        """Return how many entries the catalogue holds."""
        return (
            await self._session.scalar(select(func.count()).select_from(ExerciseRow))
            or 0
        )

    async def list(
        self,
        *,
        category: ExerciseCategory | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ExerciseRow], int]:
        """Return a page of the catalogue, plus the total count.

        Ordered by category then name so a picker groups sensibly without the
        client re-sorting, and by slug last: display names are not unique, and
        two rows tied on the sort key may land on either side of a page
        boundary from one request to the next — losing one entry and showing
        another twice. ``query`` is a case-insensitive substring match on the
        display name.
        """
        criteria = []
        if category is not None:
            criteria.append(ExerciseRow.category == category)
        if query:
            criteria.append(contains(ExerciseRow.name, query))
        total = await self._session.scalar(
            select(func.count()).select_from(ExerciseRow).where(*criteria)
        )
        result = await self._session.execute(
            select(ExerciseRow)
            .where(*criteria)
            .order_by(
                ExerciseRow.category.asc(),
                ExerciseRow.name.asc(),
                ExerciseRow.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def add(self, row: ExerciseRow) -> ExerciseRow:
        """Insert or update a catalogue entry.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        merged = await self._session.merge(row)
        await flush(self._session)
        return merged
