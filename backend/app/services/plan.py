"""The plan as the calendar reads it: one week, projected for rendering.

A read-only use-case, and the only one in this layer that exists for a
*screen*. Everything it returns is derivable from what
`app.services.planned_sessions` already serves, but deriving it per card costs
the calendar a request per session and a step-tree walk in the browser, so the
week is assembled once, here, where the domain's own helpers are.

Two shape decisions the adapters inherit (D55):

* the week is **seven days**, always, including the empty ones — a calendar
  renders a grid, and a projection that omitted Thursday would make every
  client rebuild it;
* each card carries what a card shows and nothing more. The session's full
  detail — the step tree, the criteria, the pins, the intent history — stays
  behind `GET /api/v1/planned-sessions/{id}`, which is what the day sheet
  opens.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.athlete import Discipline
from app.domain.plan import WEEK_DAYS, week_dates, week_start
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus
from app.domain.workout import workout_body_from_json
from app.persistence.planned_sessions import (
    PlannedSessionIntentRow,
    PlannedSessionRepository,
    PlannedSessionRow,
)
from app.persistence.workouts import WorkoutRepository
from app.services.workouts import WorkoutSummary

#: Most sessions one week's projection will read. Not pagination — a week is
#: rendered whole or not at all — but a bound, so a corrupted date column
#: cannot make one request load the entire table. Two hundred is roughly
#: thirty sessions a day; a plan that dense is not a plan.
MAX_WEEK_SESSIONS = 200


@dataclass(frozen=True, slots=True)
class WeekSession:
    """One planned session, as a calendar card needs it."""

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    purpose: Purpose
    status: SessionStatus
    #: The library workout this was planned from, if it still exists. ``None``
    #: for an inline prescription (and for one whose library entry has since
    #: been deleted): the card then labels itself from the purpose, which is
    #: the one thing every session has.
    title: str | None
    workout_id: uuid.UUID | None
    #: Prescribed seconds. ``None`` for a strength session, and for an
    #: endurance one with a distance-based step — there is no duration to show
    #: rather than a zero.
    planned_duration_s: int | None
    #: Prescribed working sets, for a strength session; ``None`` otherwise.
    total_sets: int | None
    #: Flattened steps (endurance) or prescription lines (strength).
    step_count: int
    #: The one-line intent, the athlete's own words.
    intent_text: str | None
    #: Which intent version the card is showing.
    intent_version: int


@dataclass(frozen=True, slots=True)
class WeekDay:
    """One day of the week, with the sessions planned for it."""

    date: dt.date
    sessions: tuple[WeekSession, ...]


@dataclass(frozen=True, slots=True)
class PlanWeek:
    """Seven consecutive days of the plan."""

    start: dt.date
    #: The last day in the window, inclusive — ``start + 6``.
    end: dt.date
    days: tuple[WeekDay, ...]
    session_count: int
    #: Prescribed seconds across the week, counting only the sessions that
    #: have a duration to count.
    planned_duration_s: int


class PlanService:
    """Read-projections over the plan. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: PlannedSessionRepository,
        workouts: WorkoutRepository,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._workouts = workouts

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session, PlannedSessionRepository(session), WorkoutRepository(session)
        )

    async def week(self, start: dt.date | None = None) -> PlanWeek:
        """Project the seven days beginning at ``start``.

        ``start`` is taken literally — a Wednesday start gives the seven days
        from Wednesday — so a client can page the calendar by a day if it
        wants to. Omitted, it defaults to the Monday of the current week
        (D55).
        """
        first = start if start is not None else week_start(_today())
        dates = week_dates(first)
        rows, _total = await self._sessions.list(
            start=first, end=dates[-1], limit=MAX_WEEK_SESSIONS
        )
        titles = await self._workouts.names(
            [
                workout_id
                for row in rows
                if (workout_id := row.current_intent.workout_id) is not None
            ]
        )
        cards = [_card(row, titles) for row in rows]
        return PlanWeek(
            start=first,
            end=dates[-1],
            days=tuple(
                WeekDay(
                    date=day,
                    sessions=tuple(card for card in cards if card.date == day),
                )
                for day in dates
            ),
            session_count=len(cards),
            planned_duration_s=sum(card.planned_duration_s or 0 for card in cards),
        )


def _today() -> dt.date:
    """The current date, in UTC.

    The athlete's own timezone is not modelled until WP-4 puts one on each
    recorded session, so UTC is the calendar the whole application already
    agrees on (D55). Isolated here so that work package has one line to
    change.
    """
    return dt.datetime.now(dt.UTC).date()


def _card(row: PlannedSessionRow, titles: dict[uuid.UUID, str]) -> WeekSession:
    """Project one stored session onto its calendar card."""
    intent = row.current_intent
    summary = _summary(intent)
    return WeekSession(
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        purpose=intent.purpose,
        status=row.status,
        title=titles.get(intent.workout_id) if intent.workout_id else None,
        workout_id=intent.workout_id,
        planned_duration_s=summary.total_duration_s,
        total_sets=summary.total_sets,
        step_count=summary.step_count,
        intent_text=intent.intent_text,
        intent_version=intent.version,
    )


def _summary(intent: PlannedSessionIntentRow) -> WorkoutSummary:
    """Derive the card's numbers from the prescription frozen in ``intent``.

    Derived on read rather than stored, like every other summary of a
    structure document (`app.services.workouts.WorkoutSummary`): the intent
    version *is* the source, and a cached duration beside it is a second
    answer waiting to disagree.

    Raises:
        ValueError: When the stored prescription no longer parses — loud on
            purpose, exactly as when one is read back individually.
    """
    return WorkoutSummary(workout_body_from_json(intent.structure))


#: Re-exported so an adapter can state the window it renders without reaching
#: into the domain for the constant.
__all__ = [
    "MAX_WEEK_SESSIONS",
    "WEEK_DAYS",
    "PlanService",
    "PlanWeek",
    "WeekDay",
    "WeekSession",
]
