"""HTTP endpoints for the athlete's training settings. Thin layer over the service.

`/settings/training` rather than a field group on `/athlete`: these are how arc
presents itself, not profile data (see `app.persistence.athlete_settings`).
Mounted on the protected `/api/v1` router in `app.main`.
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep
from app.api.schemas.settings_training import (
    TrainingSettingsRead,
    TrainingSettingsUpdate,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.athlete_settings import AthleteSettingsService

router = APIRouter(prefix="/settings/training", tags=["settings"])

type Responses = dict[int | str, dict[str, Any]]
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Unknown setting, value outside its vocabulary, or null",
    }
}


def get_service(session: SessionDep) -> AthleteSettingsService:
    """Bind the service to a request-scoped session."""
    return AthleteSettingsService.from_session(session)


ServiceDep = Annotated[AthleteSettingsService, Depends(get_service)]


@router.get("")
async def get_training_settings(service: ServiceDep) -> TrainingSettingsRead:
    """Get the training settings — the defaults when none are stored."""
    return TrainingSettingsRead(**asdict(await service.read()))


@router.patch("", responses=BAD_BODY | INVALID)
async def update_training_settings(
    service: ServiceDep, actor: ActorDep, payload: TrainingSettingsUpdate
) -> TrainingSettingsRead:
    """Partially update the training settings. An empty body changes nothing."""
    settings = await service.update(payload.model_dump(exclude_unset=True), actor=actor)
    return TrainingSettingsRead(**asdict(settings))
