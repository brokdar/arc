"""The integrations the athlete has added, and how to read them back.

One table, and it is deliberately thin: an `integrations` row records **that
the athlete asked arc to collect from this source**, and nothing else. What the
source provides and how it may be collected are facts about the catalogue
(`app.domain.integrations.CATALOGUE`), not about the row — storing them here
would freeze a copy of the spec at the moment of adding, so widening Wahoo to a
second transport would leave every existing installation on the old shape.

There is no row for the local drop. It is **synthesized** by
`IntegrationService.list` (see `SYNTHESIZED_KINDS`): `data/inbox/` has swept
since WP-4.3 whether or not anyone configured anything, and a row for it would
be one the athlete could delete and never get back.

The cloud-folder transport binding is `feeds`, not a table here.
`FeedRow` already carries the cursor, the attempt counter, the delivery stamp
and the error a folder transport needs, and it already holds the
`(connection_id, remote_path)` uniqueness that makes "no two integrations share
a folder" true. A separate `integration_transports` table would be a second
place for the same folder to be recorded.
"""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from app.domain.integrations import IntegrationKind
from app.persistence.connections import FeedRow
from app.persistence.db import Base, flush, refresh
from app.persistence.types import UtcDateTime, enum_column


class IntegrationRow(Base):
    """One source the athlete has asked arc to collect from.

    **One row per kind**, held by the database. "Add Wahoo again with a second
    folder" is one integration with two folders, not two Wahoos — the panel
    lists sources, and two entries both called Wahoo would be a list the
    athlete cannot act on. The constraint rather than a service check because
    the migration's backfill writes here too, and two feeds at the Wahoo
    default path under different connections must collapse to one row.
    """

    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("kind", name="uq_integrations_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    kind: Mapped[IntegrationKind] = mapped_column(enum_column(IntegrationKind))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )

    #: The folders this integration is collected through. Deleting the
    #: integration takes them with it (AC-9) — a feed with no integration
    #: behind it would keep polling a folder nobody asked for any more.
    feeds: Mapped[list[FeedRow]] = relationship(
        back_populates="integration",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FeedRow.remote_path",
    )


class IntegrationRepository:
    """SQLAlchemy repository for integrations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> Sequence[IntegrationRow]:
        """Every integration, feeds eagerly loaded.

        Eager for the reason `ConnectionRepository.list` is: an async session
        cannot lazy-load, so a plain `row.feeds` at serialisation time raises
        `MissingGreenlet` rather than issuing a query.
        """
        result = await self._session.execute(
            select(IntegrationRow)
            .options(selectinload(IntegrationRow.feeds))
            .order_by(IntegrationRow.created_at, IntegrationRow.kind)
        )
        return list(result.scalars())

    async def get(self, integration_id: uuid.UUID) -> IntegrationRow | None:
        """One integration with its feeds, or None."""
        result = await self._session.execute(
            select(IntegrationRow)
            .options(selectinload(IntegrationRow.feeds))
            .where(IntegrationRow.id == integration_id)
        )
        return result.scalars().first()

    async def by_kind(self, kind: IntegrationKind) -> IntegrationRow | None:
        """The integration of this kind, or None. At most one can exist."""
        result = await self._session.execute(
            select(IntegrationRow)
            .options(selectinload(IntegrationRow.feeds))
            .where(IntegrationRow.kind == kind)
        )
        return result.scalars().first()

    async def add(self, row: IntegrationRow) -> IntegrationRow:
        """Persist an integration and refresh it."""
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row, ["feeds"])
        return row

    async def delete(self, row: IntegrationRow) -> None:
        """Delete an integration; its feeds go with it by cascade."""
        await self._session.delete(row)
        await flush(self._session)

    async def unclassified_feeds(self) -> Sequence[FeedRow]:
        """Feeds no integration owns — configured before integrations existed.

        Not an error and not hidden: a folder arc has been polling since before
        this vocabulary is still collecting, and the athlete is the only one
        who can say which source it is.
        """
        result = await self._session.execute(
            select(FeedRow)
            .where(FeedRow.integration_id.is_(None))
            .order_by(FeedRow.remote_path)
        )
        return list(result.scalars())
