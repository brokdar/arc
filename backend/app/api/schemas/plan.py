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


class PlanWeekDayRead(BaseModel):
    """One day of the week, with the sessions planned for it."""

    model_config = ConfigDict(from_attributes=True)

    date: dt.date
    sessions: list[WeekSessionRead]


class PlanWeekRead(BaseModel):
    """Seven consecutive days of the plan, empty days included."""

    model_config = ConfigDict(from_attributes=True)

    start: dt.date
    #: The last day in the window, inclusive — ``start`` plus six days.
    end: dt.date
    #: Always seven entries, in date order.
    days: list[PlanWeekDayRead]
    session_count: int
    #: Prescribed seconds across the week, counting the sessions that have a
    #: duration to count.
    planned_duration_s: int
