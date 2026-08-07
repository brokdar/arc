"""The workout library tables and their repository. No business logic here.

The prescription itself is stored as **one JSON document** (`structure`),
because a step tree is recursive and a relational shredding of it would be a
join per nesting level to read back something that is only ever read whole.
The document's shape is the domain's own wire form
(`app.domain.workout.workout_body_to_json`), and the service parses it back
through the domain on every read, so a row that predates a rule fails loudly
rather than feeding a scorer.

Tags get a table rather than a JSON array on the workout: "which workouts are
tagged X" is a query, and containment on a JSON array is spelled differently
on SQLite and Postgres — the one thing `app.persistence.types` exists to avoid.
Folders get a plain string column instead of a table, because an MVP folder is
a label, not a hierarchy with its own lifecycle.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ForeignKey, String, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.athlete import Discipline
from app.persistence.db import Base, flush
from app.persistence.search import contains
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest a workout name may be.
MAX_NAME_LENGTH = 200
#: Longest a workout description may be.
MAX_DESCRIPTION_LENGTH = 2_000
#: Longest a folder label may be.
MAX_FOLDER_LENGTH = 200
#: Longest a tag may be.
MAX_TAG_LENGTH = 60


class WorkoutTagRow(Base):
    """One tag on one workout. The pair is the primary key."""

    __tablename__ = "workout_tags"

    workout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(
        String(MAX_TAG_LENGTH), primary_key=True, index=True
    )


class WorkoutRow(Base):
    """One reusable prescription in the library."""

    __tablename__ = "workouts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), index=True)
    description: Mapped[str | None] = mapped_column(String(MAX_DESCRIPTION_LENGTH))
    discipline: Mapped[Discipline] = mapped_column(enum_column(Discipline), index=True)
    #: The prescription, in the domain's wire form. Tagged with its discipline
    #: by the domain, so the document is self-describing on its own.
    structure: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: A flat label, not a path. `None` means "unfiled".
    folder: Mapped[str | None] = mapped_column(String(MAX_FOLDER_LENGTH), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    # `selectin`: a lazy load would emit IO on attribute access, which in an
    # async session raises instead of querying.
    # `passive_deletes`: the tag rows' foreign key carries ON DELETE CASCADE,
    # so the database is what removes them and the ORM must not query for rows
    # to delete itself. Tags already loaded are still deleted by the unit of
    # work, so the clause itself is proved by a statement that goes around the
    # ORM (see the CASCADE test in tests/unit/test_workouts_api.py).
    tags: Mapped[list[WorkoutTagRow]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
        order_by=WorkoutTagRow.tag,
    )

    @property
    def tag_names(self) -> list[str]:
        """The workout's tags, sorted."""
        return [row.tag for row in self.tags]


class WorkoutRepository:
    """SQLAlchemy repository for :class:`WorkoutRow`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, workout_id: uuid.UUID) -> WorkoutRow | None:
        """Return one workout by id, or None."""
        return await self._session.get(WorkoutRow, workout_id)

    async def list(
        self,
        *,
        query: str | None = None,
        folder: str | None = None,
        tag: str | None = None,
        discipline: Discipline | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[WorkoutRow], int]:
        """Return a page of the library, newest first, plus the total count.

        ``query`` matches the name or the description, case-insensitively.
        The filters combine with AND.
        """
        criteria: list[Any] = []
        if query:
            criteria.append(
                contains(WorkoutRow.name, query)
                | contains(WorkoutRow.description, query)
            )
        if folder is not None:
            criteria.append(WorkoutRow.folder == folder)
        if discipline is not None:
            criteria.append(WorkoutRow.discipline == discipline)
        if tag is not None:
            criteria.append(
                exists().where(
                    WorkoutTagRow.workout_id == WorkoutRow.id,
                    WorkoutTagRow.tag == tag,
                )
            )
        total = await self._session.scalar(
            select(func.count()).select_from(WorkoutRow).where(*criteria)
        )
        result = await self._session.execute(
            select(WorkoutRow)
            .where(*criteria)
            .order_by(WorkoutRow.created_at.desc(), WorkoutRow.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def names(self, workout_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Return the names of the given workouts, keyed by id.

        Ids with no row are simply absent — a planned session's provenance
        link is nulled when the library entry goes (`ondelete="SET NULL"`),
        but a caller holding an id from elsewhere should not have to care.

        Exists so a calendar can label a week's sessions in one query instead
        of one per card; loading whole rows for a name would carry every
        structure document along with it.
        """
        if not workout_ids:
            return {}
        result = await self._session.execute(
            select(WorkoutRow.id, WorkoutRow.name).where(WorkoutRow.id.in_(workout_ids))
        )
        return {row.id: row.name for row in result}

    async def folders(self) -> Sequence[str]:
        """Return every folder label in use, sorted."""
        result = await self._session.execute(
            select(WorkoutRow.folder)
            .where(WorkoutRow.folder.is_not(None))
            .distinct()
            .order_by(WorkoutRow.folder.asc())
        )
        return [folder for folder in result.scalars() if folder is not None]

    async def tags(self) -> Sequence[str]:
        """Return every tag in use, sorted."""
        result = await self._session.execute(
            select(WorkoutTagRow.tag).distinct().order_by(WorkoutTagRow.tag.asc())
        )
        return list(result.scalars())

    async def add(self, row: WorkoutRow) -> WorkoutRow:
        """Persist a workout (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row

    async def delete(self, row: WorkoutRow) -> None:
        """Remove a workout and its tags."""
        await self._session.delete(row)
        await flush(self._session)

    def set_tags(self, row: WorkoutRow, tags: Sequence[str]) -> None:
        """Replace a workout's tags with ``tags``, as a diff.

        Synchronous: it only rearranges the loaded collection, and the
        delete-orphan cascade turns that into the right statements at flush
        time. A diff rather than "clear and re-add" because the latter deletes
        and re-inserts an unchanged tag inside one flush, and the unit of work
        does not guarantee the DELETE is ordered before the INSERT that
        reuses its primary key.
        """
        wanted = set(tags)
        present = {tag_row.tag for tag_row in row.tags}
        for tag_row in [tag for tag in row.tags if tag.tag not in wanted]:
            row.tags.remove(tag_row)
        for tag in sorted(wanted - present):
            row.tags.append(WorkoutTagRow(tag=tag))
