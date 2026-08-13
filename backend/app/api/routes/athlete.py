"""HTTP endpoints for the athlete profile. Thin layer over the service.

Singular path (`/athlete`, not `/athletes/{id}`): there is exactly one
athlete and no user table, so a collection and an id in the URL would be
inventing a plurality the application does not have.

This router carries no auth dependency of its own: `app.main` mounts it on the
protected `/api/v1` router.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep
from app.api.schemas.athlete import AthleteRead, AthleteUpdate
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.athlete import AthleteService

router = APIRouter(prefix="/athlete", tags=["athlete"])

type Responses = dict[int | str, dict[str, Any]]
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Profile violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> AthleteService:
    """Bind the service to a request-scoped session."""
    return AthleteService.from_session(session)


ServiceDep = Annotated[AthleteService, Depends(get_service)]


@router.get("")
async def get_athlete(service: ServiceDep, actor: ActorDep) -> AthleteRead:
    """Get the athlete profile.

    Creates it, empty, on the first call — the profile is bootstrapped
    lazily rather than seeded by a migration, so this read takes an actor and
    audits that one write.
    """
    return AthleteRead.model_validate(await service.get(actor=actor))


@router.patch("", responses=BAD_BODY | INVALID)
async def update_athlete(
    service: ServiceDep, actor: ActorDep, payload: AthleteUpdate
) -> AthleteRead:
    """Partially update the athlete profile."""
    athlete = await service.update(payload.model_dump(exclude_unset=True), actor=actor)
    return AthleteRead.model_validate(athlete)
