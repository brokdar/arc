"""Response schemas for the calendar's week view.

Read-only, and deliberately flat: a card is a row of facts, not a nested
document. What a card cannot show — the step tree, the success criteria, the
pins, the intent history — is one request away at
`GET /api/v1/planned-sessions/{id}`, which is what opening the session sheet
does (D55).

`from_attributes` throughout: the service returns the projection as frozen
dataclasses, so the route validates them straight through instead of restating
every field.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.athlete import Discipline
from app.domain.matching import MatchLinkStatus
from app.domain.purpose import Purpose
from app.domain.sessions import SessionStatus


class WeekSessionRead(BaseModel):
    """One planned session, as a calendar card shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    purpose: Purpose
    status: SessionStatus
    #: The library workout this was planned from, if it still exists; null for
    #: an inline prescription. Label the card from the purpose when null.
    title: str | None
    workout_id: uuid.UUID | None
    #: Prescribed seconds; null for a strength session and for an endurance
    #: session with a distance-based step.
    planned_duration_s: int | None
    #: Prescribed working sets, for a strength session; null otherwise.
    total_sets: int | None
    #: Flattened steps (endurance) or prescription lines (strength).
    step_count: int
    #: The one-line intent, in the athlete's own words.
    intent_text: str | None
    #: Which intent version this card is showing.
    intent_version: int
    #: TSS-equivalent this prescription is expected to cost, computed on read
    #: from the frozen intent and the anchor versions it pinned. Null when
    #: there is nothing honest to say: a strength session, a distance-based
    #: ride, a ride with no power target, or an FTP that was never pinned.
    predicted_load: float | None
    #: Planned normalized power over the pinned FTP; null alongside
    #: ``predicted_load``.
    predicted_intensity_factor: float | None
    #: Fraction of the prescribed duration that carried a power target — the
    #: same number ``PredictedLoadRead.coverage`` carries on
    #: ``GET /planned-sessions/{id}``, from the same computation. Null exactly
    #: when ``predicted_load`` is. **Never render a card's load without it**:
    #: below 1.0 the load is an under-estimate, and a 40%-covered prediction
    #: is indistinguishable from a complete one otherwise (D88).
    predicted_load_coverage: float | None
    #: Σ ``sets × reps × kg`` for a strength session whose loads are in
    #: kilograms. **Kilograms, not a load** — never add it to
    #: ``predicted_load``, and never render the two in one column.
    predicted_volume_load_kg: float | None
    #: The recorded session matched to this card (WP-6), or null.
    matched_session_id: uuid.UUID | None
    #: What that link claims. ``pending`` is a **proposal**: ``status`` above
    #: is still ``planned`` until the athlete answers it, so the card shows a
    #: question rather than a completion.
    match_status: MatchLinkStatus | None


class PlanWeekDayRead(BaseModel):
    """One day of the week: what was planned for it, and what was recorded."""

    model_config = ConfigDict(from_attributes=True)

    date: dt.date
    sessions: list[WeekSessionRead]
    #: Recorded sessions dated on this day.
    completed_session_count: int
    #: Their total duration — recording time for a device session, wall clock
    #: for one typed in. Null — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their total training load; null when none of them has one yet.
    completed_load: float | None


class PlanWeekDisciplineRead(BaseModel):
    """One week's totals for one discipline.

    The two axes keep their own columns. ``planned_load`` is TSS and
    ``total_sets`` counts strength sets; there is deliberately no field that
    could hold their sum, because they measure different things (spec v2
    §5.4, §8.3).

    Both totals carry their own coverage pair, so a row explains its own
    missing number instead of leaving a client to invent a reason for it.
    """

    model_config = ConfigDict(from_attributes=True)

    discipline: Discipline
    session_count: int
    #: Prescribed seconds across this discipline's sessions that have one;
    #: null — never 0 — when none of them does.
    planned_duration_s: int | None
    #: How many of this discipline's sessions contributed to
    #: ``planned_duration_s``, and how many could not.
    duration_sessions_counted: int
    duration_sessions_uncounted: int
    #: TSS across this discipline's predictable sessions; null when none is.
    planned_load: float | None
    #: How many of this discipline's sessions contributed to
    #: ``planned_load``, and how many could not.
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: Prescribed working sets; null for a discipline that has none.
    total_sets: int | None
    #: Recorded sessions of this discipline in the window.
    completed_session_count: int
    #: Their duration; null — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their training load, with its own coverage pair. **Never render the
    #: total without them**, and never add it to ``planned_load``: planned and
    #: completed are two columns, and their sum is not a quantity.
    completed_load: float | None
    completed_load_sessions_counted: int
    completed_load_sessions_uncounted: int


class PlanWeekRead(BaseModel):
    """Seven consecutive days of the plan, empty days included."""

    model_config = ConfigDict(from_attributes=True)

    start: dt.date
    #: The last day in the window, inclusive — ``start`` plus six days.
    end: dt.date
    #: Always seven entries, in date order.
    days: list[PlanWeekDayRead]
    #: Every session in the window, including any the render cap left out of
    #: ``days``. The overflow counts as uncounted against both coverage pairs.
    session_count: int
    #: Prescribed seconds across the week. Null — never 0 — when no session
    #: contributed one, the empty week included.
    planned_duration_s: int | None
    #: How many sessions contributed to ``planned_duration_s``, and how many
    #: could not. **Never render the total without them.**
    duration_sessions_counted: int
    duration_sessions_uncounted: int
    #: TSS across the sessions that could be predicted. Null — never 0 — when
    #: none of them could.
    planned_load: float | None
    #: How many sessions contributed to ``planned_load``, and how many could
    #: not. **Never render the total without them**: a week of six sessions
    #: where only two are predictable must not read as a light week.
    load_sessions_counted: int
    load_sessions_uncounted: int
    #: Recorded sessions dated inside the window, whatever was planned.
    completed_session_count: int
    #: Their total duration. Null — never 0 — when there were none.
    completed_duration_s: float | None
    #: Their total training load, with its own coverage pair.
    completed_load: float | None
    completed_load_sessions_counted: int
    completed_load_sessions_uncounted: int
    #: Treff's polarization index across the week's recorded sessions. Null
    #: until computable: it needs time in all three bands, so an all-easy week
    #: has none, and the reason below says which band was empty.
    completed_polarization_index: float | None
    completed_polarization_not_assessed: str | None
    #: The channel rule the index counted by — **one channel per session**
    #: (A5.4). Always present: summing a session's power zones and its
    #: heart-rate zones counts its duration twice, so the number is
    #: meaningless without the rule beside it.
    completed_polarization_rule: str
    completed_polarization_sessions_counted: int
    completed_polarization_sessions_uncounted: int
    #: One row per discipline that has a planned or a recorded session this
    #: week, in vocabulary order. These totals reconcile with the flat ones
    #: above, except that a recorded sport nothing is planned as (a walk, a
    #: swim) counts in the week's totals and in no discipline row.
    by_discipline: list[PlanWeekDisciplineRead]
