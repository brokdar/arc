"""Use-cases for the daily wellness series.

**A day is corrigible; history is not.** There is an update path here, which
there deliberately is not in `app.services.anchors`, and the difference is not
an inconsistency: "appending never edits history" means a *new* reading never
rewrites an *earlier* one, not that the athlete who typed 6.5 h of sleep as 65
cannot fix it. Making a day immutable would force a correction to become a
second row for the same date, and then every read needs a which-one rule. So
one row per date, corrigible in place — and **every write appends an audit row
with the before/after diff**, exactly as `AthleteService.update` does, which is
where the old value goes.

**Backfill is a first-class write, not an import script.** Every write is dated
and any past date is a legal target of the ordinary path, so backfilling one
day needs nothing added. On top of that :meth:`WellnessService.record_many`
takes many days at once, because a year of readings through a per-day endpoint
is 365 round trips and, over MCP, 365 writes against a 60-per-hour cap. Three
properties it has, each for a reason that has already bitten this repo:

* **one transaction** — a partial migration leaves the athlete unable to tell
  which days made it, and the retry then has to reason about overlap;
* **errors named by date** — "validation failed" over 300 days is unactionable;
* **``dry_run`` with the same bounds as the write** — the bounds are domain
  rules on the shared path, so anything a dry run accepts the real write
  accepts (issue #17).

**A batch costs one write against the agent's hourly cap, not N.** The cap is a
circuit breaker on how much an agent changes per hour, and a 90-day migration
is one decision the athlete asked for, not 90. It is bounded instead by
`app.domain.wellness.MAX_BACKFILL_DAYS`, which is the honest place to put the
limit. The batch is therefore audited as **one row carrying every day's diff**
— which keeps the trail complete without letting the counter charge ninety
times for one instruction.
"""

import datetime as dt
import uuid
from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
    domain_rules,
)
from app.core.logging import get_logger
from app.domain.activity import parse_timezone
from app.domain.actor import Actor
from app.domain.plan import week_start
from app.domain.wellness import (
    MAX_BACKFILL_DAYS,
    OBJECTIVE_FIELDS,
    SUBJECTIVE_FIELDS,
    TERMINAL_PROMPT_STATUSES,
    WRITABLE_FIELDS,
    BodyRegion,
    Confounder,
    WeightInForce,
    WellnessDay,
    WellnessPromptStatus,
    WellnessProvenance,
    WellnessSource,
    is_late_entry,
    missing_dates,
    weight_in_force,
)
from app.domain.wellness_baseline import (
    BASELINE_WINDOW_DAYS,
    MARKERS,
    MARKERS_BY_METRIC,
    DaySample,
    MetricTrend,
    Readiness,
    WellnessMetric,
    readiness,
    trend_for,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, session_scope
from app.persistence.wellness import WellnessDayRow, WellnessRepository
from app.persistence.wellness_prompt import (
    WellnessPromptRepository,
    WellnessPromptRow,
)
from app.services.guardrails import check_write_cap

logger = get_logger(__name__)

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "wellness_day"

#: `entity_type` written on the daily prompt's audit rows.
PROMPT_ENTITY_TYPE = "wellness_prompt"

#: Scheduler id of the raise-and-expire sweep. One job, because the two halves
#: read the same clock and the same table, and a second job would be a second
#: place the prompt hour has to be agreed on.
PROMPT_SWEEP_JOB_ID = "wellness_prompt_sweep"

#: Longest range a weekly fold will read in one call. The same bound
#: `app.services.history` puts on a training summary, and for the same reason:
#: a year and a week is the natural ask, and everything past it is a caller
#: that meant to page.
MAX_WELLNESS_RANGE_DAYS = 371

#: The three HRV columns travel together: a statistic and a context describe a
#: reading, so clearing the reading clears them. Stated here rather than left
#: to the caller because the alternative is a 422 telling the athlete to send
#: three fields to retract one typo.
HRV_FIELDS = ("hrv_ms", "hrv_metric", "hrv_context")


class DayOutcome(StrEnum):
    """What one day of a write turned out to be."""

    CREATED = "created"
    UPDATED = "updated"
    #: Every value on the day was cleared, so the row goes. See
    #: :meth:`WellnessService.record`.
    RETRACTED = "retracted"


@dataclass(frozen=True, slots=True)
class DayResult:
    """One day's outcome, as both a real write and a dry run report it.

    ``day`` is the domain value the write produced (or *would* produce), so a
    dry run and the call after it cannot disagree about what will be stored —
    and ``None`` when the write retracted the day, because there is then no day
    to describe. ``changed`` is the before/after diff that lands on the audit
    row — empty when a write set a field to what it already held, which is a
    legal and unremarkable thing for a re-run of a backfill to do.
    """

    local_date: dt.date
    outcome: DayOutcome
    day: WellnessDay | None
    changed: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class WellnessPage:
    """One page of the series, **and the gaps in the whole range**.

    The two travel together because they answer one question and are computed
    over different scopes: ``days`` is a page, ``missing`` is every date in the
    range with no row at all. Returning only the page and letting the caller
    derive the gaps is the shape that produced the bug this class exists to
    make unrepresentable — a paged read reported 40 of 90 recorded days as
    silence.
    """

    days: Sequence[WellnessDayRow]
    #: How many days in the range have a row, ignoring paging.
    total: int
    #: Dates in the range nobody answered, oldest first.
    missing: tuple[dt.date, ...]


@dataclass(frozen=True, slots=True)
class MetricMean:
    """One metric's mean over a bucket, and the ``n`` behind it.

    The ``n`` is not decoration. A seven-day mean over three readings and one
    over seven are different objects, and a reader comparing them without
    knowing which is which is being misled by arithmetic that looks identical.
    """

    #: The field's name — except for HRV, which is named
    #: ``hrv_ms[rmssd,sleeping]`` because a statistic and a context are part of
    #: what the number *is*, and pooling two of them produces a mean belonging
    #: to neither.
    metric: str
    mean: float
    n: int


@dataclass(frozen=True, slots=True)
class WellnessWeek:
    """One Monday-to-Sunday week of the series, folded.

    ``start`` and ``end`` are the week **intersected with the requested
    range**, on the same reasoning as `app.services.history.HistoryWeek`: a
    partial week is visibly partial rather than passing for a whole one.
    """

    start: dt.date
    end: dt.date
    #: Days in this week with any row at all. The denominator for compliance,
    #: and the number that says a week of two readings is a week of two.
    days_recorded: int
    #: Days whose objective markers a declared confounder voided. They are
    #: excluded from the objective means above and counted here, so a thin
    #: ``n`` has a visible reason.
    days_invalidated: int
    #: Days entered late enough for the subjective half to be recall.
    days_recalled: int
    metrics: tuple[MetricMean, ...]


@dataclass(frozen=True, slots=True)
class WellnessWeeks:
    """What :meth:`WellnessService.weeks` answers with."""

    start: dt.date
    end: dt.date
    #: Every week the range touches, oldest first, **including empty ones** —
    #: a fortnight with nothing reported is a fact about the athlete's
    #: compliance, and skipping the blanks would show two thin weeks as
    #: adjacent full ones.
    weeks: tuple[WellnessWeek, ...]


@dataclass(frozen=True, slots=True)
class WellnessTrend:
    """What :meth:`WellnessService.trend` answers with.

    ``metrics`` holds only what the caller asked for; ``readiness`` is computed
    over **every** marker regardless, because a projection whose denominator
    depended on which metrics the caller happened to request would say
    something different every call.
    """

    start: dt.date
    end: dt.date
    #: The day the baseline and the rolling mean are anchored to — the last day
    #: of the requested range, so a historical range gets the baseline as it
    #: stood then rather than today's.
    as_of: dt.date
    metrics: Mapping[WellnessMetric, MetricTrend]
    readiness: Readiness


@dataclass(frozen=True, slots=True)
class DayInput:
    """One day of a batch write: the date, and the fields it sets.

    ``updates`` follows the same rule the per-day path follows — an omitted
    field is left alone, an explicit ``None`` clears it — so re-running a
    backfill over days that already exist never discards a field the batch did
    not mention.
    """

    local_date: dt.date
    updates: Mapping[str, Any]


class WellnessService:
    """Use-cases for the daily wellness series. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: WellnessRepository,
        prompts: WellnessPromptRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._prompts = prompts
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            WellnessRepository(session),
            WellnessPromptRepository(session),
            AuditRepository(session),
        )

    # --- reads ----------------------------------------------------------------

    @staticmethod
    def local_today() -> dt.date:
        """Today on the athlete's own clock.

        From `MATCHING__TIMEZONE` — the same clock the missed-session sweep
        runs on. There is one athlete and therefore one local clock;
        introducing a second source of "what day is it" is how the plan and the
        wellness series come to disagree about Tuesday.
        """
        zone = parse_timezone(get_settings().matching.timezone)
        return dt.datetime.now(dt.UTC).astimezone(zone).date()

    async def get(self, local_date: dt.date) -> WellnessDayRow:
        """Return the day recorded for ``local_date``.

        Raises:
            NotFoundError: When nothing was recorded that day. A 404 rather
                than a null-filled object: "the athlete reported nothing on the
                14th" and "the athlete reported nothing *at all* on the 14th"
                must not render as the same thing, and a synthesized day of
                nulls is indistinguishable from a day answered with blanks.
        """
        row = await self._repository.get(local_date)
        if row is None:
            raise NotFoundError(
                f"No wellness was recorded for {local_date.isoformat()}"
            )
        return row

    async def range(
        self, *, start: dt.date, end: dt.date, offset: int = 0, limit: int = 50
    ) -> WellnessPage:
        """Return a page of the series over the half-open range ``[start, end)``.

        **The page carries its own gaps.** ``missing`` is computed from a
        range-scoped query, not from the rows on the page, and it is returned
        from here rather than left to the adapters precisely so that neither of
        them can compute it from what it happens to be holding: doing that
        reports every recorded day past the first page as a day the athlete
        said nothing on, which inverts the one thing this read exists to be
        honest about.

        Raises:
            ValidationError: When ``end`` is before ``start``, or the range is
                longer than :data:`MAX_WELLNESS_RANGE_DAYS`.
        """
        self._check_range(start, end)
        rows, total = await self._repository.range(
            start=start, end=end, offset=offset, limit=limit
        )
        recorded = await self._repository.recorded_dates(start=start, end=end)
        return WellnessPage(
            days=rows,
            total=total,
            missing=missing_dates(recorded, start=start, end=end),
        )

    @staticmethod
    def _check_range(start: dt.date, end: dt.date) -> None:
        """Refuse a range that is inverted or longer than the bound.

        Raises:
            ValidationError: With the bound named, so the caller's next request
                can be a legal one.
        """
        # `end == start` is a legal empty range — half-open arithmetic makes
        # its length `end - start`, and a caller asking for no days gets no
        # days. `end < start` is arithmetic that has gone wrong.
        if end < start:
            raise ValidationError(
                f"end ({end.isoformat()}) must not be before start "
                f"({start.isoformat()}): the range is half-open, so the last "
                "day you want back is end minus one"
            )
        # Bounded for the same reason the weekly fold is, and it matters more
        # here: the answer names **every** date in the range that has no row,
        # so an unbounded range on an empty database is a list of every day
        # since whenever the caller asked from — tens of thousands of date
        # strings describing nothing.
        span = (end - start).days
        if span > MAX_WELLNESS_RANGE_DAYS:
            raise ValidationError(
                f"a wellness range covers at most {MAX_WELLNESS_RANGE_DAYS} "
                f"days, got {span}; ask for one block at a time"
            )

    async def days_for(
        self, local_dates: Iterable[dt.date]
    ) -> dict[dt.date, WellnessDayRow]:
        """Return the recorded days among ``local_dates``, keyed by date.

        What a session read uses to carry the wellness of its own day without
        a second round trip per session.
        """
        return await self._repository.get_many(list(local_dates))

    async def weight_in_force(self, on: dt.date) -> WeightInForce | None:
        """The body weight governing ``on``, with the date it was recorded.

        ``None`` when nothing was weighed on or before that date — an answer,
        not a gap, and watts per kilogram is then absent rather than computed
        against a default nobody has.
        """
        rows = await self._repository.weight_history(on_or_before=on)
        with domain_rules():
            return weight_in_force((row.to_domain() for row in rows), on)

    def is_recalled(self, row: WellnessDayRow) -> bool:
        """Whether this day's **subjective** ratings were entered from memory.

        Objective readings are never discounted for lateness and subjective
        ones are — see `app.domain.wellness.is_late_entry` for the asymmetry
        and why it is what makes backfill worth building.
        """
        zone = get_settings().matching.timezone
        return is_late_entry(row.local_date, row.created_at, zone)

    async def trend(
        self, *, start: dt.date, end: dt.date, metrics: Sequence[str] | None = None
    ) -> WellnessTrend:
        """The dated readings, rolling means and baselines over ``[start, end)``.

        **The window read is wider than the range asked for.** A baseline looks
        back sixty days from the anchor date, so a caller asking for the last
        fortnight still gets a mature baseline behind it — the alternative is a
        trend whose maturity depends on how much of it you happened to request,
        which is the same number meaning different things on different calls.
        One indexed scan either way (`local_date` is unique and indexed), which
        is also why nothing here is cached: see
        `app.domain.wellness_baseline` for why a stored baseline would be a
        stale one.

        Every marker is computed, not only the requested ones, because
        ``readiness``'s denominator has to be a fact about the athlete rather
        than about the query string.

        Raises:
            ValidationError: When the range is inverted or longer than
                :data:`MAX_WELLNESS_RANGE_DAYS`, or a metric is not one this
                surface knows — named, with the whole vocabulary.
        """
        self._check_range(start, end)
        wanted = self._check_metrics(metrics)
        # The anchor is the last day *in* the half-open range. An empty range
        # has no last day, and the day before its start is the honest anchor:
        # nothing in it has happened yet.
        anchor = end - dt.timedelta(days=1)
        window_start = min(start, anchor - dt.timedelta(days=BASELINE_WINDOW_DAYS - 1))
        window_end = max(end, anchor + dt.timedelta(days=1))
        rows, _ = await self._repository.range(
            start=window_start,
            end=window_end,
            offset=0,
            limit=(window_end - window_start).days,
        )
        days = [
            DaySample(day=row.to_domain(), subjective_recalled=self.is_recalled(row))
            for row in rows
        ]
        with domain_rules():
            computed = {
                marker.metric: trend_for(marker, days, start=start, end=end, on=anchor)
                for marker in MARKERS
            }
            projection = readiness(computed, on=anchor)
        return WellnessTrend(
            start=start,
            end=end,
            as_of=anchor,
            metrics={name: computed[name] for name in wanted},
            readiness=projection,
        )

    @staticmethod
    def _check_metrics(metrics: Sequence[str] | None) -> tuple[WellnessMetric, ...]:
        """Resolve the requested metrics, refusing an unknown one by name.

        Omitting them asks for every marker. An unknown one is refused with the
        whole vocabulary in the message, because an error the caller cannot act
        on costs a round trip it should never have paid for (the #19 lesson).

        Raises:
            ValidationError: Naming the offending metric and every legal one.
        """
        if not metrics:
            return tuple(marker.metric for marker in MARKERS)
        legal = ", ".join(MARKERS_BY_METRIC)
        resolved: list[WellnessMetric] = []
        for name in metrics:
            marker = MARKERS_BY_METRIC.get(str(name))
            if marker is None:
                raise ValidationError(
                    f"{name!r} is not a wellness metric. The vocabulary is: {legal}."
                )
            resolved.append(marker.metric)
        return tuple(dict.fromkeys(resolved))

    async def weeks(self, *, start: dt.date, end: dt.date) -> WellnessWeeks:
        """Fold the series into Monday-to-Sunday weeks, one mean per metric.

        The coach's natural unit for "how did sleep track against load last
        month" is the week it already reviews training in — the same fold
        `search_history` makes over sessions. Without it, that question is
        thirty day objects a model folds by hand, every time.

        Three rules the fold obeys, each of them a rule stated elsewhere in
        this increment:

        * **every mean reports the ``n`` it was computed over.** A weekly mean
          from three readings and one from seven are different objects, and a
          coach comparing them without knowing which is which is being misled
          by arithmetic that looks identical.
        * **days with an invalidating confounder are excluded from the
          objective means** and counted separately, on the reasoning that a
          mean built partly out of artefacts is worse than a shorter honest
          one. The subjective ratings of such a day still count: the athlete's
          own report of how they felt is not invalidated by why.
        * **HRV is never pooled across statistic or context.** A sleeping
          RMSSD mean and a daytime SDNN mean are not one series, so each
          (metric, context) pair gets its own entry, named.

        Raises:
            ValidationError: When the range is inverted or longer than
                :data:`MAX_WELLNESS_RANGE_DAYS` (:meth:`_check_range`).
        """
        self._check_range(start, end)
        span = (end - start).days
        rows, _ = await self._repository.range(
            start=start, end=end, offset=0, limit=span
        )
        days = [row.to_domain() for row in rows]
        recalled = {row.local_date for row in rows if self.is_recalled(row)}
        return WellnessWeeks(
            start=start,
            end=end,
            weeks=tuple(
                _fold_week(
                    monday=monday,
                    days=[
                        day
                        for day in days
                        if monday <= day.local_date < monday + dt.timedelta(days=7)
                    ],
                    recalled=recalled,
                    start=start,
                    end=end,
                )
                for monday in _mondays(start, end)
            ),
        )

    # --- writes ---------------------------------------------------------------

    async def record(
        self,
        local_date: dt.date,
        updates: Mapping[str, Any],
        *,
        actor: Actor,
        source: WellnessSource,
        dry_run: bool = False,
    ) -> DayResult:
        """Record or correct one day.

        An omitted field is left unchanged and an explicit ``None`` clears it,
        which is the same contract the API's PATCH body and the MCP tool's
        ``clear`` argument express two ways.

        **Clearing the last value on a day retracts the day**, and the row
        goes. The alternative was refusing — but a day that holds one wrong
        reading would then hold it forever, which is exactly the permanent lie
        in a baseline that clearing exists to prevent. Nothing is lost: the
        audit row carries what the day used to say, and an absent row is
        already how this surface spells "the athlete reported nothing", so a
        retracted day and an unanswered one read the same because they *are*
        the same. A day that never existed is still refused: there is nothing
        to retract, and a caller sending an empty write has made a mistake.

        Args:
            local_date: The day the readings describe. Any past or present
                date is legal — that is what makes backfilling one day need
                nothing added. A **future** date is refused.
            updates: The fields to set, from
                `app.domain.wellness.WRITABLE_FIELDS`.
            actor: Who is writing; goes on the audit row.
            source: Whether the athlete or the agent wrote it. The agent
                records what it was told and never signs as the athlete, so
                this is set by the adapter and never by the payload.
            dry_run: Validate everything and return what *would* be stored,
                writing nothing and costing no rate-cap budget.

        Raises:
            ValidationError: For an unknown field, a future date, or any
                domain rule the resulting day breaks.
            RateLimitedError: When an agent's hourly write cap is spent.
        """
        self._check_fields(updates)
        result = await self._write_one(
            local_date, updates, actor=actor, source=source, dry_run=dry_run
        )
        if result is not None:
            return result
        # Lost the create race — see :meth:`_write_one`. The winner's row is
        # committed by now, so the retry folds these updates onto it and
        # produces exactly what arriving second sequentially would have.
        retried = await self._write_one(
            local_date, updates, actor=actor, source=source, dry_run=dry_run
        )
        if retried is None:
            raise ConflictError(
                f"{local_date.isoformat()} was written concurrently twice while "
                "this write was in flight; read the day and try again"
            )
        return retried

    async def _write_one(
        self,
        local_date: dt.date,
        updates: Mapping[str, Any],
        *,
        actor: Actor,
        source: WellnessSource,
        dry_run: bool,
    ) -> DayResult | None:
        """One attempt at recording a day; ``None`` when it lost a create race.

        The race is real and the fuzzer found it: two writes for a date that
        does not exist yet both read "absent", both INSERT, and the unique
        index on ``local_date`` refuses the second — which reached the client
        as a 409 about a database constraint. Two browser tabs, or the
        athlete's form saving while the agent transcribes the same morning, is
        all it takes.

        Handled the way `AthleteService.get` handles the same race on the
        singleton profile: the loser does not fail, it re-reads and applies
        itself on top of the winner. Returning ``None`` rather than retrying
        in place is what keeps that honest — the failed flush has already
        rolled this session back, so the retry has to re-read *and* re-resolve
        against what actually landed, not against the stale row it was holding.
        """
        existing = await self._repository.get(local_date)
        result = self._resolve(
            local_date,
            updates,
            existing=existing,
            source=source,
            today=self.local_today(),
        )
        if dry_run:
            return result

        await check_write_cap(self._session, actor)
        row = existing or WellnessDayRow(local_date=local_date)
        if result.day is None:
            # The audit row is written first, while the row it describes still
            # exists to be pointed at.
            await self._audit_day(actor, result, entity_id=row.id)
            await self._repository.delete(row)
        else:
            _apply(row, result.day)
            try:
                row = await self._repository.add(row)
            except ConflictError:
                if existing is not None:
                    # Not the create race: this row was already there, so a
                    # constraint refusal is something the caller has to see.
                    raise
                return None
            await self._audit_day(actor, result, entity_id=row.id)
        # Inside this transaction, after the day has been validated and
        # written: the answer and the day it answers land together or not at
        # all. A retracted day still counts — the athlete answered and then
        # cleared it, which is not the same as never having answered.
        await self._answer_standing_prompt(local_date)
        await commit(self._session)
        return result

    async def _audit_day(
        self, actor: Actor, result: DayResult, *, entity_id: uuid.UUID
    ) -> None:
        """Append the audit row for one per-day write."""
        await self._audit.record(
            actor=actor,
            action=f"wellness.{result.outcome.value}",
            entity_type=ENTITY_TYPE,
            entity_id=entity_id,
            payload={
                "local_date": result.local_date.isoformat(),
                "changed": dict(result.changed),
            },
        )

    async def record_many(
        self,
        days: Sequence[DayInput],
        *,
        actor: Actor,
        source: WellnessSource,
        dry_run: bool = False,
    ) -> Sequence[DayResult]:
        """Record many days in **one transaction**.

        The tool for migrating a file of historical readings. Every day is
        validated before any is written, so a batch containing one invalid day
        leaves **no** rows behind — and the refusal names the offending date
        and field, because "validation failed" over three hundred days is
        unactionable.

        Days that already exist are updated rather than replaced: a field the
        batch did not mention is left alone, exactly as on the per-day path, so
        re-running an import does not discard what was corrected between runs.

        Args:
            days: One entry per date, at most
                `app.domain.wellness.MAX_BACKFILL_DAYS`.
            actor: Who is writing.
            source: Athlete or agent.
            dry_run: Report exactly the per-day outcomes the real call would
                produce, writing nothing and costing no rate-cap budget.

        Raises:
            ValidationError: For an empty or oversized batch, a repeated date,
                a future date, or any day that breaks a domain rule — always
                naming the date.
            RateLimitedError: When an agent's hourly write cap is spent. One
                batch costs **one** write against it regardless of how many
                days it carries.
        """
        if not days:
            raise ValidationError(
                "a backfill needs at least one day; an empty batch records "
                "nothing and is more likely a caller bug than an intention"
            )
        if len(days) > MAX_BACKFILL_DAYS:
            raise ValidationError(
                f"a backfill carries at most {MAX_BACKFILL_DAYS} days (a year "
                f"in one call), got {len(days)}; split it by year"
            )
        seen: set[dt.date] = set()
        for entry in days:
            if entry.local_date in seen:
                raise ValidationError(
                    f"{entry.local_date.isoformat()} appears twice in this "
                    "batch; one day is one row, so merge the two entries "
                    "rather than letting the later one silently win"
                )
            seen.add(entry.local_date)
            self._check_fields(entry.updates, at=entry.local_date)

        results = await self._write_many(
            days, actor=actor, source=source, dry_run=dry_run
        )
        if results is not None:
            return results
        # Lost a create race against a concurrent write — the same one
        # :meth:`_write_one` handles, and handled the same way. The retry
        # re-reads every date in the batch, so days the winner created are
        # updates this time round.
        retried = await self._write_many(
            days, actor=actor, source=source, dry_run=dry_run
        )
        if retried is None:
            raise ConflictError(
                "these days were written concurrently while this batch was in "
                "flight; nothing was stored, so re-read the range and send it "
                "again"
            )
        return retried

    async def _write_many(
        self,
        days: Sequence[DayInput],
        *,
        actor: Actor,
        source: WellnessSource,
        dry_run: bool,
    ) -> Sequence[DayResult] | None:
        """One attempt at a batch; ``None`` when it lost a create race.

        A batch is one transaction, so a lost race costs nothing and leaves
        nothing behind — the failed flush rolls the whole attempt back, which
        is exactly the guarantee `record_many` already promises for an invalid
        day. Re-reading from scratch is therefore the whole of the retry.

        **This path touches no prompt at all** — it raises none for the days it
        writes, and it answers none that already stand. Raising them would turn
        a migration of sixty days of history into sixty mornings the athlete
        was asked about and did not answer, which is precisely the opposite of
        what the file records; answering them would let an import that happens
        to include today silently close a question the athlete has not looked
        at yet. A backfill is history arriving, not a morning being reported —
        see :meth:`_answer_standing_prompt` for the path that is.
        """
        existing = await self._repository.get_many([entry.local_date for entry in days])
        today = self.local_today()
        results = [
            self._resolve(
                entry.local_date,
                entry.updates,
                existing=existing.get(entry.local_date),
                source=source,
                today=today,
            )
            for entry in days
        ]
        if dry_run:
            return results

        await check_write_cap(self._session, actor)
        rows = []
        for entry, result in zip(days, results, strict=True):
            row = existing.get(entry.local_date)
            if result.day is None:
                # A batch entry that clears a day's last value retracts it, the
                # same as the per-day path. `_resolve` has already refused the
                # case where there was no day to retract.
                assert row is not None  # noqa: S101
                await self._repository.delete(row)
                continue
            row = row or WellnessDayRow(local_date=entry.local_date)
            _apply(row, result.day)
            rows.append(row)
        try:
            await self._repository.add_all(rows)
        except ConflictError:
            if all(entry.local_date in existing for entry in days):
                # Every date already had a row, so a constraint refusal is not
                # the create race and the caller has to see it.
                raise
            return None
        # **One** audit row for the batch, carrying every day's diff. One row
        # per day would be complete in exactly the same way and would charge
        # the agent's hourly cap once per day, which is the thing this write
        # exists to avoid. `entity_id` is null because the write is not about
        # one row — the column has been nullable for this since WP-1.
        await self._audit.record(
            actor=actor,
            action="wellness.backfilled",
            entity_type=ENTITY_TYPE,
            entity_id=None,
            payload={
                "day_count": len(results),
                "days": [
                    {
                        "local_date": result.local_date.isoformat(),
                        "outcome": result.outcome.value,
                        "changed": dict(result.changed),
                    }
                    for result in results
                ],
            },
        )
        await commit(self._session)
        return results

    # --- the daily prompt -----------------------------------------------------

    async def prompt(
        self, local_date: dt.date | None = None
    ) -> WellnessPromptRow | None:
        """The prompt standing for ``local_date`` (today by default), or None.

        ``None`` is an answer and not a gap: nobody has been asked yet. Reading
        this **never raises one** — a question the athlete only gets because
        they happened to open the app is exactly the intermittent capture the
        prompt exists to replace, and it would also make "was the athlete
        asked?" depend on who read the page.
        """
        return await self._prompts.get(local_date or self.local_today())

    async def raise_prompt(
        self,
        local_date: dt.date,
        *,
        actor: Actor,
        now: dt.datetime | None = None,
    ) -> WellnessPromptRow:
        """Raise the day's prompt if it has not been raised before.

        Idempotent, and idempotent the honest way: the caller is an hourly
        sweep, so this is called over the same date many times a day. An
        existing prompt is returned untouched **whatever its status** — an
        answered day is not asked again, and an expired one is not resurrected.
        Nor is the deadline moved: the athlete's window runs from when the
        question was first put to them.

        The unique constraint on ``local_date`` is what makes "one prompt per
        day" true; the pre-read here only keeps the ordinary path off the
        constraint. A genuine race loses the insert, re-reads and returns the
        winner's row, so the caller never sees a 409 about a database index.
        """
        existing = await self._prompts.get(local_date)
        if existing is not None:
            return existing
        moment = now or dt.datetime.now(dt.UTC)
        hours = get_settings().wellness.prompt_expiry_hours
        row = WellnessPromptRow(
            local_date=local_date,
            status=WellnessPromptStatus.PENDING,
            expires_at=moment + dt.timedelta(hours=hours),
        )
        try:
            row = await self._prompts.add(row)
        except ConflictError:
            # Lost the raise race — two sweeps, or a sweep and a boot. The
            # failed flush has rolled this session back, so the winner's row is
            # what a re-read finds.
            won = await self._prompts.get(local_date)
            if won is None:
                raise
            return won
        await self._audit.record(
            actor=actor,
            action="wellness_prompt.raised",
            entity_type=PROMPT_ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "local_date": row.local_date.isoformat(),
                "expires_at": row.expires_at.isoformat(),
            },
        )
        await commit(self._session)
        return row

    async def raise_due_prompt(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> WellnessPromptRow | None:
        """Raise today's prompt once the athlete's own clock has reached the hour.

        ``None`` before `WELLNESS__PROMPT_HOUR_LOCAL`: the day is asked about
        once, in the evening, rather than at every tick since midnight. The
        clock is the athlete's (`MATCHING__TIMEZONE`), the same one
        :meth:`local_today` and the missed-session sweep read.
        """
        moment = now or dt.datetime.now(dt.UTC)
        zone = parse_timezone(get_settings().matching.timezone)
        local = moment.astimezone(zone)
        if local.hour < get_settings().wellness.prompt_hour_local:
            return None
        return await self.raise_prompt(local.date(), actor=actor, now=moment)

    async def answer_prompt(
        self,
        updates: Mapping[str, Any],
        *,
        actor: Actor,
        source: WellnessSource,
        local_date: dt.date | None = None,
    ) -> tuple[WellnessPromptRow, DayResult]:
        """Answer the standing prompt: write the day, and close the question.

        One transaction, and in this order: the day is validated and written
        first, and the prompt is resolved by :meth:`record` inside the same
        commit. A write that breaks a domain rule therefore leaves the prompt
        **pending** — the athlete was asked, they have not answered yet, and a
        rejected payload must not spend the day's one question.

        Raises:
            NotFoundError: When no prompt was ever raised for the date. The day
                is still writable through :meth:`record`; what does not exist
                is a question to answer.
            ConflictError: When the prompt already expired. The day closed
                unanswered and that is a recorded fact; a late reading goes
                through the ordinary dated write, which marks it as recalled.
            ValidationError: For any rule the resulting day breaks.
        """
        date = local_date or self.local_today()
        standing = await self._prompts.get(date)
        if standing is None:
            raise NotFoundError(
                f"No wellness prompt is standing for {date.isoformat()}; record "
                "the day directly instead"
            )
        if standing.status is WellnessPromptStatus.EXPIRED:
            raise ConflictError(
                f"The wellness prompt for {date.isoformat()} expired at "
                f"{standing.expires_at.isoformat()} and the day closed "
                "unanswered; record the day directly to enter it from memory"
            )
        result = await self.record(date, updates, actor=actor, source=source)
        resolved = await self._prompts.get(date)
        assert resolved is not None  # noqa: S101 — checked above, same session
        return resolved, result

    async def expire_prompts(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> Sequence[WellnessPromptRow]:
        """Close every wellness prompt whose window has run out.

        **The day closes into "not provided", and no follow-up is ever raised.**
        A reminder cascade is what an application built for the compliant
        athlete does; the athlete this exists for is the one who did not answer
        because the week is already going badly, and a second prompt asks them
        to feel worse about it. What the record needs is not another question
        but the fact that this one went unanswered — which is what lets a coach
        tell "the athlete felt fine" from "nobody asked".

        Idempotent: an already-terminal prompt is not a candidate, so a second
        run over the same data changes no status and no ``resolved_at``. The
        deadline is stored on each prompt, so this job agrees with no constant
        of its own, and the batch is taken over what **has** expired, oldest
        deadline first — the starvation lesson `ScoringService.expire_prompts`
        records.

        Returns:
            The prompts expired, oldest deadline first.
        """
        moment = now or dt.datetime.now(dt.UTC)
        due = await self._prompts.expired(
            now=moment, limit=get_settings().wellness.prompt_scan_batch
        )
        expired: list[WellnessPromptRow] = []
        for row in due:
            row.status = WellnessPromptStatus.EXPIRED
            row.resolved_at = moment
            await self._prompts.add(row)
            await self._audit.record(
                actor=actor,
                action="wellness_prompt.expired",
                entity_type=PROMPT_ENTITY_TYPE,
                entity_id=row.id,
                payload={
                    "local_date": row.local_date.isoformat(),
                    "expires_at": row.expires_at.isoformat(),
                },
            )
            expired.append(row)
        if expired:
            await commit(self._session)
        return expired

    async def _answer_standing_prompt(
        self, local_date: dt.date, *, now: dt.datetime | None = None
    ) -> None:
        """Mark the day's prompt answered, if one is standing for that date.

        Called from the **per-day** write and from nowhere else. Filling in a
        day *is* the answer to the question about it, so a prompt left pending
        beside a recorded day would expire into "not provided" while the day
        sits there — silence the coach would read as an athlete who could not
        be bothered. The batch path deliberately does not do this; see
        :meth:`_write_many`.

        A terminal prompt is left alone: a day entered after its question
        closed is a late entry, not an answer arriving on time, and the record
        should keep saying that nobody answered when they were asked.
        """
        standing = await self._prompts.get(local_date)
        if standing is None or standing.status in TERMINAL_PROMPT_STATUSES:
            return
        standing.status = WellnessPromptStatus.ANSWERED
        standing.resolved_at = now or dt.datetime.now(dt.UTC)
        await self._prompts.add(standing)

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _check_fields(updates: Mapping[str, Any], *, at: dt.date | None = None) -> None:
        """Refuse a payload naming a field the day does not have.

        Named rather than dropped, and with the vocabulary enumerated: an error
        that does not say what *is* legal costs the agent a round trip, which
        is the #19 lesson.

        Raises:
            ValidationError: When any key is unknown.
        """
        unknown = sorted(set(updates) - set(WRITABLE_FIELDS))
        if not unknown:
            return
        where = "" if at is None else f"{at.isoformat()}: "
        raise ValidationError(
            f"{where}unknown wellness field(s): {', '.join(unknown)}. A day "
            f"carries {', '.join(WRITABLE_FIELDS)}."
        )

    @staticmethod
    def _resolve(
        local_date: dt.date,
        updates: Mapping[str, Any],
        *,
        existing: WellnessDayRow | None,
        source: WellnessSource,
        today: dt.date,
    ) -> DayResult:
        """Fold ``updates`` onto the stored day and validate the result.

        Validation goes through the **domain value object** rather than field
        by field, so the API, the MCP tool and a dry run cannot drift apart on
        what a legal day is — and so the message the athlete sees is the same
        sentence on every surface.

        Raises:
            ValidationError: Naming the date and the rule the day breaks.
        """
        before = _values(existing)
        candidate = {**before, **dict(updates)}
        # The two collections have an **empty value rather than an absent
        # one**, the way `sex` and `capabilities` do on the profile: "I have
        # no confounders today" is a report, and a caller clearing the tags
        # with `null` means that rather than meaning nothing at all.
        for name, empty in _ABSENT.items():
            if candidate[name] is None:
                candidate[name] = empty
        # Clearing a reading clears what described it: an `hrv_context` with no
        # `hrv_ms` is a claim about nothing, and the domain refuses it.
        if candidate["hrv_ms"] is None:
            for name in HRV_FIELDS:
                candidate[name] = None

        day: WellnessDay | None = None
        if _has_content(candidate):
            try:
                with domain_rules():
                    day = WellnessDay(
                        local_date=local_date,
                        **candidate,
                        # Nothing writes `device_measured` yet: the value exists
                        # so Increment 2's HealthKit path is a new caller rather
                        # than a migration of stored rows.
                        provenance=WellnessProvenance.ATHLETE_REPORTED,
                        source=source,
                    )
                    day.check_not_future(today)
            except ValidationError as exc:
                raise ValidationError(
                    f"{local_date.isoformat()}: {exc.detail}"
                ) from exc
        elif existing is None:
            raise ValidationError(
                f"{local_date.isoformat()}: a wellness day must record "
                "something — give at least one reading, rating, confounder or "
                "note. There is no day here to retract."
            )

        after = _values_of(day) if day is not None else _values(None)
        if day is None:
            outcome = DayOutcome.RETRACTED
        elif existing is not None:
            outcome = DayOutcome.UPDATED
        else:
            outcome = DayOutcome.CREATED
        return DayResult(
            local_date=local_date,
            outcome=outcome,
            day=day,
            changed={
                name: {"from": _jsonable(before[name]), "to": _jsonable(after[name])}
                for name in WRITABLE_FIELDS
                if before[name] != after[name]
            },
        )


#: What a field holds on a day that does not exist yet. Absence is ``None`` for
#: every scalar; the two collections are empty rather than absent, because
#: "reported no confounders" and "not asked" are the same statement for a tag
#: list and are not for a measurement.
_ABSENT: Mapping[str, Any] = {"soreness_by_region": {}, "confounders": ()}


def _has_content(values: Mapping[str, Any]) -> bool:
    """Whether a candidate day carries anything at all.

    Checked before the domain value object is built, because the domain refuses
    an empty day outright and "the athlete cleared their last reading" needs a
    different answer from "the caller sent an empty write".
    """
    return any(values[name] not in (None, (), [], {}) for name in WRITABLE_FIELDS)


def _values(row: WellnessDayRow | None) -> dict[str, Any]:
    """The writable fields of a stored day, or of the empty day before one."""
    if row is None:
        return {name: _ABSENT.get(name) for name in WRITABLE_FIELDS}
    return _values_of(row.to_domain())


def _values_of(day: WellnessDay) -> dict[str, Any]:
    """The writable fields of a domain day, keyed by field name."""
    return {name: getattr(day, name) for name in WRITABLE_FIELDS}


def _apply(row: WellnessDayRow, day: WellnessDay) -> None:
    """Write a validated domain day onto its row.

    The enum-keyed and enum-valued collections become plain JSON here: what is
    stored is the member's **value**, the same spelling the API, the OpenAPI
    schema and every payload use, so hand-written SQL does not have to know
    which side of the ORM it is on.
    """
    for name in WRITABLE_FIELDS:
        if name in ("soreness_by_region", "confounders"):
            continue
        setattr(row, name, getattr(day, name))
    row.soreness_by_region = {
        region.value: rating for region, rating in day.soreness_by_region.items()
    }
    row.confounders = [member.value for member in day.confounders]
    row.provenance = day.provenance
    row.source = day.source


def _mondays(start: dt.date, end: dt.date) -> list[dt.date]:
    """Every week the half-open range ``[start, end)`` touches, oldest first."""
    monday = week_start(start)
    weeks: list[dt.date] = []
    last = end - dt.timedelta(days=1)
    while monday <= last:
        weeks.append(monday)
        monday += dt.timedelta(days=7)
    return weeks


def _fold_week(
    *,
    monday: dt.date,
    days: Sequence[WellnessDay],
    recalled: Container[dt.date],
    start: dt.date,
    end: dt.date,
) -> WellnessWeek:
    """Fold one week's days into its means, clipped to the requested range."""
    sunday = monday + dt.timedelta(days=6)
    actionable = [day for day in days if day.standing.actionable]
    return WellnessWeek(
        start=max(monday, start),
        end=min(sunday, end - dt.timedelta(days=1)),
        days_recorded=len(days),
        days_invalidated=len(days) - len(actionable),
        days_recalled=sum(1 for day in days if day.local_date in recalled),
        metrics=_means(objective=actionable, subjective=days),
    )


def _means(
    *, objective: Sequence[WellnessDay], subjective: Sequence[WellnessDay]
) -> tuple[MetricMean, ...]:
    """One mean per metric that anybody reported, with its ``n``.

    Two populations, not one: the objective markers are folded over the days a
    confounder did **not** void, and the subjective ratings over every day the
    athlete answered. A hot room makes a resting heart rate say nothing about
    readiness; it does not make the athlete's own report of feeling tired
    untrue.
    """
    means: list[MetricMean] = []
    for name in OBJECTIVE_FIELDS:
        if name == "hrv_ms":
            continue
        values = [
            getattr(day, name) for day in objective if getattr(day, name) is not None
        ]
        if values:
            means.append(
                MetricMean(metric=name, mean=sum(values) / len(values), n=len(values))
            )
    # HRV, one series per (statistic, context) pair — see the method docstring.
    hrv: dict[tuple[str, str], list[float]] = {}
    for day in objective:
        if day.hrv_ms is None or day.hrv_metric is None or day.hrv_context is None:
            continue
        hrv.setdefault((day.hrv_metric.value, day.hrv_context.value), []).append(
            day.hrv_ms
        )
    for (metric, context), values in sorted(hrv.items()):
        means.append(
            MetricMean(
                metric=f"hrv_ms[{metric},{context}]",
                mean=sum(values) / len(values),
                n=len(values),
            )
        )
    for name in SUBJECTIVE_FIELDS:
        values = [
            getattr(day, name) for day in subjective if getattr(day, name) is not None
        ]
        if values:
            means.append(
                MetricMean(metric=name, mean=sum(values) / len(values), n=len(values))
            )
    return tuple(means)


def _jsonable(value: Any) -> Any:
    """Make a day's value storable in the audit payload column."""
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {_jsonable(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def parse_confounders(values: Iterable[str]) -> tuple[Confounder, ...]:
    """Parse confounder tags, refusing an unknown one **with the vocabulary**.

    Lives beside the service rather than in the domain because the domain
    signals a broken rule with `ValueError` and this needs to name the whole
    legal list — an error that does not enumerate the valid values costs the
    agent a round trip it should never have paid for (the #19 lesson).

    Raises:
        ValidationError: Naming the offending tag and every legal one.
    """
    parsed = []
    for value in values:
        try:
            parsed.append(Confounder(value))
        except ValueError as exc:
            raise ValidationError(
                f"{value!r} is not a confounder. The vocabulary is: "
                f"{', '.join(member.value for member in Confounder)}."
            ) from exc
    return tuple(parsed)


def parse_soreness_by_region(mapping: Mapping[str, Any]) -> dict[BodyRegion, int]:
    """Parse a per-region soreness map, refusing an unknown region by name.

    Raises:
        ValidationError: Naming the offending region and every legal one.
    """
    parsed: dict[BodyRegion, int] = {}
    for region, rating in mapping.items():
        try:
            key = BodyRegion(region)
        except ValueError as exc:
            raise ValidationError(
                f"{region!r} is not a body region. The vocabulary is: "
                f"{', '.join(member.value for member in BodyRegion)}."
            ) from exc
        if not isinstance(rating, int) or isinstance(rating, bool):
            raise ValidationError(
                f"soreness_by_region[{region}] must be a whole number on the "
                f"soreness scale, got {rating!r}"
            )
        parsed[key] = rating
    return parsed


# --- the scheduled sweep ---------------------------------------------------------


async def run_wellness_prompt_sweep() -> None:
    """Raise the day's prompt when its hour comes, and close what has run out.

    One job for both halves: they read the same clock and the same table, and
    two jobs would be two places the prompt hour has to be agreed on.

    Never raises — a scheduler job that raises stops running, and a prompt
    surface that silently stopped raising would look exactly like an athlete
    who stopped being asked.
    """
    try:
        async with session_scope() as session:
            service = WellnessService.from_session(session)
            raised = await service.raise_due_prompt(actor=Actor.system())
            expired = await service.expire_prompts(actor=Actor.system())
        logger.info(
            "wellness_prompt_sweep_ran",
            raised=None if raised is None else raised.local_date.isoformat(),
            expired=len(expired),
        )
    except Exception:  # noqa: BLE001 — a scheduler job that raises stops running
        logger.exception("wellness_prompt_sweep_failed")


def register_wellness_prompt_job(scheduler: BaseScheduler) -> None:
    """Register the daily prompt's raise-and-expire sweep on the scheduler.

    Registered by the increment that needs it, like the inbox, missed and
    prompt-expiry sweeps, rather than in `app.core.scheduler`, which owns no
    jobs of its own. ``coalesce`` and ``max_instances=1`` because the sweep is
    idempotent and two of them over one day would race for nothing — and
    because "one prompt a day" must not depend on that race being won.
    """
    scheduler.add_job(
        run_wellness_prompt_sweep,
        "interval",
        seconds=get_settings().wellness.prompt_scan_interval_seconds,
        id=PROMPT_SWEEP_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
