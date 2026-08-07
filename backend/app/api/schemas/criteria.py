"""Request/response schemas for success criteria.

These mirror `app.domain.criteria`'s wire form field for field, because that
form is what is stored, what the API speaks, and what the generated frontend
types are derived from — three views of one document, so they have to be one
shape.

The split of responsibility matches `app.api.schemas.anchors`: the schema
owns the *structure* (which members the tagged union has, which fields each
carries, what type they are) and the domain owns the *rules* (fractions in
[0, 1], a band that is not inverted, a ceiling whose unit matches its
channel). Restating the numeric bounds here would mean two places to change
and two messages to keep in agreement, and the domain's message is the better
one.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.anchors import AnchorType
from app.domain.criteria import (
    DEFAULT_BAND_SMOOTHING_S,
    DEFAULT_CEILING_SMOOTHING_S,
    CriterionKind,
    StepSelectorKind,
)
from app.domain.workout import Channel, ChannelUnit, StepRole


class StepSelectorSchema(BaseModel):
    """Which flattened steps a criterion applies to."""

    model_config = ConfigDict(extra="forbid")

    kind: StepSelectorKind
    #: Required for a ``role`` selector, absent otherwise.
    role: StepRole | None = None
    #: Required for an ``index`` selector, absent otherwise.
    index: int | None = None


class BandSchema(BaseModel):
    """An acceptable range around a step's prescribed target, as fractions."""

    model_config = ConfigDict(extra="forbid")

    channel: Channel
    #: Lower bound as a fraction of the prescribed target (``0.95`` = -5 %).
    low: float
    #: Upper bound as a fraction of the prescribed target.
    high: float
    #: Seconds of trailing rolling mean applied to the channel before it is
    #: compared to the band; 0 means raw samples. Omit to take the default —
    #: the window is part of the frozen intent, so it is always returned.
    smoothing_s: int = DEFAULT_BAND_SMOOTHING_S


class PercentLimitSchema(BaseModel):
    """A ceiling expressed as a fraction of an anchor."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["percent_of_anchor"] = "percent_of_anchor"
    anchor_type: AnchorType
    pct: float


class AbsoluteLimitSchema(BaseModel):
    """A ceiling expressed in the channel's own unit."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    value: float
    unit: ChannelUnit


LimitSchema = Annotated[
    PercentLimitSchema | AbsoluteLimitSchema, Field(discriminator="kind")
]


class TimeInBandSchema(BaseModel):
    """At least ``min_fraction`` of the selected steps' time inside the band."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CriterionKind.TIME_IN_BAND] = CriterionKind.TIME_IN_BAND
    selector: StepSelectorSchema
    band: BandSchema
    min_fraction: float


class DurationFloorSchema(BaseModel):
    """The session must last at least this long."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CriterionKind.DURATION_FLOOR] = CriterionKind.DURATION_FLOOR
    min_seconds: int


class CeilingSchema(BaseModel):
    """No more than this long spent above this limit on this channel."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CriterionKind.CEILING] = CriterionKind.CEILING
    channel: Channel
    limit: LimitSchema
    max_seconds_above: int
    #: Seconds of trailing rolling mean applied before the comparison.
    #: Defaults to 0 — raw — because a ceiling is about excursions and
    #: smoothing hides them.
    smoothing_s: int = DEFAULT_CEILING_SMOOTHING_S


class SetsCompletedSchema(BaseModel):
    """At least this fraction of the prescribed strength sets performed."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CriterionKind.SETS_COMPLETED] = CriterionKind.SETS_COMPLETED
    min_fraction: float


class LoadWithinSchema(BaseModel):
    """Loads used within this relative tolerance of what was prescribed."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CriterionKind.LOAD_WITHIN] = CriterionKind.LOAD_WITHIN
    pct_tolerance: float


#: One success criterion, discriminated by ``kind``.
SuccessCriterionSchema = Annotated[
    TimeInBandSchema
    | DurationFloorSchema
    | CeilingSchema
    | SetsCompletedSchema
    | LoadWithinSchema,
    Field(discriminator="kind"),
]
