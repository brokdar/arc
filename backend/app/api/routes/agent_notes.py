"""HTTP endpoints for agent notes. Thin layer over the service.

The athlete's half of invariant 7: read what the coach said, and answer it.
There is **no create endpoint**, for the same reason there is none for
proposals — a note is attributed to a model, and an athlete-authored note
signed by one would make every other note's attribution worthless. The way in
is the MCP tool over `app.services.agent_notes`, the same service these routes
call.

Disputing is a `POST` to a sub-resource rather than a `PATCH` setting a field
(D56, and the same shape as accepting a proposal): it is one tap that means
one thing, and the body carries only what that tap said.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.schemas.agent_notes import (
    AgentNoteDispute,
    AgentNoteRead,
    AgentNotesRead,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.agent_notes import AgentNoteService

router = APIRouter(prefix="/agent-notes", tags=["agent-notes"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such note"}}
FORBIDDEN: Responses = {
    403: {"model": ErrorDetail, "description": "Only the athlete rates a note"}
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The subject is not exactly one, or the week is not a Monday",
    }
}


def get_service(session: SessionDep) -> AgentNoteService:
    """Bind the service to a request-scoped session."""
    return AgentNoteService.from_session(session)


ServiceDep = Annotated[AgentNoteService, Depends(get_service)]

# `SkipJsonSchema[None]`: optional by omission, never `null` — see
# `.claude/rules/api-optional-query-params.md`.
SessionFilter = Annotated[
    uuid.UUID | SkipJsonSchema[None],
    Query(description="Notes about this recorded session."),
]
WeekFilter = Annotated[
    dt.date | SkipJsonSchema[None],
    Query(
        description=(
            "Notes about this plan week, given as the Monday it starts on. "
            "Exactly one of `session_id` and `week` is required."
        )
    ),
]


@router.get("", responses=INVALID)
async def list_agent_notes(
    service: ServiceDep,
    session_id: SessionFilter = None,
    week: WeekFilter = None,
) -> AgentNotesRead:
    """List the notes about one session or one plan week, oldest first.

    Oldest first because these are a conversation about a subject and read in
    the order they were written — unlike the proposal inbox, which is a queue
    of things to answer.

    One of `session_id` and `week` is required, and never both: a note has one
    subject, so a query with no subject has no answer and a query with two is
    two queries.
    """
    return AgentNotesRead(
        items=[
            AgentNoteRead.model_validate(row)
            for row in await service.list(session_id=session_id, plan_week=week)
        ]
    )


@router.post("/{note_id}/dispute", responses=NOT_FOUND | FORBIDDEN | INVALID)
async def dispute_agent_note(
    service: ServiceDep,
    actor: ActorDep,
    note_id: uuid.UUID,
    payload: AgentNoteDispute,
) -> AgentNoteRead:
    """Rate a note up or down, or clear the rating with `null`.

    Overwrites whatever was there: this is a toggle on a card, and an athlete
    who cannot take a rating back will stop giving them. What actually
    happened is kept in the audit log.
    """
    return AgentNoteRead.model_validate(
        await service.dispute(note_id, actor=actor, rating=payload.rating)
    )
