"""Anchor-version ORM model and its repository. No business logic here.

The repository deliberately offers **no update and no delete**: anchor history
is append-only (build-plan invariant 3), and the cheapest way to keep that
true is to give the rest of the stack no method that could break it. The 405s
the API returns are the polite half of the same rule; this is the half that
cannot be bypassed by a new caller.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import Date, Float, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.anchors import (
    MAX_PROTOCOL_CHARS,
    MVP_STALENESS_STATE,
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
    StalenessState,
)
from app.persistence.db import Base, flush
from app.persistence.types import UtcDateTime, enum_column


class AnchorVersionRow(Base):
    """One immutable entry in an anchor's history.

    Named ``...Row`` because the domain owns the name ``AnchorVersion``: the
    two are converted by :meth:`to_domain`, and a service that means the pure
    value object should not get this by autocomplete.
    """

    __tablename__ = "anchor_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    anchor_type: Mapped[AnchorType] = mapped_column(enum_column(AnchorType), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[AnchorUnit] = mapped_column(enum_column(AnchorUnit))
    provenance: Mapped[Provenance] = mapped_column(enum_column(Provenance))
    #: How the value was measured. Required for `tested` provenance — the
    #: domain enforces that; the column stays nullable for the other three.
    protocol: Mapped[str | None] = mapped_column(String(MAX_PROTOCOL_CHARS))
    #: The date the value describes the athlete from — not the append time.
    effective_date: Mapped[dt.date] = mapped_column(Date, index=True)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    source: Mapped[AnchorSource] = mapped_column(enum_column(AnchorSource))
    #: Hardcoded `fresh` in the MVP; the staleness model is deferred, the
    #: column is not (build plan WP-1).
    staleness_state: Mapped[StalenessState] = mapped_column(
        enum_column(StalenessState), default=MVP_STALENESS_STATE
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )

    def to_domain(self) -> AnchorVersion:
        """Project the row onto the pure domain value object.

        Re-runs the domain's own validation, so a row that predates a rule
        fails loudly here instead of silently feeding a derived value.
        """
        return AnchorVersion(
            anchor_type=self.anchor_type,
            value=self.value,
            unit=self.unit,
            provenance=self.provenance,
            protocol=self.protocol,
            effective_date=self.effective_date,
            ci_low=self.ci_low,
            ci_high=self.ci_high,
            created_at=self.created_at,
            source=self.source,
            staleness_state=self.staleness_state,
        )


class AnchorRepository:
    """SQLAlchemy repository for :class:`AnchorVersionRow` — append and read only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, anchor_version_id: uuid.UUID) -> AnchorVersionRow | None:
        """Return one anchor version by id, or None."""
        return await self._session.get(AnchorVersionRow, anchor_version_id)

    async def get_many(
        self, anchor_version_ids: Iterable[uuid.UUID]
    ) -> Sequence[AnchorVersionRow]:
        """Return the versions with these ids, in one query, order unspecified.

        The batched half of :meth:`get`, and the reason a week of planned
        sessions resolves its pins without a round-trip per session. Ids with
        no row are simply absent from the result — a caller that pinned a
        version since deleted (which nothing can do: anchor history is
        append-only) gets fewer rows, not an exception.
        """
        ids = list(dict.fromkeys(anchor_version_ids))
        if not ids:
            return []
        result = await self._session.execute(
            select(AnchorVersionRow).where(AnchorVersionRow.id.in_(ids))
        )
        return list(result.scalars())

    async def list(
        self,
        *,
        anchor_type: AnchorType | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[AnchorVersionRow], int]:
        """Return a page of history, newest first, plus the total count.

        Newest first by ``(effective_date, created_at)`` — the reverse of the
        canonical `app.domain.anchors` ordering. Note "newest" is not "in
        force": a future-dated version sorts first here but is not in force
        yet, which is why `current` asks the domain instead of this method.
        """
        criteria = (
            (AnchorVersionRow.anchor_type == anchor_type,)
            if anchor_type is not None
            else ()
        )
        total = await self._session.scalar(
            select(func.count()).select_from(AnchorVersionRow).where(*criteria)
        )
        result = await self._session.execute(
            select(AnchorVersionRow)
            .where(*criteria)
            .order_by(
                AnchorVersionRow.effective_date.desc(),
                AnchorVersionRow.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def history(self, anchor_type: AnchorType) -> Sequence[AnchorVersionRow]:
        """Return the complete history of one anchor type, oldest first.

        Unpaged on purpose: "which version was in force at T" is a fold over
        the whole history (`app.domain.anchors.anchor_as_of`), and a single
        athlete's anchor history is a handful of rows per year.
        """
        result = await self._session.execute(
            select(AnchorVersionRow)
            .where(AnchorVersionRow.anchor_type == anchor_type)
            .order_by(
                AnchorVersionRow.effective_date.asc(),
                AnchorVersionRow.created_at.asc(),
            )
        )
        return list(result.scalars())

    async def latest_created_at(self, anchor_type: AnchorType) -> dt.datetime | None:
        """Return the newest ``created_at`` in one anchor type's history.

        One aggregate rather than :meth:`history`: `AnchorService.append` asks
        this on every write to keep the stamps it hands the tie-break strictly
        increasing, and it needs the one value, not the rows.
        """
        return await self._session.scalar(
            select(func.max(AnchorVersionRow.created_at)).where(
                AnchorVersionRow.anchor_type == anchor_type
            )
        )

    async def add(self, row: AnchorVersionRow) -> AnchorVersionRow:
        """Append a version and refresh server-generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row
