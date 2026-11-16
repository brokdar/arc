"""What the server remembers about the phone's outbox.

Two tables, both bookkeeping for `app.services.sync`; the training data itself
stays in `sessions`, `logged_sets` and `exercise_notes`, which the sync
projects onto so every existing reader sees a synced set exactly as it sees a
typed-in one.

* **`sync_mutations`** — one receipt per mutation id ever applied. It is what
  makes a replay a no-op (a known id answers `already_applied`), and its
  autoincrement `seq` is the server's receive order: the conflict rule's
  tie-breaker (`app.domain.sync`), a sequence rather than a timestamp because
  the wall clock steps backwards.
* **`sync_entities`** — per synced thing (a session, a set, an exercise note)
  the merged field values and each field's winning stamp. The values are kept
  here as well as on the projected row because a deleted set has no row:
  its tombstone, and the fields a later revival brings back, live only here.
  A tombstone instead of a `deleted_at` column on `logged_sets` was rejected
  because every reader of `logged_sets` would then have to filter it — the
  scorer, the matcher, the metric artefact, the MCP views — and one that
  forgot would count a deleted set.
"""

import datetime as dt
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.activity import SessionLifecycle
from app.domain.sync import MutationKind
from app.persistence.activity import SessionRow
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest key a synced entity may have: a uuid, or an exercise slug.
MAX_ENTITY_KEY_LENGTH = 64

#: Matches `audit_log.actor`.
MAX_ACTOR_LENGTH = 120


class SyncMutationRow(Base):
    """One applied mutation: its id, and the order it was received in."""

    __tablename__ = "sync_mutations"

    #: The receive sequence. Assigned by the database, strictly increasing.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The phone's id for this outbox item; a second sight of it is a replay.
    mutation_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    kind: Mapped[MutationKind] = mapped_column(enum_column(MutationKind))
    actor: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))
    #: For reading the trail, never for ordering (see the module docstring).
    received_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class SyncEntityRow(Base):
    """The merged state of one synced session, set or exercise note."""

    __tablename__ = "sync_entities"
    __table_args__ = (UniqueConstraint("entity", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: ``session``, ``set`` or ``note``.
    entity: Mapped[str] = mapped_column(String(16))
    #: The client id (session, set) or the exercise slug (note).
    key: Mapped[str] = mapped_column(String(MAX_ENTITY_KEY_LENGTH))
    #: The server session this entity belongs to (a session, or a set's
    #: session); null for a note. A deleted session takes its sync state with
    #: it.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    #: The merged fields, JSON-plain (enums by value, instants as ISO text).
    values: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: Each field's winning stamp, `app.domain.sync.encode_clocks`.
    clocks: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class SyncRepository:
    """Receipts and merged entity state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def known(self, mutation_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these mutation ids were applied before."""
        wanted = set(mutation_ids)
        if not wanted:
            return set()
        result = await self._session.execute(
            select(SyncMutationRow.mutation_id).where(
                SyncMutationRow.mutation_id.in_(wanted)
            )
        )
        return set(result.scalars())

    async def receive(
        self, mutation_id: uuid.UUID, kind: MutationKind, actor: str
    ) -> int:
        """Record a receipt and return its receive sequence.

        Raises:
            ConflictError: When the id was received concurrently.
        """
        row = SyncMutationRow(mutation_id=mutation_id, kind=kind, actor=actor)
        self._session.add(row)
        await flush(self._session)
        return row.seq

    async def entity(self, entity: str, key: str) -> SyncEntityRow | None:
        """One entity's merged state, or None if it was never synced."""
        result = await self._session.execute(
            select(SyncEntityRow).where(
                SyncEntityRow.entity == entity, SyncEntityRow.key == key
            )
        )
        return result.scalar_one_or_none()

    async def active_sessions(self) -> list[tuple[SyncEntityRow, SessionRow]]:
        """Every synced session still active, with its row, oldest first."""
        result = await self._session.execute(
            select(SyncEntityRow, SessionRow)
            .join(SessionRow, SessionRow.id == SyncEntityRow.session_id)
            .where(
                SyncEntityRow.entity == "session",
                SessionRow.lifecycle == SessionLifecycle.ACTIVE,
            )
            .order_by(SessionRow.start_time.asc(), SessionRow.id.asc())
        )
        return [(entity, row) for entity, row in result.all()]

    async def save(self, row: SyncEntityRow) -> SyncEntityRow:
        """Persist a new or changed entity state.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        return row
