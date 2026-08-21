"""The daily wellness row and its repository. No business logic here.

One row per athlete-local date, **unique on the date**: the "one consolidated
touchpoint per day" promise is held by the database rather than by a code path
that could forget it. Corrigible in place — see `app.domain.wellness` for why a
correction is an update rather than a second row — so unlike the anchor
repository this one has an update path. What it still has no method for is a
*delete*: a day the athlete reported is part of the record even after they
correct it, and the audit trail carries what it used to say.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Date, Float, Integer, String, Time, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.wellness import (
    MAX_NOTE_CHARS,
    BodyRegion,
    Confounder,
    HrvContext,
    HrvMetric,
    WellnessDay,
    WellnessProvenance,
    WellnessSource,
)
from app.persistence.db import Base, flush, refresh
from app.persistence.types import JSONColumn, UtcDateTime, enum_column


class WellnessDayRow(Base):
    """One athlete-local day of reported wellness.

    Named ``...Row`` because the domain owns the name
    :class:`app.domain.wellness.WellnessDay`; :meth:`to_domain` converts, and a
    service that means the pure value object should not get this by
    autocomplete.

    Every value column is nullable and **null means "not provided", never
    zero**. There is no all-null row — the domain refuses to build one, and
    :meth:`to_domain` re-runs that check on the way out, so a row written by
    something that is not this ORM fails loudly here rather than feeding a
    rolling mean.
    """

    __tablename__ = "wellness_days"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: The day the reading **describes**, on the same basis as
    #: `SessionRow.local_date` — except that an overnight reading belongs to
    #: the *wake* day (`app.domain.wellness.wellness_day_date`), where a
    #: session belongs to the day it started. Unique: one touchpoint per day.
    local_date: Mapped[dt.date] = mapped_column(Date, unique=True, index=True)

    sleep_duration_s: Mapped[int | None] = mapped_column(Integer)
    #: Clock times with **no date** — the date is the row's. Storing a second
    #: one would create two answers to which day the night belongs to.
    sleep_start_local: Mapped[dt.time | None] = mapped_column(Time)
    sleep_end_local: Mapped[dt.time | None] = mapped_column(Time)
    resting_hr_bpm: Mapped[int | None] = mapped_column(Integer)
    #: HRV in milliseconds. Not `hrv_rmssd_ms`: HealthKit only exposes SDNN, so
    #: the statistic is a stored discriminator rather than a promise in a
    #: column name (`app.domain.wellness.HrvMetric`).
    hrv_ms: Mapped[float | None] = mapped_column(Float)
    #: Non-null exactly when `hrv_ms` is — the domain enforces the triple.
    hrv_metric: Mapped[HrvMetric | None] = mapped_column(enum_column(HrvMetric))
    hrv_context: Mapped[HrvContext | None] = mapped_column(enum_column(HrvContext))
    respiratory_rate_brpm: Mapped[float | None] = mapped_column(Float)
    #: A **fraction** (0.97), per `.claude/rules/backend-domain-units.md`.
    spo2: Mapped[float | None] = mapped_column(Float)
    #: Deviation from the device's own baseline, which is what a watch reports.
    wrist_temperature_delta_c: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)

    sleep_quality: Mapped[int | None] = mapped_column(Integer)
    fatigue: Mapped[int | None] = mapped_column(Integer)
    soreness: Mapped[int | None] = mapped_column(Integer)
    stress: Mapped[int | None] = mapped_column(Integer)
    motivation: Mapped[int | None] = mapped_column(Integer)
    #: ``{region: 1..5}``, keys from `app.domain.wellness.BodyRegion`.
    soreness_by_region: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: A list of `app.domain.wellness.Confounder` values. JSON rather than a
    #: join table: it is a short set of tags read whole on every day read, and
    #: nothing queries "every day tagged alcohol" that a scan cannot serve at
    #: single-athlete scale.
    confounders: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    #: Free text, never parsed.
    note: Mapped[str | None] = mapped_column(String(MAX_NOTE_CHARS))

    provenance: Mapped[WellnessProvenance] = mapped_column(
        enum_column(WellnessProvenance), default=WellnessProvenance.ATHLETE_REPORTED
    )
    source: Mapped[WellnessSource] = mapped_column(enum_column(WellnessSource))

    #: When the row was first written. With `local_date` this is the whole of
    #: the backfill story: the pair is what makes a late entry derivable
    #: without a third column (`app.domain.wellness.is_late_entry`).
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> WellnessDay:
        """Project the row onto the pure domain value object.

        Re-runs the domain's own validation, so a row that predates a rule
        fails loudly here instead of silently feeding a derived number.
        """
        return WellnessDay(
            local_date=self.local_date,
            sleep_duration_s=self.sleep_duration_s,
            sleep_start_local=self.sleep_start_local,
            sleep_end_local=self.sleep_end_local,
            resting_hr_bpm=self.resting_hr_bpm,
            hrv_ms=self.hrv_ms,
            hrv_metric=self.hrv_metric,
            hrv_context=self.hrv_context,
            respiratory_rate_brpm=self.respiratory_rate_brpm,
            spo2=self.spo2,
            wrist_temperature_delta_c=self.wrist_temperature_delta_c,
            weight_kg=self.weight_kg,
            sleep_quality=self.sleep_quality,
            fatigue=self.fatigue,
            soreness=self.soreness,
            stress=self.stress,
            motivation=self.motivation,
            soreness_by_region={
                BodyRegion(region): rating
                for region, rating in (self.soreness_by_region or {}).items()
            },
            confounders=tuple(Confounder(value) for value in (self.confounders or [])),
            note=self.note,
            provenance=self.provenance,
            source=self.source,
        )


class WellnessRepository:
    """SQLAlchemy repository for :class:`WellnessDayRow` — read, add, update."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, local_date: dt.date) -> WellnessDayRow | None:
        """Return the day recorded for ``local_date``, or None."""
        return await self._session.scalar(
            select(WellnessDayRow).where(WellnessDayRow.local_date == local_date)
        )

    async def get_many(
        self, local_dates: Sequence[dt.date]
    ) -> dict[dt.date, WellnessDayRow]:
        """Return the recorded days among ``local_dates``, keyed by date.

        The batched half of :meth:`get`, and the reason a backfill of 300 days
        resolves create-or-update in one query rather than three hundred.
        Dates with no row are simply absent from the result.
        """
        wanted = list(dict.fromkeys(local_dates))
        if not wanted:
            return {}
        result = await self._session.execute(
            select(WellnessDayRow).where(WellnessDayRow.local_date.in_(wanted))
        )
        return {row.local_date: row for row in result.scalars()}

    async def range(
        self,
        *,
        start: dt.date,
        end: dt.date,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[WellnessDayRow], int]:
        """Return a page of the series over ``[start, end)``, plus the total.

        **Half-open**, like every range in this codebase: ``end`` is the first
        day *not* included. Oldest first — a wellness series is read forwards,
        the way a chart is drawn, where the session log reads backwards.

        Days with no row are simply absent; the caller reports the gaps
        (`app.domain.wellness.missing_dates`) rather than getting a
        null-filled object it cannot distinguish from a day reported as
        nothing.
        """
        criteria = (
            WellnessDayRow.local_date >= start,
            WellnessDayRow.local_date < end,
        )
        total = await self._session.scalar(
            select(func.count()).select_from(WellnessDayRow).where(*criteria)
        )
        result = await self._session.execute(
            select(WellnessDayRow)
            .where(*criteria)
            .order_by(WellnessDayRow.local_date.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def recorded_dates(self, *, start: dt.date, end: dt.date) -> set[dt.date]:
        """Every date in ``[start, end)`` that has a row, and nothing else.

        One column, no ORM objects and **no paging**: this answers "which days
        did the athlete answer over this range", which is a different question
        from "give me a page of them". Computing the gaps from a page instead
        reports every recorded day beyond the page as silence — see
        `app.domain.wellness.missing_dates`.
        """
        result = await self._session.execute(
            select(WellnessDayRow.local_date).where(
                WellnessDayRow.local_date >= start,
                WellnessDayRow.local_date < end,
            )
        )
        return set(result.scalars())

    async def weight_history(
        self, *, on_or_before: dt.date
    ) -> Sequence[WellnessDayRow]:
        """Return every day carrying a weight on or before ``on_or_before``.

        Filtered in SQL rather than folded over the whole series in Python:
        "the weight in force on date D" is asked on every session read, and
        most days carry no weight at all.
        """
        result = await self._session.execute(
            select(WellnessDayRow)
            .where(
                WellnessDayRow.weight_kg.is_not(None),
                WellnessDayRow.local_date <= on_or_before,
            )
            .order_by(WellnessDayRow.local_date.asc())
        )
        return list(result.scalars())

    async def add(self, row: WellnessDayRow) -> WellnessDayRow:
        """Persist a new or edited day and refresh server-generated fields.

        Raises:
            ConflictError: When the write violates the one-row-per-date
                constraint — which is a race, since the service pre-checks.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
        return row

    async def delete(self, row: WellnessDayRow) -> None:
        """Remove one day.

        The one destructive method on this repository, and it exists for
        exactly one caller: a write that clears the last value on a day
        (`app.services.wellness.WellnessService.record`). An absent row is
        already how this surface spells "the athlete reported nothing", so a
        retracted day and an unanswered one *are* the same state; the audit row
        keeps what it used to say. There is no "delete this day" use-case above
        it, and there should not be.
        """
        await self._session.delete(row)
        await flush(self._session)

    async def add_all(self, rows: Sequence[WellnessDayRow]) -> None:
        """Persist many days in **one** flush.

        The batch write's whole point: a partial migration leaves the athlete
        unable to tell which days made it, and the retry then has to reason
        about overlap. One flush inside the caller's transaction means the
        batch lands whole or not at all.
        """
        self._session.add_all(list(rows))
        await flush(self._session)
