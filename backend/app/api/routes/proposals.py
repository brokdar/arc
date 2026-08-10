"""HTTP endpoints for plan-change proposals. Thin layer over the service.

The athlete's half of invariant 6: an inbox and two answers. There is **no
create endpoint** — proposals are what the coaching agent suggests, and the way
in is the MCP tool over `app.services.proposals`, the same service these
routes call. An athlete who wants to change the plan changes the plan
(`/planned-sessions`); proposing to oneself is not a workflow.

Accepting is a `POST` to a sub-resource rather than a `PATCH` setting a status,
for the reason `move` is its own verb (D56): accepting applies a set of plan
changes in one transaction, and that is an action, not a field.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.proposals import (
    ProposalRead,
    ProposalReject,
    ProposalsPage,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.proposals import ProposalStatus
from app.persistence.db import SessionDep
from app.services.proposals import ProposalService

router = APIRouter(prefix="/proposals", tags=["proposals"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such proposal"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": (
            "The proposal is no longer pending, has expired, or the plan has "
            "moved on since it was written"
        ),
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The answer violates a schema or domain rule",
    }
}


def get_service(session: SessionDep) -> ProposalService:
    """Bind the service to a request-scoped session."""
    return ProposalService.from_session(session)


ServiceDep = Annotated[ProposalService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`. Aliased because `status` is
# conventionally `fastapi.status` in a route module; clients see `?status=`.
StatusFilter = Annotated[
    ProposalStatus | SkipJsonSchema[None],
    Query(alias="status", description="Restrict to one status; omit for all of them."),
]


@router.get("")
async def list_proposals(
    service: ServiceDep,
    page: PageParamsDep,
    proposal_status: StatusFilter = None,
) -> ProposalsPage:
    """List proposals, newest first.

    Newest first because this is an inbox: the thing to answer is the thing
    that just arrived. Filter with `?status=pending` for the ones still
    standing.
    """
    proposals, total = await service.list(
        status=proposal_status, offset=page.offset, limit=page.limit
    )
    return ProposalsPage(
        items=[ProposalRead.model_validate(row) for row in proposals],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{proposal_id}", responses=NOT_FOUND)
async def get_proposal(service: ServiceDep, proposal_id: uuid.UUID) -> ProposalRead:
    """Get one proposal with its rationale and its computed diff."""
    return ProposalRead.model_validate(await service.get(proposal_id))


@router.post("/{proposal_id}/accept", responses=NOT_FOUND | CONFLICT)
async def accept_proposal(
    service: ServiceDep, actor: ActorDep, proposal_id: uuid.UUID
) -> ProposalRead:
    """Apply every change in a proposal, atomically.

    The concurrency tokens are re-checked first: a proposal can stand for days
    and the plan is the athlete's to edit meanwhile, so one whose session has
    been revised since is refused with a 409 and **stays pending** — it may
    still be a good suggestion against the plan as it now stands.
    """
    return ProposalRead.model_validate(await service.accept(proposal_id, actor=actor))


@router.post(
    "/{proposal_id}/reject", responses=NOT_FOUND | CONFLICT | BAD_BODY | INVALID
)
async def reject_proposal(
    service: ServiceDep,
    actor: ActorDep,
    proposal_id: uuid.UUID,
    payload: ProposalReject,
) -> ProposalRead:
    """Decline a proposal, optionally saying why. Changes no plan."""
    return ProposalRead.model_validate(
        await service.reject(proposal_id, actor=actor, reason=payload.reason)
    )
