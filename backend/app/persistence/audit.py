"""The audit log: one row per write, appended by the service layer.

Every mutating use-case appends here — build plan WP-1.6 — which is what makes
the agent surface reviewable later (WP-8 states its guardrails in terms of
``actor=agent:<key-label>``). Like the anchor repository, this one offers no
update and no delete: an audit trail that can be edited is not one.

``actor`` is stored as the string form of `app.domain.actor.Actor`
(``athlete`` / ``agent:<key-label>`` / ``system``) and round-trips through
``Actor.parse``.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import String, Uuid, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.actor import Actor
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime


class AuditLogEntry(Base):
    """One recorded write."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: `Actor` in its stored string form; see the module docstring.
    actor: Mapped[str] = mapped_column(String(120), index=True)
    #: Dotted verb naming the use-case, e.g. `athlete.updated`.
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    #: Null for writes that are not about one row (none today; kept nullable
    #: so a future bulk action does not need a migration to be auditable).
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: What changed, as JSON. No foreign key to the entity: the audit row must
    #: survive the entity, and must stay readable once the row's shape moves on.
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), index=True
    )


class AuditRepository:
    """SQLAlchemy repository for :class:`AuditLogEntry` — append and read only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor: Actor,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Append one audit row.

        Flushed, not committed: the row joins the transaction of the write it
        describes, so an audit entry can never outlive a rolled-back change
        (nor a change escape unaudited).
        """
        entry = AuditLogEntry(
            actor=str(actor),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload or {},
        )
        self._session.add(entry)
        await flush(self._session)
        return entry

    async def count_since(self, *, actor_prefix: str, since: dt.datetime) -> int:
        """Count rows written at or after ``since`` by an actor with this prefix.

        The trailing-window query behind WP-8.3's rate cap. It counts the
        **audit log** rather than a counter table because the audit log is
        already the record of every write the guardrail is trying to bound —
        a separate counter could disagree with it, and the one that would be
        believed is the one nobody is looking at.

        ``actor_prefix`` is matched with ``LIKE 'prefix%'``, so ``agent:``
        covers every key label at once. Do not read the index on ``actor`` as
        a promise here: a plain btree under a non-C collation does not serve a
        ``LIKE`` prefix on Postgres (that needs `text_pattern_ops`), so this
        may well be a scan filtered by ``at``. At single-athlete scale — one
        agent, a trailing hour, an audit log measured in thousands of rows —
        that is fine, and it is cheaper than carrying an index for it.
        """
        total = await self._session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(
                AuditLogEntry.actor.startswith(actor_prefix),
                AuditLogEntry.at >= since,
            )
        )
        return total or 0

    async def count_for_entity_since(
        self, *, action: str, entity_id: uuid.UUID, since: dt.datetime
    ) -> int:
        """Count one entity's rows for one action at or after ``since``.

        The trailing-window read behind "how many rides has this folder
        delivered this week". It counts the audit log for the same reason
        :meth:`count_since` does: the trail is already the record of every
        write, so a counter column beside it would be a second answer that can
        drift from the first — and a delivery count that disagrees with the
        trail is worse than no delivery count, because the coach reasons over
        it as if it were the pipeline's own word.
        """
        total = await self._session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_id == entity_id,
                AuditLogEntry.at >= since,
            )
        )
        return total or 0

    async def list(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[AuditLogEntry], int]:
        """Return a page of audit rows, newest first, plus the total count."""
        total = await self._session.scalar(
            select(func.count()).select_from(AuditLogEntry)
        )
        result = await self._session.execute(
            select(AuditLogEntry)
            .order_by(AuditLogEntry.at.desc(), AuditLogEntry.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0
