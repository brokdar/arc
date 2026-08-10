"""Date-range training summaries — the read behind `search_history` (WP-8.1).

The coaching agent needs to be able to ask "what have the last eight weeks
looked like" without pulling eight weeks of sessions, metrics and scores
across the wire and adding them up itself. An agent that has to fetch the raw
rows to answer a shape question will fetch them every time it wonders, and an
agent that adds them up itself is a second implementation of what a week's
load *is* — one nobody here can fix.

So the folding happens once, here, and the tool returns the fold.

**Per week, because a training week is the unit this application already
thinks in** (`app.domain.plan`: Monday to Sunday, ISO-8601). Per day would be
noise at this range and per month would cross the boundary every plan is
written against.

**Summaries, not streams.** Counts, durations, load and verdict tallies —
enough to see the shape of a block and decide what to look at in detail, and
deliberately not enough to reconstruct a session. `get_session_detail` is
where detail lives, one session at a time.

**It refuses rather than truncates.** A summary quietly missing a third of
its sessions is worse than no summary, because nothing in it says so and the
agent will reason over the gap. See :data:`MAX_HISTORY_SESSIONS`.
"""

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.domain.activity import SessionDiscipline
from app.domain.plan import week_start
from app.domain.scoring import Verdict
from app.persistence.activity import SessionRow, session_duration_s
from app.services.activity import SessionService
from app.services.metrics import SessionMetricsService, summarise
from app.services.scoring import ScoringService

#: Longest range one call may summarise, in days. Fifty-three weeks: a year of
#: training is the longest span a question about "the shape of things" is
#: usually asking, and a bound that admits one is a bound nobody argues with.
MAX_HISTORY_DAYS = 371

#: Most sessions one summary will fold. Reached only by a range far outside
#: what a single athlete records, so hitting it means the range is wrong, not
#: that the athlete is prolific.
MAX_HISTORY_SESSIONS = 800

#: The value reported for "no verdict was declared". Not a `Verdict` member,
#: because it is the absence of one: the athlete has not answered yet, and
#: folding that into `as_intended` would invent agreement.
UNDECLARED = "undeclared"


@dataclass(frozen=True, slots=True)
class DisciplineTotals:
    """One discipline's share of a week.

    ``load`` is the sum over the sessions that *have* a training load, and
    ``load_sessions_uncounted`` says how many did not — a session with no
    power and no heart rate cannot be priced, and a total that silently
    omitted it would read as a lighter week rather than a less-measured one.
    """

    discipline: SessionDiscipline
    session_count: int
    duration_s: float
    load: float | None
    load_sessions_counted: int
    load_sessions_uncounted: int


@dataclass(frozen=True, slots=True)
class HistoryWeek:
    """One week of recorded training, bucketed Monday to Sunday.

    ``start`` and ``end`` are the week **intersected with the requested
    range**, not the calendar week: a summary from a Wednesday reports its
    first week as Wednesday-to-Sunday, because Monday and Tuesday were never
    looked at and the totals below do not include them. A partial week is
    therefore visibly partial — its bounds are not a Monday and a Sunday, and
    a reader comparing week loads can see which ones are not comparable.
    Stamping the full calendar week on it would have claimed five days of
    training were a whole week of it.
    """

    start: dt.date
    end: dt.date
    session_count: int
    duration_s: float
    load: float | None
    load_sessions_counted: int
    load_sessions_uncounted: int
    by_discipline: tuple[DisciplineTotals, ...]
    #: Declared verdicts in this week, by value, plus `undeclared`. Only the
    #: **declared** verdict is counted: the suggestion is the scoring engine's
    #: opinion and the declaration is the athlete's, and a history of what the
    #: engine guessed is a history of this application, not of the training.
    verdicts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HistorySummary:
    """What :meth:`HistoryService.summarise` answers with."""

    start: dt.date
    end: dt.date
    session_count: int
    duration_s: float
    load: float | None
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: Every week the range touches, oldest first, **including empty ones** —
    #: a fortnight off is a fact about a training block, and a summary that
    #: skipped the blank weeks would show it as two adjacent hard ones.
    weeks: tuple[HistoryWeek, ...]
    verdicts: Mapping[str, int] = field(default_factory=dict)


class HistoryService:
    """Date-range summaries folded from sessions, metrics and declarations."""

    def __init__(
        self,
        sessions: SessionService,
        metrics: SessionMetricsService,
        scoring: ScoringService,
    ) -> None:
        self._sessions = sessions
        self._metrics = metrics
        self._scoring = scoring

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service to one session."""
        return cls(
            SessionService.from_session(session),
            SessionMetricsService.from_session(session),
            ScoringService.from_session(session),
        )

    async def summarise(self, *, start: dt.date, end: dt.date) -> HistorySummary:
        """Fold the recorded sessions in a date range into weekly totals.

        The range is over the athlete-**local** date of each session, which is
        the day the athlete would say they rode on — the same day the plan
        places a session on, so a week's plan and a week's history are about
        the same seven days.

        Args:
            start: First day of the range, inclusive.
            end: Last day of the range, inclusive.

        Returns:
            Totals for the range and for each week it touches, oldest first.

        Raises:
            ValidationError: When the range is inverted, longer than
                :data:`MAX_HISTORY_DAYS`, or holds more than
                :data:`MAX_HISTORY_SESSIONS` sessions.
        """
        _check_range(start, end)
        rows, total = await self._sessions.list(
            start=start, end=end, offset=0, limit=MAX_HISTORY_SESSIONS
        )
        if total > MAX_HISTORY_SESSIONS:
            raise ValidationError(
                f"That range holds {total} sessions, more than the {MAX_HISTORY_SESSIONS} "
                "one summary will fold. Ask for a shorter range — a summary "
                "missing part of its range would not say so."
            )

        loads = await self._loads(rows)
        verdicts = await self._verdicts(rows)
        by_week: dict[dt.date, list[SessionRow]] = {}
        for row in rows:
            by_week.setdefault(week_start(row.local_date), []).append(row)
        weeks = tuple(
            _fold_week(
                monday, by_week.get(monday, []), loads, verdicts, window=(start, end)
            )
            for monday in _mondays(start, end)
        )
        totals = _totals(rows, loads)
        return HistorySummary(
            start=start,
            end=end,
            session_count=len(rows),
            duration_s=totals.duration_s,
            load=totals.load,
            load_sessions_counted=totals.load_sessions_counted,
            load_sessions_uncounted=totals.load_sessions_uncounted,
            weeks=weeks,
            verdicts=_tally(rows, verdicts),
        )

    async def _loads(self, rows: Sequence[SessionRow]) -> dict[uuid.UUID, float | None]:
        """The training load of each session, or None where it has none."""
        current = await self._metrics.current_for_sessions(row.id for row in rows)
        return {
            row.id: (
                summarise(current[row.id]).training_load if row.id in current else None
            )
            for row in rows
        }

    async def _verdicts(self, rows: Sequence[SessionRow]) -> dict[uuid.UUID, str]:
        """The declared verdict of each session, or `undeclared`."""
        declared = await self._scoring.declarations_for(row.id for row in rows)
        return {
            row.id: (
                declared[row.id].declared_verdict.value
                if row.id in declared
                else UNDECLARED
            )
            for row in rows
        }


@dataclass(frozen=True, slots=True)
class _Totals:
    """The four numbers every level of the fold reports."""

    duration_s: float
    load: float | None
    load_sessions_counted: int
    load_sessions_uncounted: int


def _totals(
    rows: Sequence[SessionRow], loads: Mapping[uuid.UUID, float | None]
) -> _Totals:
    """Fold one bucket of sessions.

    ``load`` is ``None`` rather than ``0.0`` when nothing in the bucket could
    be priced: zero is a real training load and means an easy week, and an
    unmeasured week is not an easy one.
    """
    priced = [loads[row.id] for row in rows if loads.get(row.id) is not None]
    return _Totals(
        duration_s=sum(session_duration_s(row) for row in rows),
        load=sum(value for value in priced if value is not None) if priced else None,
        load_sessions_counted=len(priced),
        load_sessions_uncounted=len(rows) - len(priced),
    )


def _fold_week(
    monday: dt.date,
    rows: Sequence[SessionRow],
    loads: Mapping[uuid.UUID, float | None],
    verdicts: Mapping[uuid.UUID, str],
    *,
    window: tuple[dt.date, dt.date],
) -> HistoryWeek:
    """One week's totals, overall and per discipline.

    Args:
        monday: The Monday the bucket is keyed by.
        rows: The sessions that fell in it.
        loads: Training load per session id.
        verdicts: Declared verdict per session id.
        window: The requested range. The reported bounds are clipped to it,
            because the totals are: the query never saw a day outside it, so
            a week stamped Monday-to-Sunday would be claiming days nothing was
            counted over (see :class:`HistoryWeek`).
    """
    totals = _totals(rows, loads)
    by_discipline: list[DisciplineTotals] = []
    for discipline in SessionDiscipline:
        share = [row for row in rows if row.discipline is discipline]
        if not share:
            continue
        part = _totals(share, loads)
        by_discipline.append(
            DisciplineTotals(
                discipline=discipline,
                session_count=len(share),
                duration_s=part.duration_s,
                load=part.load,
                load_sessions_counted=part.load_sessions_counted,
                load_sessions_uncounted=part.load_sessions_uncounted,
            )
        )
    return HistoryWeek(
        start=max(monday, window[0]),
        end=min(monday + dt.timedelta(days=6), window[1]),
        session_count=len(rows),
        duration_s=totals.duration_s,
        load=totals.load,
        load_sessions_counted=totals.load_sessions_counted,
        load_sessions_uncounted=totals.load_sessions_uncounted,
        by_discipline=tuple(by_discipline),
        verdicts=_tally(rows, verdicts),
    )


def _tally(
    rows: Sequence[SessionRow], verdicts: Mapping[uuid.UUID, str]
) -> dict[str, int]:
    """Count the declared verdicts in one bucket, omitting the zeroes."""
    counts = Counter(verdicts[row.id] for row in rows if row.id in verdicts)
    order = [member.value for member in Verdict] + [UNDECLARED]
    return {name: counts[name] for name in order if counts[name]}


def _mondays(start: dt.date, end: dt.date) -> list[dt.date]:
    """Every week the range touches, oldest first."""
    monday = week_start(start)
    weeks: list[dt.date] = []
    while monday <= end:
        weeks.append(monday)
        monday += dt.timedelta(days=7)
    return weeks


def _check_range(start: dt.date, end: dt.date) -> None:
    """Refuse a range that is inverted or longer than the bound.

    Raises:
        ValidationError: With the bound named, so the agent's next call can
            be a legal one.
    """
    if end < start:
        raise ValidationError(
            f"start ({start.isoformat()}) must not be after end ({end.isoformat()})"
        )
    span = (end - start).days + 1
    if span > MAX_HISTORY_DAYS:
        raise ValidationError(
            f"A summary covers at most {MAX_HISTORY_DAYS} days; that range is "
            f"{span}. Ask for a shorter one, or several."
        )
