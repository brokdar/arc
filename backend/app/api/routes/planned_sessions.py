"""HTTP endpoints for planned sessions. Thin layer over the service.

Everything interesting — pinning anchors at creation, versioning intent on
edit, flagging a post-execution edit — happens in
`app.services.planned_sessions`, because WP-8's MCP tools do the same things
and may not import this module.

Intent versions are a sub-resource rather than a payload field: the version in
force is what a calendar renders, and the history is asked for by whoever
needs to explain a score.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.planned_sessions import (
    PlannedSessionCopy,
    PlannedSessionCreate,
    PlannedSessionMove,
    PlannedSessionRead,
    PlannedSessionsPage,
    PlannedSessionUpdate,
    SessionIntentRead,
    SessionIntentsRead,
)
from app.api.schemas.workouts import (
    STRUCTURE_ADAPTER,
    WorkoutSummarySchema,
    structure_document,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.sessions import SessionStatus
from app.domain.workout import workout_body_from_json
from app.persistence.db import SessionDep
from app.persistence.planned_sessions import (
    PlannedSessionIntentRow,
    PlannedSessionRow,
)
from app.services.planned_sessions import PlannedSessionService
from app.services.workouts import WorkoutSummary

router = APIRouter(prefix="/planned-sessions", tags=["planned-sessions"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "No such planned session"}
}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Session violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> PlannedSessionService:
    """Bind the service to a request-scoped session."""
    return PlannedSessionService.from_session(session)


ServiceDep = Annotated[PlannedSessionService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`.
StartFilter = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(description="Earliest athlete-local date to include (inclusive)."),
]
EndFilter = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(description="Latest athlete-local date to include (inclusive)."),
]
# Aliased because `status` is taken in this module by `fastapi.status`; the
# query parameter clients see is `?status=`.
StatusFilter = Annotated[
    SessionStatus | SkipJsonSchema[None],
    Query(alias="status", description="Restrict to one status; omit for all of them."),
]


def intent_to_read(intent: PlannedSessionIntentRow) -> SessionIntentRead:
    """Project one stored intent version onto its response shape."""
    summary = WorkoutSummary(workout_body_from_json(intent.structure))
    return SessionIntentRead.model_validate(
        {
            "id": intent.id,
            "artefact_id": intent.artefact_id,
            "version": intent.version,
            "as_of": intent.as_of,
            "superseded_by": intent.superseded_by,
            "recompute_reason": intent.recompute_reason,
            "edited_post_hoc": intent.edited_post_hoc,
            "purpose": intent.purpose,
            "intent_text": intent.intent_text,
            "coach_notes": intent.coach_notes,
            "success_criteria": intent.success_criteria,
            "pinned_anchor_versions": intent.pinned_anchor_versions,
            "workout_id": intent.workout_id,
            "structure": STRUCTURE_ADAPTER.validate_python(intent.structure),
            "summary": WorkoutSummarySchema(
                step_count=summary.step_count,
                total_duration_s=summary.total_duration_s,
                total_sets=summary.total_sets,
            ),
        }
    )


def to_read(row: PlannedSessionRow) -> PlannedSessionRead:
    """Project a stored planned session onto its response shape."""
    return PlannedSessionRead(
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        status=row.status,
        intent=intent_to_read(row.current_intent),
        intent_versions=len(row.intents),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("")
async def list_planned_sessions(
    service: ServiceDep,
    page: PageParamsDep,
    start: StartFilter = None,
    end: EndFilter = None,
    session_status: StatusFilter = None,
) -> PlannedSessionsPage:
    """List planned sessions in date order, optionally within a date range."""
    sessions, total = await service.list(
        start=start,
        end=end,
        status=session_status,
        offset=page.offset,
        limit=page.limit,
    )
    return PlannedSessionsPage(
        items=[to_read(session) for session in sessions],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID | NOT_FOUND
)
async def create_planned_session(
    service: ServiceDep, actor: ActorDep, payload: PlannedSessionCreate
) -> PlannedSessionRead:
    """Plan a session, freezing its prescription and pinning its anchors."""
    row = await service.create(
        actor=actor,
        date=payload.date,
        purpose=payload.purpose,
        workout_id=payload.workout_id,
        structure=(
            None if payload.structure is None else structure_document(payload.structure)
        ),
        intent_text=payload.intent_text,
        coach_notes=payload.coach_notes,
        success_criteria=(
            None
            if payload.success_criteria is None
            else [
                criterion.model_dump(mode="json", exclude_none=True)
                for criterion in payload.success_criteria
            ]
        ),
    )
    return to_read(row)


@router.get("/{planned_session_id}", responses=NOT_FOUND)
async def get_planned_session(
    service: ServiceDep, planned_session_id: uuid.UUID
) -> PlannedSessionRead:
    """Get one planned session with the intent version in force."""
    return to_read(await service.get(planned_session_id))


@router.patch("/{planned_session_id}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def update_planned_session(
    service: ServiceDep,
    actor: ActorDep,
    planned_session_id: uuid.UUID,
    payload: PlannedSessionUpdate,
) -> PlannedSessionRead:
    """Update a planned session, appending an intent version if intent changed.

    Editing intent before the session has been matched re-pins its anchors;
    editing it afterwards keeps the pins, flags the new version
    `edited_post_hoc`, and triggers a rescore (build-plan invariant 4).
    """
    updates = payload.model_dump(exclude_unset=True)
    if payload.structure is not None:
        updates["structure"] = structure_document(payload.structure)
    if payload.success_criteria is not None:
        updates["success_criteria"] = [
            criterion.model_dump(mode="json", exclude_none=True)
            for criterion in payload.success_criteria
        ]
    row = await service.update(planned_session_id, updates, actor=actor)
    return to_read(row)


@router.post("/{planned_session_id}/move", responses=NOT_FOUND | BAD_BODY | INVALID)
async def move_planned_session(
    service: ServiceDep,
    actor: ActorDep,
    planned_session_id: uuid.UUID,
    payload: PlannedSessionMove,
) -> PlannedSessionRead:
    """Move a planned session to another date.

    Its own verb rather than `PATCH ... {"date": ...}`, which does the same
    thing: dragging a card across the calendar is one intention, and the audit
    trail should be able to say so (D56). Nothing about the prescription
    changes — no intent version, no re-pinning.
    """
    return to_read(
        await service.move(planned_session_id, date=payload.date, actor=actor)
    )


@router.post(
    "/{planned_session_id}/copy",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | BAD_BODY | INVALID,
)
async def copy_planned_session(
    service: ServiceDep,
    actor: ActorDep,
    planned_session_id: uuid.UUID,
    payload: PlannedSessionCopy,
) -> PlannedSessionRead:
    """Copy a planned session onto another date.

    The copy is a **new** planned session: status `planned`, its own intent
    chain starting at version 1, and its anchors pinned at the versions in
    force now — a prescription freezes when it is planned, and this one is
    being planned now (invariant 4, D57).
    """
    return to_read(
        await service.copy(planned_session_id, date=payload.date, actor=actor)
    )


@router.delete(
    "/{planned_session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def delete_planned_session(
    service: ServiceDep, actor: ActorDep, planned_session_id: uuid.UUID
) -> None:
    """Remove a planned session and its whole intent chain."""
    await service.delete(planned_session_id, actor=actor)


@router.get("/{planned_session_id}/intents", responses=NOT_FOUND)
async def list_planned_session_intents(
    service: ServiceDep, planned_session_id: uuid.UUID
) -> SessionIntentsRead:
    """List every intent version of one session, oldest first.

    Unpaged: intent versions are the history of one session's prescription,
    and there are a handful even for a much-edited one.
    """
    intents = await service.intents(planned_session_id)
    return SessionIntentsRead(items=[intent_to_read(intent) for intent in intents])


@router.get("/{planned_session_id}/intents/{version}", responses=NOT_FOUND)
async def get_planned_session_intent(
    service: ServiceDep, planned_session_id: uuid.UUID, version: int
) -> SessionIntentRead:
    """Get one intent version — what was prescribed at that point."""
    return intent_to_read(await service.intent(planned_session_id, version))
