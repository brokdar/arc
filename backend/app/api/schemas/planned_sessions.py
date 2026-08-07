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
from app.api.schemas.workouts import WorkoutStructureSchema, WorkoutSummarySchema
from app.api.validation import PostgresText
from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.purpose import Purpose
from app.domain.sessions import MAX_INTENT_CHARS, SessionStatus

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


class PlannedSessionRead(BaseModel):
    """One planned session, with the intent version in force."""

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    status: SessionStatus
    intent: SessionIntentRead
    #: How many intent versions exist, including the one in force.
    intent_versions: int
    created_at: dt.datetime
    updated_at: dt.datetime


PlannedSessionsPage = Page[PlannedSessionRead]


class SessionIntentsRead(BaseModel):
    """Every intent version of one session, oldest first."""

    items: list[SessionIntentRead]
