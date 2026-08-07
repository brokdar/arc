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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.plan import WEEK_DAYS, week_dates, week_start
from app.domain.prediction import (
    PinnedAnchor,
    predict_endurance_load,
    predict_strength_volume,
)
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus
from app.domain.strength import StrengthWorkout
from app.domain.workout import WorkoutBody, workout_body_from_json
from app.persistence.planned_sessions import (
    PlannedSessionRepository,
    PlannedSessionRow,
)
from app.persistence.workouts import WorkoutRepository
from app.services.anchors import AnchorService, parse_pins, resolve_pins
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
    #: TSS-equivalent this prescription is expected to cost, predicted from
    #: the frozen intent and the anchor versions it pinned. ``None`` whenever
    #: there is nothing honest to say — a strength session, a distance-based
    #: ride, a ride with no power target, an unpinned FTP.
    predicted_load: float | None
    #: Planned NP over the pinned FTP. ``None`` alongside `predicted_load`.
    predicted_intensity_factor: float | None
    #: Σ ``sets × reps × kg`` for a strength session, when its loads are in
    #: kilograms. Kilograms, **not** a load: never add this to
    #: `predicted_load` (spec v2 §5.4).
    predicted_volume_load_kg: float | None


@dataclass(frozen=True, slots=True)
class WeekDay:
    """One day of the week, with the sessions planned for it."""

    date: dt.date
    sessions: tuple[WeekSession, ...]


@dataclass(frozen=True, slots=True)
class PlanWeekDiscipline:
    """One week's totals for one discipline.

    The two axes stay in their own columns: `planned_load` is TSS and
    `total_sets` counts strength sets, and there is deliberately no field that
    could hold their sum.
    """

    discipline: Discipline
    session_count: int
    planned_duration_s: int
    #: TSS across this discipline's predictable sessions; ``None`` when none
    #: of them is.
    planned_load: float | None
    #: Prescribed working sets; ``None`` for a discipline that has none.
    total_sets: int | None


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
    #: TSS across the sessions that could be predicted. ``None`` — never 0 —
    #: when none of them could: an unpredictable week has no load, and a zero
    #: would read as a rest week.
    planned_load: float | None
    #: How many sessions contributed to `planned_load`, and how many could
    #: not. Never render the total without them: a week of six sessions where
    #: only two are predictable must not read as a light week.
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: One row per discipline that has a session this week, in vocabulary
    #: order.
    by_discipline: tuple[PlanWeekDiscipline, ...]


class PlanService:
    """Read-projections over the plan. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: PlannedSessionRepository,
        workouts: WorkoutRepository,
        anchors: AnchorService,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._workouts = workouts
        self._anchors = anchors

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            PlannedSessionRepository(session),
            WorkoutRepository(session),
            AnchorService.from_session(session),
        )

    async def week(self, start: dt.date | None = None) -> PlanWeek:
        """Project the seven days beginning at ``start``.

        ``start`` is taken literally — a Wednesday start gives the seven days
        from Wednesday — so a client can page the calendar by a day if it
        wants to. Omitted, it defaults to the Monday of the current week
        (D55).

        Predicted load is computed here, on read, from each intent's frozen
        prescription and the anchor versions it pinned — never stored, exactly
        like the durations beside it. The pins for the whole week are loaded
        in **one** query, so a busy week costs the same round-trips as an
        empty one.
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
        pins = {
            row.id: parse_pins(row.current_intent.pinned_anchor_versions)
            for row in rows
        }
        versions = await self._anchors.by_ids(
            version_id
            for session_pins in pins.values()
            for version_id in session_pins.values()
        )
        cards = [
            _card(row, titles, resolve_pins(pins[row.id], versions)) for row in rows
        ]
        loads = [
            card.predicted_load for card in cards if card.predicted_load is not None
        ]
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
            planned_load=sum(loads) if loads else None,
            load_sessions_counted=len(loads),
            load_sessions_uncounted=len(cards) - len(loads),
            by_discipline=_by_discipline(cards),
        )


def _today() -> dt.date:
    """The current date, in UTC.

    The athlete's own timezone is not modelled until WP-4 puts one on each
    recorded session, so UTC is the calendar the whole application already
    agrees on (D55). Isolated here so that work package has one line to
    change.
    """
    return dt.datetime.now(dt.UTC).date()


def _card(
    row: PlannedSessionRow,
    titles: dict[uuid.UUID, str],
    anchors: Mapping[AnchorType, PinnedAnchor],
) -> WeekSession:
    """Project one stored session onto its calendar card.

    Raises:
        ValueError: When the stored prescription no longer parses — loud on
            purpose, exactly as when one is read back individually.
    """
    intent = row.current_intent
    body = workout_body_from_json(intent.structure)
    summary = WorkoutSummary(body)
    load, factor, volume = _predict(body, anchors)
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
        predicted_load=load,
        predicted_intensity_factor=factor,
        predicted_volume_load_kg=volume,
    )


def _predict(
    body: WorkoutBody, anchors: Mapping[AnchorType, PinnedAnchor]
) -> tuple[float | None, float | None, float | None]:
    """Return ``(load, intensity_factor, volume_load_kg)`` for one prescription.

    Exactly one of the two axes is ever populated: a strength prescription has
    kilograms and no TSS, an endurance one has TSS and no kilograms. The
    split is the point — see `app.domain.prediction`.
    """
    if isinstance(body, StrengthWorkout):
        return None, None, predict_strength_volume(body).volume_load_kg
    predicted = predict_endurance_load(body, anchors)
    if predicted is None:
        return None, None, None
    return predicted.load, predicted.intensity_factor, None


def _by_discipline(cards: Sequence[WeekSession]) -> tuple[PlanWeekDiscipline, ...]:
    """Total the week per discipline, skipping disciplines with no session.

    Every total here is the same fold as its flat counterpart on
    :class:`PlanWeek`, over a subset of the same cards, so the rows reconcile
    with the week's own numbers by construction rather than by agreement.
    """
    rows: list[PlanWeekDiscipline] = []
    for discipline in Discipline:
        group = [card for card in cards if card.discipline is discipline]
        if not group:
            continue
        loads = [
            card.predicted_load for card in group if card.predicted_load is not None
        ]
        sets = [card.total_sets for card in group if card.total_sets is not None]
        rows.append(
            PlanWeekDiscipline(
                discipline=discipline,
                session_count=len(group),
                planned_duration_s=sum(card.planned_duration_s or 0 for card in group),
                planned_load=sum(loads) if loads else None,
                total_sets=sum(sets) if sets else None,
            )
        )
    return tuple(rows)


#: Re-exported so an adapter can state the window it renders without reaching
#: into the domain for the constant.
__all__ = [
    "MAX_WEEK_SESSIONS",
    "WEEK_DAYS",
    "PlanService",
    "PlanWeek",
    "PlanWeekDiscipline",
    "WeekDay",
    "WeekSession",
]
