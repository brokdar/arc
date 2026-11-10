"""HTTP endpoints for suggestion cards. Thin layer over the service.

``GET /cards`` is the athlete's read and **stamps** what it returns as shown
(`CardService.show`): that is the moment the card is in front of them, so it
is the moment the budget counts and the seven-day expiry starts. It returns a
list for the client's convenience, and the list never holds more than one
card — and holds none while a session is active.

Answering is a ``POST`` to a sub-resource, like accepting a proposal: an
answer may run the kind's handler, and that is an action, not a field.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep
from app.api.schemas.cards import CardAct, CardRead
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.db import SessionDep
from app.services.cards import CardService

router = APIRouter(prefix="/cards", tags=["cards"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such card"}}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": "The card was already answered or has expired",
    }
}
INVALID: Responses = {
    422: {"model": ValidationErrorDetail, "description": "Malformed answer"}
}


def get_service(session: SessionDep) -> CardService:
    """Bind the service to a request-scoped session."""
    return CardService.from_session(session)


ServiceDep = Annotated[CardService, Depends(get_service)]


@router.get("")
async def list_cards(service: ServiceDep, actor: ActorDep) -> list[CardRead]:
    """The visible card, if any: at most one, none during an active session."""
    return [CardRead.model_validate(row) for row in await service.show(actor=actor)]


@router.post("/{card_id}/act", responses=NOT_FOUND | CONFLICT | BAD_BODY | INVALID)
async def act_on_card(
    service: ServiceDep, actor: ActorDep, card_id: uuid.UUID, payload: CardAct
) -> CardRead:
    """Accept, dismiss or snooze ("Not now") one card."""
    return CardRead.model_validate(
        await service.act(card_id, payload.action, actor=actor)
    )
