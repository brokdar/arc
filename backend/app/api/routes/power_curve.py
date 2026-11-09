"""A session's power curve: `GET /sessions/{id}/power-curve`.

Its own module rather than another route in `routes/activity.py`, which is the
session resource's CRUD; this is one derived read hanging off it. A
sub-resource of one member, so it sits under `/sessions/{id}` without the
facet collision `.claude/rules/api-collection-facets.md` describes.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.schemas.power_curve import (
    AssessedPowerCurveRead,
    NotAssessedPowerCurveRead,
    PowerCurvePointRead,
    PowerCurveRead,
)
from app.core.exceptions import ErrorDetail
from app.persistence.db import SessionDep
from app.services.power_curve import PowerCurveService, SessionPowerCurve

router = APIRouter(prefix="/sessions", tags=["sessions"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorDetail, "description": "No such session"}
}


def get_power_curves(session: SessionDep) -> PowerCurveService:
    """Bind the power-curve service to a request-scoped session."""
    return PowerCurveService.from_session(session)


PowerCurvesDep = Annotated[PowerCurveService, Depends(get_power_curves)]


def to_read(answer: SessionPowerCurve) -> PowerCurveRead:
    """Project the service's answer onto the response schema."""
    if answer.curve is None:
        return NotAssessedPowerCurveRead(reason=answer.reason or "")
    return AssessedPowerCurveRead(
        grid_version=answer.curve.grid_version,
        points=[
            PowerCurvePointRead(
                duration_s=point.duration_s,
                watts=point.watts,
                start_index=point.start_index,
                clean=point.clean,
            )
            for point in answer.curve.points
        ],
    )


@router.get("/{session_id}/power-curve", responses=NOT_FOUND)
async def get_power_curve(
    power_curves: PowerCurvesDep, session_id: uuid.UUID
) -> PowerCurveRead:
    """One session's best mean power for every duration, from 1 s upwards.

    The grid is fixed (`grid_version`): every second below 2 min, then
    progressively coarser steps, up to the ride's own length and never past
    it. A session with no power meter — or one recorded before curves
    existed — answers `not_assessed` with the reason, never zeros. 404 for an
    unknown session.
    """
    return to_read(await power_curves.read(session_id))
