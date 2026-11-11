"""Response schema for the session packet (`app.services.packet`).

Every number in it is computed on the server; the phone stores the document
as it arrives and renders from it offline. Nullable fields are nullable
because the value is genuinely absent (a bodyweight line has no plates, a
blank pre-fill has no weight) — this is a response, nobody sends it.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.api.schemas.exercises import ExerciseRead
from app.api.schemas.workouts import StrengthSetSchema
from app.domain.activity import EffortSimple
from app.domain.loading import Equipment
from app.domain.progression import SuggestionOutcome, SuggestionReason
from app.domain.purpose import Purpose
from app.domain.rules import Evidence
from app.domain.training_mode import AskFrequency, TrainingMode, Units
from app.services.packet import EffortScale, PrefillSource


class PrefillRead(BaseModel):
    """The numbers a line's first set opens with, and where they came from.

    ``starting_flow`` is true exactly when ``source`` is ``blank``: the
    exercise has never been logged and the line names no weight, so the phone
    guides the athlete to a starting weight.
    """

    model_config = ConfigDict(from_attributes=True)

    source: PrefillSource
    load_kg: float | None
    reps: int | None
    duration_s: int | None
    starting_flow: bool


class WarmupRead(BaseModel):
    """One generated warm-up set."""

    model_config = ConfigDict(from_attributes=True)

    kg: float
    reps: int
    #: Plates on each side of the bar, heaviest first; null off a barbell.
    plates_per_side: list[float] | None


class SuggestionInputRead(BaseModel):
    """One session a suggestion read, as the set it was judged by."""

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    #: The athlete-local date of the session.
    date: dt.date
    load_kg: float | None
    reps: int | None
    #: Reps left as reported (a set RPE converted); null when unrated.
    reps_left: float | None
    #: The rating was "4+": ``reps_left`` is a floor, not a count.
    reps_left_capped: bool
    effort_simple: EffortSimple | None
    #: The session logged fewer working sets than prescribed.
    trimmed: bool


class SuggestionExplanationRead(BaseModel):
    """Why a suggestion says what it says: the Why sheet and its Details."""

    model_config = ConfigDict(from_attributes=True)

    #: The registry id of the rule that set the value
    #: (`app.domain.rules`); null only for a first time.
    rule: str | None
    evidence: Evidence | None
    reason: SuggestionReason
    #: The sessions read, oldest first.
    inputs: list[SuggestionInputRead]
    target_reps: int | None
    #: The reps left the effort was held to (a range's nearer end).
    target_reps_left: float | None
    #: The rule's result before limits and rounding, unrounded.
    computed_kg: float | None
    #: The registry id of the limit that held the result back, if any.
    limit: str | None


class SuggestionRead(BaseModel):
    """Next session's numbers for one line, from what was lifted."""

    model_config = ConfigDict(from_attributes=True)

    outcome: SuggestionOutcome
    #: Null for a first time: the plan or the starting flow decides.
    load_kg: float | None
    reps: int | None
    #: ``load_kg`` minus the last weight lifted (made loadable).
    change_kg: float | None
    explanation: SuggestionExplanationRead


class PacketLineRead(BaseModel):
    """One prescription line with everything the gym screen needs."""

    model_config = ConfigDict(from_attributes=True)

    #: ``"<group>.<item>"``, 0-based: what a logged set sends as
    #: ``planned_line_ref``.
    line_ref: str
    group_index: int
    item_index: int
    group_label: str | None
    exercise_id: str
    exercise_name: str
    prescription: StrengthSetSchema
    #: Whether this line is a main lift (explicit, or the pattern default).
    main: bool
    #: How the weight is made up, when it is rounded at all; null for
    #: bodyweight, kettlebells, plate-loaded machines and the like.
    equipment: Equipment | None
    prefill: PrefillRead
    #: Empty when the line gets no ramp.
    warmups: list[WarmupRead]
    #: Plates for the pre-filled weight; null off a barbell or with no weight.
    plates_per_side: list[float] | None
    #: The suggested next weight and why. Null under Plain logbook or
    #: follow-exactly, and for a line with no loadable weight.
    suggestion: SuggestionRead | None


class PacketSessionRead(BaseModel):
    """One planned strength session in the packet's window."""

    model_config = ConfigDict(from_attributes=True)

    planned_session_id: uuid.UUID
    date: dt.date
    #: ``HH:MM`` on the athlete's clock, as everywhere a time of day is sent.
    time_of_day: str | None
    purpose: Purpose
    intent_text: str | None
    key: bool
    plan_id: uuid.UUID
    #: The plan must be followed exactly: every pre-fill is the prescription.
    follow_exactly: bool
    lines: list[PacketLineRead]


class LastNumbersRead(BaseModel):
    """The last non-warm-up set logged for one exercise."""

    model_config = ConfigDict(from_attributes=True)

    exercise_id: str
    exercise_name: str
    load_kg: float | None
    reps: int | None
    duration_s: int | None
    per_side: bool
    #: The athlete-local date of the session it was logged in.
    logged_on: dt.date
    #: Its weight may not go up: the latest session of it had sharp pain
    #: (`app.domain.safety.blocks_increase`).
    increase_blocked: bool


class PacketSettingsRead(BaseModel):
    """The training settings the packet was prepared under."""

    model_config = ConfigDict(from_attributes=True)

    mode: TrainingMode
    plain_logbook: bool
    units: Units


class EffortQuestionRead(BaseModel):
    """Which effort question follows a set, and how often it is asked."""

    model_config = ConfigDict(from_attributes=True)

    scale: EffortScale
    ask: AskFrequency


class PlatePairRead(BaseModel):
    """One kind of plate in the gym, and how many pairs of it."""

    kg: float
    pairs: int


class GymRead(BaseModel):
    """The gym every weight in the packet was made loadable for."""

    bar_kg: float
    plate_pairs: list[PlatePairRead]
    dumbbell_step_kg: float
    dumbbell_max_kg: float
    stack_step_kg: float
    stack_max_kg: float


class PacketRead(BaseModel):
    """Everything the phone stores to train the next seven days offline."""

    model_config = ConfigDict(from_attributes=True)

    #: The phone refuses a packet newer than it understands.
    schema_version: int
    generated_at: dt.datetime
    #: The window, half-open: ``[starts_on, ends_before)``.
    starts_on: dt.date
    ends_before: dt.date
    settings: PacketSettingsRead
    effort: EffortQuestionRead
    gym: GymRead
    sessions: list[PacketSessionRead]
    last_numbers: list[LastNumbersRead]
    #: The library entries the sessions and the last-numbers table name.
    library: list[ExerciseRead]
