"""HTTP endpoints for the exercise catalogue. Read-only.

There is no POST, PATCH or DELETE here and there is no repository method for
one either: the catalogue is bundled reference data, seeded from
`app/resources/exercise_catalogue.json` on first access. Adding a movement is
a reviewed change to that file, not a runtime write — which is what keeps
every deployment's slugs identical, and therefore keeps a stored prescription
readable anywhere.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.pagination import PageParamsDep
from app.api.schemas.exercises import ExerciseRead, ExercisesPage
from app.api.validation import PostgresText
from app.core.exceptions import ErrorDetail
from app.domain.strength import ExerciseCategory
from app.persistence.db import SessionDep
from app.services.exercises import ExerciseService

router = APIRouter(prefix="/exercises", tags=["exercises"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such exercise"}}


def get_service(session: SessionDep) -> ExerciseService:
    """Bind the service to a request-scoped session."""
    return ExerciseService.from_session(session)


ServiceDep = Annotated[ExerciseService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`.
CategoryFilter = Annotated[
    ExerciseCategory | SkipJsonSchema[None],
    Query(description="Restrict to one movement family; omit for all of them."),
]
SearchQuery = Annotated[
    PostgresText | SkipJsonSchema[None],
    Query(max_length=100, description="Case-insensitive substring of the name."),
]


@router.get("")
async def list_exercises(
    service: ServiceDep,
    page: PageParamsDep,
    category: CategoryFilter = None,
    q: SearchQuery = None,
) -> ExercisesPage:
    """List catalogue exercises, by family then name."""
    exercises, total = await service.list(
        category=category, query=q, offset=page.offset, limit=page.limit
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
