"""Response schema for `GET /exports/workouts.json`.

The document is openweight's `WorkoutLog` (vendored schema under
`tests/vectors/openweight/`) inside arc's ``arc-export`` envelope, built by
`app.services.exports`. The models here publish its shape in the OpenAPI
contract and check the service's output on the way out; the keys are
openweight's camelCase, and arc's own additions carry the ``arc`` prefix
openweight leaves free (it allows additional properties).

Every optional field is ``X | SkipJsonSchema[None]`` and the route excludes
``None`` on the way out: openweight types its fields and does not allow
``null`` for any of them, so absent is the only way to say "unknown" and still
validate. The contract therefore advertises "may be omitted", never "may be
null".
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic.json_schema import SkipJsonSchema


class _Camel(BaseModel):
    """camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ExportedSet(_Camel):
    """One logged set: openweight's ``SetLog`` plus arc's ``arc*`` fields."""

    reps: int | SkipJsonSchema[None] = None
    #: Kilograms, never negative (openweight's rule). A legacy assistance row
    #: omits it and says ``arc_assistance_kg`` instead.
    weight: Annotated[float, Field(ge=0)] | SkipJsonSchema[None] = None
    unit: str | SkipJsonSchema[None] = None
    duration_seconds: int | SkipJsonSchema[None] = None
    rpe: float | SkipJsonSchema[None] = None
    rir: int | SkipJsonSchema[None] = None
    to_failure: bool | SkipJsonSchema[None] = None
    #: openweight's set type; arc's exact kind is ``arc_set_kind``.
    type: str
    rest_seconds: int | SkipJsonSchema[None] = None
    notes: str | SkipJsonSchema[None] = None
    #: The phone-minted id the set is stored under.
    arc_client_id: uuid.UUID
    #: ``both``, ``left`` or ``right``.
    arc_side: str
    arc_set_kind: str
    arc_per_side: bool
    arc_assistance_kg: float | SkipJsonSchema[None] = None
    arc_rir_capped: bool | SkipJsonSchema[None] = None
    arc_hold_quality: str | SkipJsonSchema[None] = None
    arc_effort_simple: str | SkipJsonSchema[None] = None
    arc_planned_line_ref: str | SkipJsonSchema[None] = None
    arc_replaced_exercise_id: str | SkipJsonSchema[None] = None
    arc_source: str
    arc_confidence: str
    arc_pain_kind: str | SkipJsonSchema[None] = None
    arc_pain_band: str | SkipJsonSchema[None] = None
    arc_completed_at: dt.datetime | SkipJsonSchema[None] = None


class ExportedExerciseName(_Camel):
    """openweight's ``Exercise``: the movement's name and equipment."""

    name: str
    equipment: str | SkipJsonSchema[None] = None


class ExportedExercise(_Camel):
    """openweight's ``ExerciseLog``: a run of sets of one movement."""

    exercise: ExportedExerciseName
    #: 1-based position in the workout.
    order: int
    sets: list[ExportedSet]
    #: The catalogue slug, when the movement is a catalogue one.
    arc_exercise_id: str | SkipJsonSchema[None] = None


class ExportedWorkout(_Camel):
    """openweight's ``WorkoutLog``: one session with logged sets."""

    #: The start, ISO 8601 with the session's own offset.
    date: dt.datetime
    name: str
    notes: str | SkipJsonSchema[None] = None
    duration_seconds: int
    exercises: list[ExportedExercise]
    arc_id: uuid.UUID
    arc_discipline: str
    arc_timezone: str
    arc_start_time_known: bool
    arc_session_rpe: float | SkipJsonSchema[None] = None
    arc_session_rating: int | SkipJsonSchema[None] = None
    arc_bodyweight_kg: float | SkipJsonSchema[None] = None


class WorkoutsExport(_Camel):
    """The whole file: arc's envelope around openweight workouts."""

    #: Always ``arc-export``.
    format: str
    version: int
    exported_at: dt.datetime
    workouts: list[ExportedWorkout]
