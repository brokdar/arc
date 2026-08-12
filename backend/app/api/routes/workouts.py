"""HTTP endpoints for the workout library. Thin layer over the service.

Ordinary CRUD, which is the point: nothing in the library is frozen or
versioned. A workout here is a *template* the athlete reuses; the copy that
must not change is the snapshot a planned session takes of it
(`app.services.planned_sessions`).

The folder and tag lists live at `/workout-labels`, a sibling of the
collection rather than `/workouts/folders`. Any single extra segment under
`/workouts` also matches `/workouts/{workout_id}`, so an undocumented method
on it (`PATCH /workouts/tags`) fell through to the id route and answered 422
about uuid syntax where 405 is the true answer — found by Schemathesis, and
the same class of mismatch already fixed for the append-only refusals. Moving the
path out of the id namespace removes the collision instead of papering over it
with more refusal handlers.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.workouts import (
    STRUCTURE_ADAPTER,
    WorkoutCreate,
    WorkoutLabelsRead,
    WorkoutRead,
    WorkoutsPage,
    WorkoutSummarySchema,
    WorkoutUpdate,
    structure_document,
)
from app.api.validation import PostgresText
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.athlete import Discipline
from app.persistence.db import SessionDep
from app.persistence.workouts import WorkoutRow
from app.services.workouts import WorkoutService, summarize

router = APIRouter(prefix="/workouts", tags=["workouts"])
#: Facets of the library as a whole, deliberately outside the id namespace.
labels_router = APIRouter(prefix="/workout-labels", tags=["workouts"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such workout"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Workout violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> WorkoutService:
    """Bind the service to a request-scoped session."""
    return WorkoutService.from_session(session)


ServiceDep = Annotated[WorkoutService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — a query string
# delivers `?x=null` as the four-letter string, which the parser refuses.
SearchQuery = Annotated[
    PostgresText | SkipJsonSchema[None],
    Query(
        max_length=200, description="Case-insensitive substring of name or description."
    ),
]
FolderFilter = Annotated[
    PostgresText | SkipJsonSchema[None],
    Query(max_length=200, description="Restrict to one folder label."),
]
TagFilter = Annotated[
    PostgresText | SkipJsonSchema[None],
    Query(max_length=60, description="Restrict to workouts carrying this tag."),
]
DisciplineFilter = Annotated[
    Discipline | SkipJsonSchema[None],
    Query(description="Restrict to one discipline; omit for both."),
]


def to_read(row: WorkoutRow) -> WorkoutRead:
    """Project a stored workout onto its response shape.

    The structure is re-validated on the way out and the summary is computed,
    never read: both are derived from the one document, so neither can go
    stale relative to it.
    """
    summary = summarize(row)
    return WorkoutRead(
        id=row.id,
        name=row.name,
        description=row.description,
        discipline=row.discipline,
        folder=row.folder,
        tags=row.tag_names,
        structure=STRUCTURE_ADAPTER.validate_python(row.structure),
        summary=WorkoutSummarySchema(
            step_count=summary.step_count,
            total_duration_s=summary.total_duration_s,
            total_sets=summary.total_sets,
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("")
async def list_workouts(
    service: ServiceDep,
    page: PageParamsDep,
    q: SearchQuery = None,
    folder: FolderFilter = None,
    tag: TagFilter = None,
    discipline: DisciplineFilter = None,
) -> WorkoutsPage:
    """List library workouts, newest first, with optional search and filters."""
    workouts, total = await service.list(
        query=q,
        folder=folder,
        tag=tag,
        discipline=discipline,
        offset=page.offset,
        limit=page.limit,
    )
    return WorkoutsPage(
        items=[to_read(workout) for workout in workouts],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID)
async def create_workout(
    service: ServiceDep, actor: ActorDep, payload: WorkoutCreate
) -> WorkoutRead:
    """Add a workout to the library."""
    row = await service.create(
        actor=actor,
        name=payload.name,
        structure=structure_document(payload.structure),
        description=payload.description,
        folder=payload.folder,
        tags=payload.tags,
    )
    return to_read(row)


@router.get("/{workout_id}", responses=NOT_FOUND)
async def get_workout(service: ServiceDep, workout_id: uuid.UUID) -> WorkoutRead:
    """Get one library workout by id."""
    return to_read(await service.get(workout_id))


@router.patch("/{workout_id}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def update_workout(
    service: ServiceDep,
    actor: ActorDep,
    workout_id: uuid.UUID,
    payload: WorkoutUpdate,
) -> WorkoutRead:
    """Partially update a library workout."""
    updates = payload.model_dump(exclude_unset=True)
    if "structure" in updates and payload.structure is not None:
        updates["structure"] = structure_document(payload.structure)
    row = await service.update(workout_id, updates, actor=actor)
    return to_read(row)


@router.delete(
    "/{workout_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_workout(
    service: ServiceDep, actor: ActorDep, workout_id: uuid.UUID
) -> None:
    """Remove a workout from the library.

    Planned sessions built from it keep their own frozen snapshot; only the
    provenance link is nulled.
    """
    await service.delete(workout_id, actor=actor)


@labels_router.get("")
async def list_workout_labels(service: ServiceDep) -> WorkoutLabelsRead:
    """List the folder labels and tags in use across the library.

    One call rather than two: the workout creator needs both at once, to offer
    what already exists instead of inviting a fourth spelling of "base".
    """
    return WorkoutLabelsRead(
        folders=list(await service.folders()), tags=list(await service.tags())
    )
