"""HTTP endpoints for plans. Thin layer over `app.services.plans`.

A week's tags are a sub-resource of one plan (`/plans/{id}/weeks/{monday}`),
one segment deeper than the id route, so nothing collides
(`.claude/rules/api-collection-facets.md`).
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import ActorDep
from app.api.schemas.plans import (
    PlanCreate,
    PlanRead,
    PlanUpdate,
    PlanWeekTagsRead,
    PlanWeekTagsWrite,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.plans import PlansService

router = APIRouter(prefix="/plans", tags=["plans"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such plan"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": "The plan would overlap another active plan",
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Plan violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> PlansService:
    """Bind the service to a request-scoped session."""
    return PlansService.from_session(session)


ServiceDep = Annotated[PlansService, Depends(get_service)]


@router.get("")
async def list_plans(service: ServiceDep) -> list[PlanRead]:
    """Every plan, oldest first, the default plan included."""
    return [PlanRead.model_validate(row) for row in await service.list()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=BAD_BODY | CONFLICT | INVALID,
)
async def create_plan(
    service: ServiceDep, actor: ActorDep, payload: PlanCreate
) -> PlanRead:
    """Create a plan. An active plan may not overlap another active one."""
    row = await service.create(actor=actor, **payload.model_dump())
    return PlanRead.model_validate(row)


@router.get("/{plan_id}", responses=NOT_FOUND)
async def get_plan(service: ServiceDep, plan_id: uuid.UUID) -> PlanRead:
    """One plan."""
    return PlanRead.model_validate(await service.get(plan_id))


@router.patch("/{plan_id}", responses=NOT_FOUND | BAD_BODY | CONFLICT | INVALID)
async def update_plan(
    service: ServiceDep, actor: ActorDep, plan_id: uuid.UUID, payload: PlanUpdate
) -> PlanRead:
    """Change a plan's name, phase, follow-exactly flag, activity or dates."""
    row = await service.update(
        plan_id, payload.model_dump(exclude_unset=True), actor=actor
    )
    return PlanRead.model_validate(row)


@router.get("/{plan_id}/weeks/{week_start}", responses=NOT_FOUND | INVALID)
async def get_plan_week_tags(
    service: ServiceDep, plan_id: uuid.UUID, week_start: dt.date
) -> PlanWeekTagsRead:
    """The tags on one week of one plan; ``week_start`` is its Monday."""
    tags = await service.week_tags(plan_id, week_start)
    return PlanWeekTagsRead(plan_id=plan_id, week_start=week_start, tags=list(tags))


@router.put("/{plan_id}/weeks/{week_start}", responses=NOT_FOUND | BAD_BODY | INVALID)
async def set_plan_week_tags(
    service: ServiceDep,
    actor: ActorDep,
    plan_id: uuid.UUID,
    week_start: dt.date,
    payload: PlanWeekTagsWrite,
) -> PlanWeekTagsRead:
    """Replace one week's tags; ``[]`` clears them."""
    tags = await service.set_week_tags(plan_id, week_start, payload.tags, actor=actor)
    return PlanWeekTagsRead(plan_id=plan_id, week_start=week_start, tags=list(tags))
