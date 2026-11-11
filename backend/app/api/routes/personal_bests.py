"""`GET /personal-bests`: the new bests of one session, for the Finish screen.

A top-level resource keyed by the phone's session id rather than a
sub-resource of `/sessions/{id}`: the Finish screen asks right after its
outbox flushes, and the phone never learns the server's id for the session it
started offline. An exercise's whole history is `GET /exercises/{id}/history`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.schemas.strength_history import SessionBestsRead, to_session_bests_read
from app.core.exceptions import ErrorDetail
from app.persistence.db import SessionDep
from app.services.strength_history import StrengthHistoryService

router = APIRouter(prefix="/personal-bests", tags=["strength"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "No session with that phone id"}
}


@router.get("", responses=NOT_FOUND)
async def get_session_bests(
    session: SessionDep,
    session_client_id: Annotated[
        uuid.UUID, Query(description="The session id the phone minted at Start.")
    ],
) -> SessionBestsRead:
    """New personal bests set in one session; an empty list when there were none."""
    service = StrengthHistoryService.from_session(session)
    return to_session_bests_read(await service.session_bests(session_client_id))
