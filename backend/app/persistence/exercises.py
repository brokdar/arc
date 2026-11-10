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
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.muscles import MuscleRegion
from app.domain.strength import (
    Exercise,
    ExerciseCategory,
    ExerciseEquipment,
    LoadType,
    Mechanic,
    MovementPattern,
    Restriction,
)
from app.persistence.db import Base, flush, refresh
from app.persistence.search import contains
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest slug the catalogue may carry.
MAX_SLUG_LENGTH = 80
#: Longest exercise note, in characters — a line or two for the gym floor
#: ("pins at hole 4", "left shoulder: neutral grip"), not a diary.
MAX_NOTE_LENGTH = 500


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
    #: ``{region: weight}`` as JSON — region keys are `MuscleRegion` values.
    #: JSON rather than a join table: it is read whole, written only by the
    #: seeder, and the group filter rolls it up in the domain anyway.
    muscles: Mapped[dict[str, float]] = mapped_column(
        JSONColumn, default=dict, server_default="{}"
    )
    #: The three scalar tags are nullable for one reason: rows seeded before
    #: migration 0027 hold none until the next seed, which runs on the first
    #: access after the upgrade and fills every row from the file. Nothing
    #: reads a row without seeding first.
    mechanic: Mapped[Mechanic | None] = mapped_column(enum_column(Mechanic))
    pattern: Mapped[MovementPattern | None] = mapped_column(
        enum_column(MovementPattern)
    )
    #: `ExerciseEquipment` values, sorted — sorted so that the seeder's diff
    #: compares like with like.
    equipment: Mapped[list[str]] = mapped_column(
        JSONColumn, default=list, server_default="[]"
    )
    load_type: Mapped[LoadType | None] = mapped_column(enum_column(LoadType))
    #: `Restriction` values, sorted, for the same reason.
    restrictions: Mapped[list[str]] = mapped_column(
        JSONColumn, default=list, server_default="[]"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> Exercise:
        """Project the row onto the pure domain value object.

        ``source`` is not stored — it documents the bundled file, not the
        row — so the projection says ``arc``.

        Raises:
            ValueError: When the row has not been seeded since migration 0027
                and so carries no tags yet.
        """
        if self.mechanic is None or self.pattern is None or self.load_type is None:
            raise ValueError(f"exercise {self.id} has not been tagged yet; seed first")
        return Exercise(
            id=self.id,
            name=self.name,
            category=self.category,
            unilateral=self.unilateral,
            muscles={
                MuscleRegion(region): weight for region, weight in self.muscles.items()
            },
            mechanic=self.mechanic,
            pattern=self.pattern,
            equipment=frozenset(ExerciseEquipment(item) for item in self.equipment),
            load_type=self.load_type,
            restrictions=frozenset(Restriction(tag) for tag in self.restrictions),
            source="arc",
        )

    @classmethod
    def from_domain(cls, exercise: Exercise) -> ExerciseRow:
        """The row the seeder writes for one catalogue entry."""
        return cls(id=exercise.id, **row_tags(exercise))


def row_tags(exercise: Exercise) -> dict[str, Any]:
    """Every column the file decides, in the row's own representation.

    One function for the seeder's write *and* its diff, so a tag cannot be
    written without being compared, or compared in a different shape than it
    is stored.
    """
    return {
        "name": exercise.name,
        "category": exercise.category,
        "unilateral": exercise.unilateral,
        "muscles": {
            region.value: weight for region, weight in sorted(exercise.muscles.items())
        },
        "mechanic": exercise.mechanic,
        "pattern": exercise.pattern,
        "equipment": sorted(item.value for item in exercise.equipment),
        "load_type": exercise.load_type,
        "restrictions": sorted(tag.value for tag in exercise.restrictions),
    }


class ExerciseNoteRow(Base):
    """The athlete's one note on one exercise.

    Keyed by the exercise slug, which is what makes it *one* note per
    exercise: a second write replaces the first rather than adding a row.
    Catalogue rows are never deleted, so the cascade is a formality that keeps
    the foreign key honest.
    """

    __tablename__ = "exercise_notes"

    exercise_id: Mapped[str] = mapped_column(
        String(MAX_SLUG_LENGTH),
        ForeignKey("exercises.id", ondelete="CASCADE"),
        primary_key=True,
    )
    text: Mapped[str] = mapped_column(String(MAX_NOTE_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
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

    async def matching(
        self,
        *,
        category: ExerciseCategory | None = None,
        query: str | None = None,
        mechanic: Mechanic | None = None,
        pattern: MovementPattern | None = None,
    ) -> Sequence[ExerciseRow]:
        """Return every entry matching the column filters, in display order.

        Unpaged on purpose: the service filters further on the JSON tags
        (muscle group, equipment) and pages after that, so `total` counts
        matches. The catalogue is bundled reference data of a few hundred
        rows; filtering them in Python is cheaper than a JSON operator per
        dialect.

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
        if mechanic is not None:
            criteria.append(ExerciseRow.mechanic == mechanic)
        if pattern is not None:
            criteria.append(ExerciseRow.pattern == pattern)
        result = await self._session.execute(
            select(ExerciseRow)
            .where(*criteria)
            .order_by(
                ExerciseRow.category.asc(),
                ExerciseRow.name.asc(),
                ExerciseRow.id.asc(),
            )
        )
        return list(result.scalars())

    async def add(self, row: ExerciseRow) -> ExerciseRow:
        """Insert or update a catalogue entry.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        merged = await self._session.merge(row)
        await flush(self._session)
        return merged


class ExerciseNoteRepository:
    """SQLAlchemy repository for :class:`ExerciseNoteRow`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, exercise_id: str) -> ExerciseNoteRow | None:
        """Return the note on one exercise, or None."""
        return await self._session.get(ExerciseNoteRow, exercise_id)

    async def put(self, exercise_id: str, text: str) -> ExerciseNoteRow:
        """Create or replace the note on one exercise.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        row = await self.get(exercise_id)
        if row is None:
            row = ExerciseNoteRow(exercise_id=exercise_id, text=text)
            self._session.add(row)
        else:
            row.text = text
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def delete(self, row: ExerciseNoteRow) -> None:
        """Remove a note."""
        await self._session.delete(row)
        await flush(self._session)
