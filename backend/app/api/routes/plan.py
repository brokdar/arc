"""HTTP endpoints for the plan as a calendar. Thin layer over the service.

`/plan` is a view onto planned sessions, not a second collection of them:
nothing here creates, edits or deletes anything, and the projection it serves
is assembled in `app.services.plan` so WP-8's MCP tools can show the agent the
same week the athlete is looking at.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.schemas.plan import PlanWeekRead
from app.persistence.db import SessionDep
from app.services.plan import PlanService

router = APIRouter(prefix="/plan", tags=["plan"])


def get_service(session: SessionDep) -> PlanService:
    """Bind the service to a request-scoped session."""
    return PlanService.from_session(session)


ServiceDep = Annotated[PlanService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — a query string
# delivers `?x=null` as the four-letter string, which the parser refuses.
WeekStart = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(
        description=(
            "First athlete-local date of the seven-day window. Taken "
            "literally, not snapped to a Monday. Defaults to the Monday of "
            "the current week."
        )
    ),
]


@router.get("/week")
async def get_plan_week(service: ServiceDep, start: WeekStart = None) -> PlanWeekRead:
    """Get seven consecutive days of the plan, empty days included.

    Each session is summarized for a calendar card; its full detail — step
    tree, criteria, pins, intent history — stays at
    `GET /api/v1/planned-sessions/{id}`.
    """
    return PlanWeekRead.model_validate(await service.week(start))
