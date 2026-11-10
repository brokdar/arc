"""Suggestion cards over HTTP (AC-7, and AC-6's 409).

Cards are seeded through `CardService.create` — there is no create endpoint,
arc writes them itself — and everything else goes through the athlete's API.
The active session is a real manual strength session, logged through
`POST /manual-sessions`, whose `lifecycle` is set to `active` the way the gym
screen's Start will set it.
"""

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.domain.activity import SessionLifecycle
from app.domain.actor import Actor
from app.domain.cards import WELCOME_BACK, CardPriority, CardState
from app.domain.proposals import ChangeKind
from app.persistence.activity import SessionRow
from app.persistence.audit import AuditLogEntry
from app.persistence.cards import CardRow
from app.persistence.proposals import PlanProposalRow
from app.services.cards import (
    NOTE,
    CardService,
    run_card_expiry,
)

CARDS = "/api/v1/cards"
MANUAL = "/api/v1/manual-sessions"


async def queue(
    db_session: AsyncSession,
    *,
    priority: CardPriority = CardPriority.DIGEST,
    kind: str = NOTE,
    headline: str = "Your import needs a look",
    because: str = "Three sets came in without a weight.",
) -> CardRow:
    """Queue one card the way arc does."""
    return await CardService.from_session(db_session).create(
        actor=Actor.system(),
        kind=kind,
        priority=priority,
        headline=headline,
        because=because,
        payload={"sets": [1, 2, 3]},
    )


async def start_strength_session(client: AsyncClient, db_session: AsyncSession) -> str:
    """Log a strength session and put it between Start and Finish."""
    response = await client.post(
        MANUAL,
        json={
            "start_time": "2026-11-10T17:30:00+01:00",
            "timezone": "Europe/Berlin",
            "duration_s": 3600,
            "sets": [{"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0}],
        },
    )
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    assert response.json()["discipline"] == "strength"
    await set_lifecycle(db_session, session_id, SessionLifecycle.ACTIVE)
    return session_id


async def set_lifecycle(
    db_session: AsyncSession, session_id: str, lifecycle: SessionLifecycle
) -> None:
    """Start or Finish a session."""
    await db_session.execute(
        update(SessionRow)
        .where(SessionRow.id == uuid.UUID(session_id))
        .values(lifecycle=lifecycle)
    )
    await db_session.commit()


async def stored(db_session: AsyncSession, card_id: Any) -> CardRow:
    """The card as the database holds it now."""
    row = await db_session.get(CardRow, uuid.UUID(str(card_id)))
    assert row is not None
    await db_session.refresh(row)
    return row


# --- AC-7: never during an active session ------------------------------------


async def test_no_card_is_returned_while_a_strength_session_is_active(
    data_root: Path, client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    await queue(db_session, priority=CardPriority.SAFETY, headline="Stop and rest")
    await queue(db_session, kind=WELCOME_BACK, priority=CardPriority.TODAY)
    session_id = await start_strength_session(client, db_session)

    during = await client.get(CARDS)

    assert during.status_code == 200
    assert during.json() == []
    assert (await stored(db_session, card.id)).first_shown is None, (
        "nothing was shown, so nothing spent the budget"
    )

    await set_lifecycle(db_session, session_id, SessionLifecycle.FINISHED)
    after = await client.get(CARDS)

    assert after.status_code == 200
    [shown] = after.json()
    assert shown["headline"] == "Stop and rest", "safety first, once training is over"
    assert shown["state"] == "shown"


async def test_the_queued_card_is_returned_after_finish(
    data_root: Path, client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    session_id = await start_strength_session(client, db_session)
    assert (await client.get(CARDS)).json() == []

    await set_lifecycle(db_session, session_id, SessionLifecycle.FINISHED)
    [shown] = (await client.get(CARDS)).json()

    assert shown["id"] == str(card.id)
    assert shown["headline"] == "Your import needs a look"
    assert shown["because"] == "Three sets came in without a weight."
    assert shown["kind"] == NOTE
    assert shown["priority"] == CardPriority.DIGEST
    assert shown["payload"] == {"sets": [1, 2, 3]}
    row = await stored(db_session, card.id)
    assert row.state is CardState.SHOWN
    assert row.first_shown is not None
    assert row.expires_at == row.first_shown + dt.timedelta(days=7)
    assert shown["expires_at"] is not None


async def test_an_empty_queue_is_an_empty_list(client: AsyncClient) -> None:
    response = await client.get(CARDS)

    assert response.status_code == 200
    assert response.json() == []


async def test_the_list_needs_a_login(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(CARDS)).status_code == 401
    assert (
        await anon_client.post(f"{CARDS}/{uuid.uuid7()}/act", json={"action": "accept"})
    ).status_code == 401


# --- answering ------------------------------------------------------------------


async def test_accepting_answers_the_card_and_audits_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    await client.get(CARDS)

    response = await client.post(f"{CARDS}/{card.id}/act", json={"action": "accept"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "accepted"
    row = await stored(db_session, card.id)
    assert row.state is CardState.ACCEPTED
    assert row.resolved_at is not None
    actions = [
        entry.action
        for entry in (
            await db_session.execute(
                select(AuditLogEntry)
                .where(AuditLogEntry.entity_id == card.id)
                .order_by(AuditLogEntry.id)
            )
        ).scalars()
    ]
    assert actions == ["card.created", "card.shown", "card.accepted"]


async def test_not_now_snoozes_and_keeps_first_shown(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    [shown] = (await client.get(CARDS)).json()

    response = await client.post(f"{CARDS}/{card.id}/act", json={"action": "snooze"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "snoozed"
    assert response.json()["first_shown"] == shown["first_shown"]
    assert response.json()["snoozed_until"] is not None
    assert (await client.get(CARDS)).json() == [], "snoozed until tomorrow"


async def test_acting_on_an_unknown_card_is_404(client: AsyncClient) -> None:
    missing = uuid.uuid7()

    response = await client.post(f"{CARDS}/{missing}/act", json={"action": "accept"})

    assert response.status_code == 404
    assert response.json()["detail"] == f"Card {missing} not found"


async def test_acting_on_a_dismissed_card_is_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    await client.get(CARDS)
    first = await client.post(f"{CARDS}/{card.id}/act", json={"action": "dismiss"})
    assert first.status_code == 200
    assert first.json()["state"] == "dismissed"

    again = await client.post(f"{CARDS}/{card.id}/act", json={"action": "accept"})

    assert again.status_code == 409
    assert "dismissed" in again.json()["detail"]
    assert (await stored(db_session, card.id)).state is CardState.DISMISSED


async def test_acting_on_an_expired_card_is_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Stamped into the past rather than moving the clock: same state,
    # deterministic. The sweep has not run, so the row still says `shown`.
    card = await queue(db_session)
    await client.get(CARDS)
    row = await stored(db_session, card.id)
    first = dt.datetime.now(dt.UTC) - dt.timedelta(days=7, minutes=1)
    row.first_shown = first
    row.expires_at = first + dt.timedelta(days=7)
    await db_session.commit()

    response = await client.post(f"{CARDS}/{card.id}/act", json={"action": "accept"})

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    assert (await client.get(CARDS)).json() == [], "listed nowhere"


async def test_an_unknown_action_is_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)

    response = await client.post(f"{CARDS}/{card.id}/act", json={"action": "maybe"})

    assert response.status_code == 422


# --- the sweep ------------------------------------------------------------------


async def test_the_sweep_writes_expired_and_audits_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    card = await queue(db_session)
    fresh = await queue(db_session, priority=CardPriority.SAFETY)
    await client.get(CARDS)  # shows the safety card
    old = await stored(db_session, card.id)
    first = dt.datetime.now(dt.UTC) - dt.timedelta(days=8)
    old.first_shown = first
    old.expires_at = first + dt.timedelta(days=7)
    old.state = CardState.SHOWN
    await db_session.commit()

    await run_card_expiry()

    assert (await stored(db_session, card.id)).state is CardState.EXPIRED
    assert (await stored(db_session, fresh.id)).state is CardState.SHOWN
    audit = (
        await db_session.execute(
            select(AuditLogEntry).where(AuditLogEntry.action == "card.expired")
        )
    ).scalars()
    assert [(entry.entity_id, entry.actor) for entry in audit] == [(card.id, "system")]


async def test_an_unregistered_kind_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationError, match="Unknown card kind"):
        await queue(db_session, kind="horoscope")


# --- the decision: cards are not proposals --------------------------------------


async def test_proposals_untouched(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Cards are a separate store with a kind registry; the proposals union
    # stays the four plan changes. A card's whole life writes no proposal and
    # widens no union.
    card = await queue(db_session)
    await client.get(CARDS)
    await client.post(f"{CARDS}/{card.id}/act", json={"action": "accept"})

    assert {kind.value for kind in ChangeKind} == {"create", "update", "move", "delete"}
    assert (await db_session.execute(select(PlanProposalRow))).scalars().all() == []
    proposals = await client.get("/api/v1/proposals")
    assert proposals.status_code == 200
    assert proposals.json()["total"] == 0
