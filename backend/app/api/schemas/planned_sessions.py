"""Request/response schemas for planned sessions and their intent versions.

The read shape has two levels on purpose. A planned session carries the intent
version **in force** inline, because that is what a calendar renders; the
older versions are a sub-resource
(`GET /api/v1/planned-sessions/{id}/intents`), because they are history and
history is asked for, not served by default.

`PlannedSessionUpdate` accepts intent fields and session fields in one payload
and the service decides which of them versions anything — the freeze rule is a
domain concern, and splitting it across two endpoints would put it in the
adapter.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.api.pagination import Page
from app.api.schemas.criteria import SuccessCriterionSchema
from app.api.schemas.matching import MatchSummary
from app.api.schemas.workouts import WorkoutStructureSchema, WorkoutSummarySchema
from app.api.validation import PostgresText
from app.domain.anchors import AnchorType, AnchorUnit, Provenance
from app.domain.athlete import Discipline
from app.domain.purpose import Purpose
from app.domain.sessions import MAX_INTENT_CHARS, SessionStatus
from app.domain.workout import Channel, ChannelUnit, StepRole

IntentText = Annotated[PostgresText, Field(min_length=1, max_length=MAX_INTENT_CHARS)]


class PlannedSessionCreate(BaseModel):
    """Payload for planning a session."""

    model_config = ConfigDict(extra="forbid")

    #: The athlete-local date the session belongs to.
    date: dt.date
    purpose: Purpose
    #: Exactly one of ``workout_id`` and ``structure``: the prescription comes
    #: from the library or is written inline.
    workout_id: uuid.UUID | None = None
    structure: WorkoutStructureSchema | None = None
    intent_text: IntentText | None = None
    coach_notes: IntentText | None = None
    #: Omit to take the purpose template's defaults, which is the normal case.
    success_criteria: list[SuccessCriterionSchema] | None = None


class PlannedSessionUpdate(BaseModel):
    """Payload for updating a planned session. Omitted fields are unchanged.

    Touching ``purpose``, ``intent_text``, ``coach_notes``,
    ``success_criteria``, ``workout_id`` or ``structure`` appends a new intent
    version. Touching only ``date`` or ``status`` does not.
    """

    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None
    status: SessionStatus | None = None
    purpose: Purpose | None = None
    workout_id: uuid.UUID | None = None
    structure: WorkoutStructureSchema | None = None
    intent_text: IntentText | None = None
    coach_notes: IntentText | None = None
    success_criteria: list[SuccessCriterionSchema] | None = None


class PlannedSessionMove(BaseModel):
    """Payload for moving a planned session to another date."""

    model_config = ConfigDict(extra="forbid")

    #: The athlete-local date to move the session to.
    date: dt.date


class PlannedSessionCopy(BaseModel):
    """Payload for copying a planned session onto another date."""

    model_config = ConfigDict(extra="forbid")

    #: The athlete-local date the copy is planned for.
    date: dt.date


class SessionIntentRead(BaseModel):
    """One version of a planned session's intent.

    Carries WP-1's versioning vocabulary verbatim, because it is one of the
    versioned artefacts invariant 1 describes.
    """

    id: uuid.UUID
    #: Stable identity of the artefact: the planned session.
    artefact_id: uuid.UUID
    version: int
    as_of: dt.datetime
    #: Id of the version that replaced this one; null on the version in force.
    superseded_by: uuid.UUID | None
    recompute_reason: str | None
    #: True when this version was written after the session had been matched.
    edited_post_hoc: bool

    purpose: Purpose
    intent_text: str | None
    coach_notes: str | None
    success_criteria: list[SuccessCriterionSchema]
    #: Anchor type -> the anchor version id this intent's percentages resolve
    #: against, frozen when the version was written.
    pinned_anchor_versions: dict[AnchorType, uuid.UUID]
    #: The library workout this came from, if any. Null once that workout is
    #: deleted; the frozen structure below is unaffected.
    workout_id: uuid.UUID | None
    #: The prescription as frozen at this version.
    structure: WorkoutStructureSchema
    summary: WorkoutSummarySchema


class ResolvedTargetRead(BaseModel):
    """One channel's target, said both ways.

    The prescription and the numbers are both returned because they are both
    the truth: ``88–93 % FTP`` is what survives an FTP change and what a
    purpose template can express, ``220–232 W`` is what the athlete rides.
    """

    channel: Channel
    #: The target as written, render-ready: ``"88–93 % FTP"``, ``"250 W"``.
    prescribed: str
    #: Lower bound in ``unit``, or null when nothing resolves it — an
    #: absolute target passes through, a percentage of an anchor this session
    #: did not pin resolves to null on both bounds. Null means "not
    #: resolved", never zero.
    resolved_low: float | None
    #: Upper bound; equal to ``resolved_low`` for a point target.
    resolved_high: float | None
    unit: ChannelUnit
    #: The anchor version the percentage resolved against; null for an
    #: absolute target.
    anchor_version_id: uuid.UUID | None


class ResolvedStepRead(BaseModel):
    """One flattened step of the prescription, with its targets resolved."""

    #: 0-based position in the flattened sequence — the same index the step
    #: tree flattens to, so a client can join this onto what it already drew.
    index: int
    role: StepRole
    name: str | None
    duration_s: int | None
    distance_m: float | None
    #: When false, ``end_targets`` repeats ``start_targets``.
    is_ramp: bool
    start_targets: list[ResolvedTargetRead]
    end_targets: list[ResolvedTargetRead]


class PinnedAnchorRead(BaseModel):
    """One anchor version this session's percentages resolve against.

    The pin is the product's most distinctive invariant (build-plan invariant
    4) and it is worth nothing invisible: showing the provenance is what
    makes an `estimated` FTP read as an estimate rather than a fact.
    """

    anchor_type: AnchorType
    anchor_version_id: uuid.UUID
    value: float
    unit: AnchorUnit
    provenance: Provenance
    #: The date the value describes the athlete from — not when it was entered.
    effective_date: dt.date


class MetricExplanationRead(BaseModel):
    """Why a computed number is the number. Travels with it; not page copy."""

    #: The arithmetic, written the way a human reads it.
    formula: str
    #: Named quantities that went in, already rendered. An anchor input names
    #: the **version's** value, provenance and effective date, never the
    #: athlete's current one.
    inputs: dict[str, str]
    #: What the computation had to assume. Empty when there were none.
    assumptions: list[str]
    #: Where the method comes from, when it comes from somewhere.
    citation: str | None


class PredictedLoadRead(BaseModel):
    """What this prescription is expected to cost, and how that was arrived at."""

    #: TSS-equivalent. Never add this to a strength session's kilograms.
    load: float
    #: Planned normalized power over the pinned FTP.
    intensity_factor: float
    #: Fraction of the prescribed duration that carried a power target. Below
    #: 1.0 the load is an **under**-estimate: the rest counted as 0 W.
    coverage: float
    #: The FTP version the prediction resolved against.
    anchor_version_id: uuid.UUID
    explanation: MetricExplanationRead


class PredictedVolumeRead(BaseModel):
    """What a strength prescription is expected to cost. **Not** a load.

    Kilograms and TSS are different axes (spec v2 §5.4, §8.3): never add
    ``volume_load_kg`` to ``PredictedLoadRead.load``, and never render the two
    in one column. Exactly one of the two is ever present on a session.
    """

    #: Σ ``sets × reps × kg`` over the sets prescribed in kilograms; null when
    #: none is — a session of bodyweight, RPE or %e1RM work has no volume load
    #: until it is performed. Null means "not assessed", never zero.
    volume_load_kg: float | None
    #: Prescribed working sets across the whole workout, whatever their load
    #: kind — the honest denominator.
    total_sets: int
    #: Fraction of ``total_sets`` whose load is in kilograms. Below 1.0,
    #: ``volume_load_kg`` covers only part of the session.
    coverage: float


class PlannedSessionListItem(BaseModel):
    """One planned session as a **list row**: everything but the expensive parts.

    Deliberately not `PlannedSessionRead`.
    A page of this collection is a page of *sessions*, and serving the resolved
    step tree and the load explanation for every one of them costs megabytes
    of body and seconds of CPU that no list view spends. What is dropped is
    dropped whole rather than emptied: `resolved_steps`, `predicted_load` and
    `predicted_volume` are **absent from this shape**, not null in it, so a
    client cannot mistake a list row for a session that has no prediction.

    `GET /planned-sessions/{id}` — and every write route, which answers with
    the session it wrote — returns the full `PlannedSessionRead`.
    """

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    status: SessionStatus
    intent: SessionIntentRead
    #: How many intent versions exist, including the one in force.
    intent_versions: int
    created_at: dt.datetime
    updated_at: dt.datetime
    #: The anchor versions this session pinned, in anchor-type order. Kept on
    #: the list row: the whole page's pins are one query, and a row that
    #: quotes a percentage without saying what it resolves against is the
    #: thing invariant 4 exists to prevent.
    pinned_anchors: list[PinnedAnchorRead]
    #: The recorded session linked to this one (WP-6), when there is one. A
    #: `pending` link is a proposal: the status above is still ``planned``
    #: until the athlete answers it.
    match: MatchSummary | None = None


class PlannedSessionRead(PlannedSessionListItem):
    """One planned session, with the intent version in force.

    The resolved fields are computed on every read from the intent's frozen
    prescription and the anchor versions it pinned — never stored, so
    appending a new anchor cannot change what an existing session says.
    """

    #: Every flattened step with its targets resolved against those pins.
    #: Empty for a strength session, which prescribes no anchor percentages.
    resolved_steps: list[ResolvedStepRead]
    #: Null when the cost cannot honestly be predicted: a strength session, a
    #: distance-based ride, a ride with no power target, an unpinned FTP.
    predicted_load: PredictedLoadRead | None
    #: The prescribed volume load of a strength session; null for an endurance
    #: one. The other axis — never summed with ``predicted_load``.
    predicted_volume: PredictedVolumeRead | None


PlannedSessionsPage = Page[PlannedSessionListItem]


class SessionIntentsRead(BaseModel):
    """Every intent version of one session, oldest first."""

    items: list[SessionIntentRead]
