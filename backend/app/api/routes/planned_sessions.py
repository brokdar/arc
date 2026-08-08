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
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.routes.matching import to_summary
from app.api.schemas.planned_sessions import (
    MetricExplanationRead,
    PinnedAnchorRead,
    PlannedSessionCopy,
    PlannedSessionCreate,
    PlannedSessionListItem,
    PlannedSessionMove,
    PlannedSessionRead,
    PlannedSessionsPage,
    PlannedSessionUpdate,
    PredictedLoadRead,
    PredictedVolumeRead,
    ResolvedStepRead,
    ResolvedTargetRead,
    SessionIntentRead,
    SessionIntentsRead,
)
from app.api.schemas.workouts import (
    STRUCTURE_ADAPTER,
    WorkoutSummarySchema,
    structure_document,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.anchors import AnchorType
from app.domain.prediction import PinnedAnchor, PredictedLoad, PredictedVolume
from app.domain.resolution import ResolvedStep, ResolvedTarget
from app.domain.sessions import SessionStatus
from app.domain.workout import workout_body_from_json
from app.persistence.db import SessionDep
from app.persistence.matching import SessionMatchRow
from app.persistence.planned_sessions import (
    PlannedSessionIntentRow,
    PlannedSessionRow,
)
from app.services.matching import MatchingService
from app.services.planned_sessions import PlannedSessionService, SessionResolution
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


def get_matching(session: SessionDep) -> MatchingService:
    """Bind the matching service to a request-scoped session."""
    return MatchingService.from_session(session)


ServiceDep = Annotated[PlannedSessionService, Depends(get_service)]
MatchingDep = Annotated[MatchingService, Depends(get_matching)]

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


def _target_to_read(target: ResolvedTarget) -> ResolvedTargetRead:
    """Project one resolved target onto its response shape."""
    return ResolvedTargetRead(
        channel=target.channel,
        prescribed=target.prescribed,
        resolved_low=target.resolved_low,
        resolved_high=target.resolved_high,
        unit=target.unit,
        anchor_version_id=target.anchor_version_id,
    )


def _step_to_read(step: ResolvedStep) -> ResolvedStepRead:
    """Project one resolved flat step onto its response shape."""
    return ResolvedStepRead(
        index=step.index,
        role=step.role,
        name=step.name,
        duration_s=step.duration_s,
        distance_m=step.distance_m,
        is_ramp=step.is_ramp,
        start_targets=[_target_to_read(target) for target in step.start_targets],
        end_targets=[_target_to_read(target) for target in step.end_targets],
    )


def _pins_to_read(
    anchors: Mapping[AnchorType, PinnedAnchor],
) -> list[PinnedAnchorRead]:
    """Project the pinned anchor versions, in anchor-type order."""
    return [
        PinnedAnchorRead(
            anchor_type=anchor_type,
            anchor_version_id=pinned.version_id,
            value=pinned.version.value,
            unit=pinned.version.unit,
            provenance=pinned.version.provenance,
            effective_date=pinned.version.effective_date,
        )
        for anchor_type, pinned in sorted(
            anchors.items(), key=lambda item: item[0].value
        )
    ]


def _load_to_read(predicted: PredictedLoad | None) -> PredictedLoadRead | None:
    """Project a predicted load, explanation and all."""
    if predicted is None:
        return None
    return PredictedLoadRead(
        load=predicted.load,
        intensity_factor=predicted.intensity_factor,
        coverage=predicted.coverage,
        anchor_version_id=predicted.anchor_version_id,
        explanation=MetricExplanationRead(
            formula=predicted.explanation.formula,
            inputs=dict(predicted.explanation.inputs),
            assumptions=list(predicted.explanation.assumptions),
            citation=predicted.explanation.citation,
        ),
    )


def _volume_to_read(predicted: PredictedVolume | None) -> PredictedVolumeRead | None:
    """Project a predicted strength volume. Kilograms, never a load."""
    if predicted is None:
        return None
    return PredictedVolumeRead(
        volume_load_kg=predicted.volume_load_kg,
        total_sets=predicted.total_sets,
        coverage=predicted.coverage,
    )


def to_list_item(
    row: PlannedSessionRow,
    anchors: Mapping[AnchorType, PinnedAnchor],
    link: SessionMatchRow | None = None,
) -> PlannedSessionListItem:
    """Project a stored planned session onto its list-row shape (D79)."""
    return PlannedSessionListItem(
        match=to_summary(link) if link is not None else None,
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        status=row.status,
        intent=intent_to_read(row.current_intent),
        intent_versions=len(row.intents),
        created_at=row.created_at,
        updated_at=row.updated_at,
        pinned_anchors=_pins_to_read(anchors),
    )


def to_read(
    row: PlannedSessionRow,
    resolution: SessionResolution,
    link: SessionMatchRow | None = None,
) -> PlannedSessionRead:
    """Project a stored planned session onto its response shape."""
    return PlannedSessionRead(
        match=to_summary(link) if link is not None else None,
        id=row.id,
        date=row.date,
        discipline=row.discipline,
        status=row.status,
        intent=intent_to_read(row.current_intent),
        intent_versions=len(row.intents),
        created_at=row.created_at,
        updated_at=row.updated_at,
        pinned_anchors=_pins_to_read(resolution.anchors),
        resolved_steps=[_step_to_read(step) for step in resolution.steps],
        predicted_load=_load_to_read(resolution.predicted_load),
        predicted_volume=_volume_to_read(resolution.predicted_volume),
    )


async def one_to_read(
    service: PlannedSessionService,
    matching: MatchingService,
    row: PlannedSessionRow,
) -> PlannedSessionRead:
    """Resolve one session's pins and its match link, and project it.

    Every endpoint that answers with a whole session goes through here, so the
    resolved targets, the predicted load and the recorded session that answered
    it are part of the resource rather than something only the detail route
    happens to include.
    """
    resolutions = await service.resolutions([row])
    links = await matching.for_planned_sessions([row.id])
    return to_read(row, resolutions[row.id], links.get(row.id))


@router.get("")
async def list_planned_sessions(
    service: ServiceDep,
    matching: MatchingDep,
    page: PageParamsDep,
    start: StartFilter = None,
    end: EndFilter = None,
    session_status: StatusFilter = None,
) -> PlannedSessionsPage:
    """List planned sessions in date order, optionally within a date range.

    A list row is lighter than the session it names (D79): no resolved step
    tree and no predicted-load explanation, because a page of two hundred
    sessions carrying either is measured in megabytes and in seconds of
    synchronous CPU. The pins stay — they are one query for the whole page —
    and the whole session is one request away at
    `GET /planned-sessions/{id}`.
    """
    sessions, total = await service.list(
        start=start,
        end=end,
        status=session_status,
        offset=page.offset,
        limit=page.limit,
    )
    # One query for the whole page's pins, not one per session — and no
    # prescription parsed, no 1 Hz expansion run.
    anchors = await service.pins(sessions)
    links = await matching.for_planned_sessions([session.id for session in sessions])
    return PlannedSessionsPage(
        items=[
            to_list_item(session, anchors[session.id], links.get(session.id))
            for session in sessions
        ],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID | NOT_FOUND
)
async def create_planned_session(
    service: ServiceDep,
    matching: MatchingDep,
    actor: ActorDep,
    payload: PlannedSessionCreate,
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
    return await one_to_read(service, matching, row)


@router.get("/{planned_session_id}", responses=NOT_FOUND)
async def get_planned_session(
    service: ServiceDep, matching: MatchingDep, planned_session_id: uuid.UUID
) -> PlannedSessionRead:
    """Get one planned session with the intent version in force."""
    return await one_to_read(service, matching, await service.get(planned_session_id))


@router.patch("/{planned_session_id}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def update_planned_session(
    service: ServiceDep,
    matching: MatchingDep,
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
    return await one_to_read(service, matching, row)


@router.post("/{planned_session_id}/move", responses=NOT_FOUND | BAD_BODY | INVALID)
async def move_planned_session(
    service: ServiceDep,
    matching: MatchingDep,
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
    return await one_to_read(
        service,
        matching,
        await service.move(planned_session_id, date=payload.date, actor=actor),
    )


@router.post(
    "/{planned_session_id}/copy",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | BAD_BODY | INVALID,
)
async def copy_planned_session(
    service: ServiceDep,
    matching: MatchingDep,
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
    return await one_to_read(
        service,
        matching,
        await service.copy(planned_session_id, date=payload.date, actor=actor),
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
