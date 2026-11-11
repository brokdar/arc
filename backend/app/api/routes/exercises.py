"""HTTP endpoints for the exercise catalogue and the athlete's notes on it.

The catalogue is read-only: there is no POST, PATCH or DELETE for an exercise
and no repository method for one either. It is bundled reference data, seeded
from `app/resources/exercise_catalogue.json` on first access. Adding a
movement is a reviewed change to that file, not a runtime write — which is
what keeps every deployment's slugs identical, and therefore keeps a stored
prescription readable anywhere.

The note is a sub-resource of one exercise (`/exercises/{id}/note`), one per
exercise: `PUT` replaces it, and an empty text deletes it.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.exercises import (
    ExerciseNoteRead,
    ExerciseNoteWrite,
    ExerciseRead,
    ExercisesPage,
)
from app.api.schemas.strength_history import ExerciseHistoryRead, to_history_read
from app.api.validation import PostgresText
from app.core.exceptions import ErrorDetail
from app.domain.muscles import MuscleGroup
from app.domain.strength import (
    ExerciseCategory,
    ExerciseEquipment,
    Mechanic,
    MovementPattern,
)
from app.persistence.db import SessionDep
from app.services.exercises import ExerciseService
from app.services.strength_history import StrengthHistoryService

router = APIRouter(prefix="/exercises", tags=["exercises"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such exercise"}}
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}


def get_service(session: SessionDep) -> ExerciseService:
    """Bind the service to a request-scoped session."""
    return ExerciseService.from_session(session)


ServiceDep = Annotated[ExerciseService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — a query string
# delivers `?x=null` as the four-letter string, which the parser refuses.
CategoryFilter = Annotated[
    ExerciseCategory | SkipJsonSchema[None],
    Query(description="Restrict to one movement family; omit for all of them."),
]
SearchQuery = Annotated[
    PostgresText | SkipJsonSchema[None],
    Query(max_length=100, description="Case-insensitive substring of the name."),
]


GroupFilter = Annotated[
    MuscleGroup | SkipJsonSchema[None],
    Query(description="Only exercises whose main muscles roll up to this group."),
]
PatternFilter = Annotated[
    MovementPattern | SkipJsonSchema[None],
    Query(description="Only exercises with this movement pattern."),
]
EquipmentFilter = Annotated[
    ExerciseEquipment | SkipJsonSchema[None],
    Query(description="Only exercises whose equipment includes this item."),
]
MechanicFilter = Annotated[
    Mechanic | SkipJsonSchema[None],
    Query(description="Only multi-joint or only single-joint exercises."),
]


@router.get("")
async def list_exercises(
    service: ServiceDep,
    page: PageParamsDep,
    category: CategoryFilter = None,
    q: SearchQuery = None,
    group: GroupFilter = None,
    pattern: PatternFilter = None,
    equipment: EquipmentFilter = None,
    mechanic: MechanicFilter = None,
) -> ExercisesPage:
    """List catalogue exercises, by family then name; every filter narrows."""
    exercises, total = await service.list(
        category=category,
        query=q,
        group=group,
        pattern=pattern,
        equipment=equipment,
        mechanic=mechanic,
        offset=page.offset,
        limit=page.limit,
    )
    return ExercisesPage(
        items=[ExerciseRead.model_validate(exercise) for exercise in exercises],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{exercise_id}", responses=NOT_FOUND)
async def get_exercise(service: ServiceDep, exercise_id: str) -> ExerciseRead:
    """Get one catalogue exercise by its slug."""
    return ExerciseRead.model_validate(await service.get(exercise_id))


@router.get("/{exercise_id}/history", responses=NOT_FOUND)
async def get_exercise_history(
    session: SessionDep, exercise_id: str
) -> ExerciseHistoryRead:
    """Personal bests, heaviest-for-N-reps and the estimated-max line of one exercise.

    Derived from every logged set on each read. An exercise never logged
    answers empty lists, not 404; a slug outside the catalogue is 404.
    """
    service = StrengthHistoryService.from_session(session)
    return to_history_read(await service.exercise_history(exercise_id))


@router.get("/{exercise_id}/note", responses=NOT_FOUND)
async def get_exercise_note(service: ServiceDep, exercise_id: str) -> ExerciseNoteRead:
    """Get the athlete's note on an exercise; empty text when there is none."""
    return ExerciseNoteRead.model_validate(await service.get_note(exercise_id))


@router.put("/{exercise_id}/note", responses=BAD_BODY | NOT_FOUND)
async def put_exercise_note(
    service: ServiceDep, actor: ActorDep, exercise_id: str, body: ExerciseNoteWrite
) -> ExerciseNoteRead:
    """Replace the note on an exercise; an empty text deletes it."""
    return ExerciseNoteRead.model_validate(
        await service.set_note(exercise_id, body.text, actor=actor)
    )
