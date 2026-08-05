"""Request/response schemas for the workout library.

The step tree is recursive, so these models are too: a repeat block holds
steps, one of which may be a repeat block. Pydantic resolves that through the
forward reference plus :meth:`model_rebuild`, and the OpenAPI schema comes out
as a ``$ref`` cycle that the generated TypeScript follows.

As in `app.api.schemas.criteria`, the schema owns the *structure* and the
domain owns the *rules*: nesting depth, plausibility bounds, "exactly one of
duration or distance", the channel/unit pairing. That is what makes
`app.mcp`'s future tools, which never see these models, obey the same
prescription grammar as the web UI.

The field names deliberately match the domain's wire form exactly, so a
validated request body dumped with ``model_dump(mode="json",
exclude_none=True)`` *is* the document
`app.domain.workout.workout_body_from_json` reads.
"""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.strength import LoadKind
from app.domain.workout import Channel, ChannelUnit, StepRole
from app.persistence.workouts import (
    MAX_DESCRIPTION_LENGTH,
    MAX_FOLDER_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TAG_LENGTH,
)

WorkoutName = Annotated[PostgresText, Field(min_length=1, max_length=MAX_NAME_LENGTH)]
WorkoutDescription = Annotated[
    PostgresText, Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
]
FolderName = Annotated[PostgresText, Field(min_length=1, max_length=MAX_FOLDER_LENGTH)]
Tag = Annotated[PostgresText, Field(min_length=1, max_length=MAX_TAG_LENGTH)]
FreeText = Annotated[PostgresText, Field(min_length=1, max_length=200)]


# --- targets ------------------------------------------------------------------


class PercentOfAnchorSchema(BaseModel):
    """A target expressed as a fraction range of an anchor."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["percent_of_anchor"] = "percent_of_anchor"
    anchor_type: AnchorType
    #: Lower bound as a fraction (``0.85`` is 85 % of the anchor).
    pct_low: float
    #: Upper bound as a fraction; equal to ``pct_low`` for a point target.
    pct_high: float


class AbsoluteRangeSchema(BaseModel):
    """A target expressed as an absolute range in the channel's unit."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    low: float
    high: float
    unit: ChannelUnit


#: One channel's target, discriminated by ``kind``.
TargetSchema = Annotated[
    PercentOfAnchorSchema | AbsoluteRangeSchema, Field(discriminator="kind")
]

#: Targets keyed by channel; at most one per channel per step.
TargetsSchema = dict[Channel, TargetSchema]


# --- steps --------------------------------------------------------------------


class SteadyStepSchema(BaseModel):
    """Hold a set of targets for a duration or a distance."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["steady"] = "steady"
    #: Exactly one of ``duration_s`` and ``distance_m`` must be given.
    duration_s: int | None = None
    distance_m: float | None = None
    targets: TargetsSchema = Field(default_factory=dict)
    role: StepRole = StepRole.WORK
    name: FreeText | None = None


class RampStepSchema(BaseModel):
    """Move from one set of targets to another over a duration or distance."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ramp"] = "ramp"
    #: Exactly one of ``duration_s`` and ``distance_m`` must be given.
    duration_s: int | None = None
    distance_m: float | None = None
    #: Both ends must prescribe the same channels.
    start_targets: TargetsSchema
    end_targets: TargetsSchema
    role: StepRole = StepRole.WORK
    name: FreeText | None = None


class RepeatBlockSchema(BaseModel):
    """Perform ``children`` ``times`` over."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["repeat"] = "repeat"
    times: int
    children: list[StepSchema]


# A plain assignment, not a PEP 695 `type` alias: `RepeatBlockSchema` refers to
# this name as a forward reference, and `model_rebuild` resolves it out of the
# module namespace.
#: Any step of the tree, discriminated by ``kind``.
StepSchema = Annotated[
    SteadyStepSchema | RampStepSchema | RepeatBlockSchema,
    Field(discriminator="kind"),
]

RepeatBlockSchema.model_rebuild()


# --- strength -----------------------------------------------------------------


class LoadSchema(BaseModel):
    """How heavy a prescribed set is."""

    model_config = ConfigDict(extra="forbid")

    kind: LoadKind
    #: Absent — and only absent — for a ``bodyweight`` load. A
    #: ``percent_e1rm`` value is a fraction (``0.85`` is 85 % of the e1RM).
    value: float | None = None


class StrengthSetSchema(BaseModel):
    """One line of a strength prescription."""

    model_config = ConfigDict(extra="forbid")

    #: Slug of a catalogue exercise; see `GET /api/v1/exercises`.
    exercise_id: Annotated[PostgresText, Field(min_length=1, max_length=80)]
    sets: int
    reps: int
    load: LoadSchema
    #: Target reps in reserve, 0-10.
    rir: int | None = None
    rest_s: int | None = None
    #: Free-form tempo notation (``"3-1-1-0"``); uninterpreted by the MVP.
    tempo: FreeText | None = None
    notes: FreeText | None = None


class StrengthGroupSchema(BaseModel):
    """One or more lines performed together; more than one is a superset."""

    model_config = ConfigDict(extra="forbid")

    items: list[StrengthSetSchema]
    label: FreeText | None = None


# --- the structure document ---------------------------------------------------


class EnduranceStructureSchema(BaseModel):
    """A structured endurance prescription."""

    model_config = ConfigDict(extra="forbid")

    discipline: Literal[Discipline.CYCLING] = Discipline.CYCLING
    steps: list[StepSchema]


class StrengthStructureSchema(BaseModel):
    """A strength prescription."""

    model_config = ConfigDict(extra="forbid")

    discipline: Literal[Discipline.STRENGTH] = Discipline.STRENGTH
    groups: list[StrengthGroupSchema]


#: Either discipline's prescription, discriminated by ``discipline``.
WorkoutStructureSchema = Annotated[
    EnduranceStructureSchema | StrengthStructureSchema,
    Field(discriminator="discipline"),
]

#: Validates a stored structure document back into the response schema.
STRUCTURE_ADAPTER: TypeAdapter[Any] = TypeAdapter(WorkoutStructureSchema)


def structure_document(structure: Any) -> dict[str, Any]:
    """Turn a validated structure model into the domain's wire document.

    ``exclude_none`` because the domain reads an absent field and an explicit
    ``null`` the same way, and omitting them keeps the stored document to what
    was actually prescribed.
    """
    return structure.model_dump(mode="json", exclude_none=True)


# --- the library resource -----------------------------------------------------


class WorkoutSummarySchema(BaseModel):
    """Derived facts about a prescription. Computed on read, never stored."""

    #: Flattened step count (endurance) or prescription-line count (strength).
    step_count: int
    #: Total prescribed seconds, or null when any step is distance-based or
    #: the workout is a strength workout.
    total_duration_s: int | None
    #: Total prescribed working sets, for a strength workout.
    total_sets: int | None


class WorkoutCreate(BaseModel):
    """Payload for adding a workout to the library."""

    model_config = ConfigDict(extra="forbid")

    name: WorkoutName
    structure: WorkoutStructureSchema
    description: WorkoutDescription | None = None
    #: A flat label, not a path. Omit for an unfiled workout.
    folder: FolderName | None = None
    #: Lower-cased and deduplicated on write.
    tags: list[Tag] = Field(default_factory=list)


class WorkoutUpdate(BaseModel):
    """Payload for partially updating a workout. Omitted fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: WorkoutName | None = None
    structure: WorkoutStructureSchema | None = None
    description: WorkoutDescription | None = None
    folder: FolderName | None = None
    tags: list[Tag] | None = None


class WorkoutRead(BaseModel):
    """One library workout as returned by the API."""

    id: uuid.UUID
    name: str
    description: str | None
    discipline: Discipline
    folder: str | None
    tags: list[str]
    structure: WorkoutStructureSchema
    summary: WorkoutSummarySchema
    created_at: dt.datetime
    updated_at: dt.datetime


WorkoutsPage = Page[WorkoutRead]


class WorkoutLabelsRead(BaseModel):
    """The folder labels and tags currently in use across the library."""

    folders: list[str]
    tags: list[str]
