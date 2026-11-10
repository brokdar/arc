"""Request/response schemas for suggestion cards.

There is no create schema: cards are written by arc itself and, later, by the
coaching agent over MCP. The athlete's endpoints are the one visible card and
the three answers.
"""

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.cards import CardAction, CardPriority, CardState


class CardRead(BaseModel):
    """One card as the athlete sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    priority: CardPriority
    state: CardState
    headline: str
    #: The one sentence that says why arc is showing this.
    because: str
    payload: dict[str, Any]
    first_shown: dt.datetime | None
    expires_at: dt.datetime | None
    snoozed_until: dt.datetime | None


class CardAct(BaseModel):
    """The athlete's answer to a card. "Not now" is ``snooze``."""

    model_config = ConfigDict(extra="forbid")

    action: CardAction
