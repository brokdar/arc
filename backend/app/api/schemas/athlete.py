"""Request/response schemas for the athlete profile."""

import datetime as dt
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.validation import PostgresJsonObject, PostgresText
from app.core.clock import athlete_today
from app.domain.athlete import (
    MAX_HEIGHT_CM,
    MAX_RED_FLAG_NOTE_CHARS,
    MIN_HEIGHT_CM,
    RedFlagSeverity,
    Sex,
)
from app.domain.plan import PlanState

#: Constraints live INSIDE the union member: applied to the union itself,
#: pydantic would try `min_length` against None and 500 with a TypeError.
AthleteName = Annotated[PostgresText, Field(min_length=1, max_length=200)]
HeightCm = Annotated[float, Field(ge=MIN_HEIGHT_CM, le=MAX_HEIGHT_CM)]
RedFlagNote = Annotated[
    PostgresText, Field(min_length=1, max_length=MAX_RED_FLAG_NOTE_CHARS)
]


class AthleteRead(BaseModel):
    """The athlete profile as returned by the API.

    Every field is nullable: the profile is bootstrapped empty on first access
    and filled in from the UI.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str | None
    date_of_birth: dt.date | None
    sex: Sex
    height_cm: float | None
    #: Free-form per-discipline capability stub; opaque to the MVP.
    capabilities: dict[str, Any]
    #: Whether the plan is being enforced. `paused` stops missed-session
    #: marking and nothing else (`app.domain.plan`).
    plan_state: PlanState
    #: Whether the athlete is currently ill or injured (WP-8.4). While set,
    #: the coaching agent may not propose a change that adds or intensifies
    #: work, and every agent read carries the flag so it cannot claim not to
    #: have known.
    red_flag_active: bool
    red_flag_note: str | None
    #: Non-null exactly while `red_flag_active` is set.
    red_flag_severity: RedFlagSeverity | None
    created_at: dt.datetime
    updated_at: dt.datetime


class AthleteUpdate(BaseModel):
    """Payload for partially updating the profile.

    Omitted fields are left unchanged; an explicit ``null`` clears a field
    (for ``sex``, ``capabilities``, ``plan_state`` and ``red_flag_active``,
    "clear" means back to ``unspecified``, ``{}``, ``active`` and ``false`` —
    those four have an empty value rather than an absent one).
    """

    # `extra="forbid"` so a typo'd field name is a 422 rather than a silent
    # no-op: with one athlete and no undo, a lost edit is expensive.
    model_config = ConfigDict(extra="forbid")

    name: AthleteName | None = None
    date_of_birth: dt.date | None = None
    sex: Sex | None = None
    height_cm: HeightCm | None = None
    capabilities: PostgresJsonObject | None = None
    #: Pause the plan (no missed-session marking) or resume it.
    plan_state: PlanState | None = None
    #: Raise or lower the illness/injury flag. Raising it requires a severity
    #: in the same request; lowering it clears the note and the severity, so
    #: `{"red_flag_active": false}` on its own is enough to say "I am better".
    red_flag_active: bool | None = None
    red_flag_note: RedFlagNote | None = None
    red_flag_severity: RedFlagSeverity | None = None

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_the_future(cls, value: dt.date | None) -> dt.date | None:
        # Checked here rather than in the domain: "is this in the future"
        # needs a clock, and `app.domain` stays free of ambient state. The
        # lower bound (EARLIEST_BIRTH_YEAR) is a domain rule and lives there.
        #
        # The clock is the athlete's own (`app.core.clock`), not UTC. The
        # difference only ever decides one edge case — somebody born today,
        # typing it in on the far side of a date line — but a schema spelling
        # "today" its own way is how a process ends up with four of them
        # (issue #62), and the one that matters is a schema layer that can
        # reach the shared answer without reaching into a service.
        if value is not None and value > athlete_today():
            raise ValueError("date_of_birth cannot be in the future")
        return value
