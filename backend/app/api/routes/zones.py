"""HTTP endpoints for computed zones. Thin layer over the service.

Zones are a derived view with no identity of their own, so they are addressed
by *what they are derived from*, and there are two of those:

* ``GET /zones?anchor_type=ftp`` — from whatever version is in force now.
  How a chart asks.
* ``GET /anchors/{id}/zones`` — from one specific, frozen version. How a
  stored prescription asks, and the reason it stays reproducible.

Two endpoints rather than one with two mutually exclusive query parameters:
an API that can express a request with no meaning has to reject it at runtime,
and the rejection is invisible in the contract.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.schemas.zones import ZonesRead
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.anchors import AnchorType
from app.domain.zones import ZoneModel
from app.persistence.db import SessionDep
from app.services.zones import ZoneService

router = APIRouter(tags=["zones"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {
        "model": ErrorDetail,
        "description": "No such anchor version, or none in force",
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "No zone model derives from this anchor type",
    }
}


def get_service(session: SessionDep) -> ZoneService:
    """Bind the service to a request-scoped session."""
    return ZoneService.from_session(session)


ServiceDep = Annotated[ZoneService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission — advertising `null` would
# promise a value the query-string enum parser rejects (see the same pattern
# in `app.api.routes.anchors`).
ZoneModelQuery = Annotated[
    ZoneModel | SkipJsonSchema[None],
    Query(description="Defaults to the model that derives from the anchor type."),
]


@router.get("/zones", responses=NOT_FOUND | INVALID)
async def get_zones(
    service: ServiceDep,
    anchor_type: Annotated[
        AnchorType, Query(description="Whose version in force to derive from.")
    ],
    zone_model: ZoneModelQuery = None,
) -> ZonesRead:
    """Compute zones from the anchor version currently in force."""
    resolved = await service.for_current_anchor(anchor_type, model=zone_model)
    return ZonesRead.model_validate(resolved)


@router.get("/anchors/{anchor_version_id}/zones", responses=NOT_FOUND | INVALID)
async def get_anchor_version_zones(
    service: ServiceDep,
    anchor_version_id: uuid.UUID,
    zone_model: ZoneModelQuery = None,
) -> ZonesRead:
    """Compute zones from one specific, frozen anchor version."""
    resolved = await service.for_version(anchor_version_id, model=zone_model)
    return ZonesRead.model_validate(resolved)
