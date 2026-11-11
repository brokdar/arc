"""HTTP endpoints for swaps and the athlete's preferences per exercise.

Thin layer over `app.services.exercise_preferences`. Swaps and preferences are
sub-resources of one exercise (`/exercises/{id}/substitutes`,
`/exercises/{id}/preferences`); the list of every exercise with something
stated is a facet of the whole collection, so it lives outside the id
namespace at `/exercise-preferences` (`.claude/rules/api-collection-facets.md`).
Advancing *Ease back in* is a ``POST`` to a sub-resource: it is the athlete's
tap, an action, not a field anyone may set to any value.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep
from app.api.schemas.exercise_preferences import (
    ExercisePreferenceRead,
    ExercisePreferenceUpdate,
    SubstitutesRead,
    preference_read,
    swap_read,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.exercise_preferences import ExercisePreferencesService

router = APIRouter(prefix="/exercises", tags=["exercises"])
list_router = APIRouter(prefix="/exercise-preferences", tags=["exercises"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such exercise"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": "Not easing back in, or nothing easier to ease back in on",
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "An exercise named as its own swap",
    }
}


def get_service(session: SessionDep) -> ExercisePreferencesService:
    """Bind the service to a request-scoped session."""
    return ExercisePreferencesService.from_session(session)


ServiceDep = Annotated[ExercisePreferencesService, Depends(get_service)]


@router.get("/{exercise_id}/substitutes", responses=NOT_FOUND)
async def get_substitutes(service: ServiceDep, exercise_id: str) -> SubstitutesRead:
    """Up to three swaps for an exercise, best first — the packet's three.

    Never an avoided movement, one needing equipment the gym lacks, or one
    making a demand recent sharp pain rules out; fewer than three when fewer
    qualify. The athlete's remembered pick comes first.
    """
    rows = await service.substitutes(exercise_id)
    return SubstitutesRead(
        exercise_id=exercise_id, substitutes=[swap_read(row) for row in rows]
    )


@router.get("/{exercise_id}/preferences", responses=NOT_FOUND)
async def get_preferences(
    service: ServiceDep, exercise_id: str
) -> ExercisePreferenceRead:
    """The athlete's preferences on one exercise; defaults when none were stated."""
    return preference_read(await service.get(exercise_id))


@router.patch(
    "/{exercise_id}/preferences",
    responses=BAD_BODY | NOT_FOUND | CONFLICT | INVALID,
)
async def update_preferences(
    service: ServiceDep,
    actor: ActorDep,
    exercise_id: str,
    body: ExercisePreferenceUpdate,
) -> ExercisePreferenceRead:
    """Change some of one exercise's preferences; omitted fields are left alone."""
    changes = body.model_dump(exclude_unset=True)
    return preference_read(await service.update(exercise_id, changes, actor=actor))


@router.post(
    "/{exercise_id}/preferences/ease-back/advance",
    responses=NOT_FOUND | CONFLICT,
)
async def advance_ease_back(
    service: ServiceDep, actor: ActorDep, exercise_id: str
) -> ExercisePreferenceRead:
    """The athlete's tap: one step up *Ease back in*; past the last, it ends."""
    return preference_read(await service.advance_ease_back(exercise_id, actor=actor))


@list_router.get("")
async def list_preferences(service: ServiceDep) -> list[ExercisePreferenceRead]:
    """Every exercise the athlete has stated a preference on, by slug."""
    return [preference_read(p) for p in await service.list()]
