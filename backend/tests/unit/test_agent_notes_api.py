"""Agent notes end to end: who may write one, who may rate it, what it is about.

Invariant 7 in one file. The two halves have opposite permissions and that is
the design, so most of these tests are about a refusal: the athlete cannot
author a note signed by a model, and the agent cannot rate its own.

Writing has no endpoint by design — it is the coaching agent's move — so these
drive `AgentNoteService` directly, the same service the MCP tool calls. The
athlete's half (reading, disputing) goes through HTTP.
"""

import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.domain.actor import Actor
from app.domain.agent_notes import MAX_CITES, DisputeRating, NoteKind
from app.persistence.agent_notes import AgentNoteRow
from app.persistence.audit import AuditLogEntry
from app.services.agent_notes import AgentNoteService

NOTES = "/api/v1/agent-notes"
MANUAL = "/api/v1/manual-sessions"

AGENT = Actor.agent("coach")
ATHLETE = Actor.athlete()
SYSTEM = Actor.system()

MODEL = "claude-opus-4-6"

#: A Monday. Every plan week is keyed by one (`app.domain.plan`).
MONDAY = dt.date(2026, 8, 10)


# --- helpers ---------------------------------------------------------------------


async def record(client: AsyncClient, **overrides: Any) -> str:
    """Log a manual session and return its id."""
    payload: dict[str, Any] = {
        "start_time": "2026-08-10T17:00:00Z",
        "timezone": "UTC",
        "duration_s": 3_600,
        "discipline": "cycling",
    } | overrides
    response = await client.post(MANUAL, json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def write(
    session: AsyncSession,
    *,
    actor: Actor = AGENT,
    kind: NoteKind = NoteKind.EVALUATION,
    text: str = "Steady all the way through; the last twenty minutes cost more.",
    model_id: str = MODEL,
    **kwargs: Any,
) -> AgentNoteRow:
    """Write a note through the service the MCP tool uses."""
    return await AgentNoteService.from_session(session).create(
        actor=actor, kind=kind, text=text, model_id=model_id, **kwargs
    )


async def stored(session: AsyncSession) -> list[AgentNoteRow]:
    """Every note in the database, oldest first."""
    session.expire_all()
    result = await session.execute(
        select(AgentNoteRow).order_by(AgentNoteRow.created_at, AgentNoteRow.id)
    )
    return list(result.scalars())


async def audit_actions(session: AsyncSession) -> list[str]:
    """Every audit action so far, oldest first."""
    session.expire_all()
    result = await session.execute(
        select(AuditLogEntry.action).order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(result.scalars())


# --- who may write one -----------------------------------------------------------


async def test_an_agent_writes_an_attributed_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    row = await write(db_session, session_id=uuid.UUID(session_id))

    assert row.kind is NoteKind.EVALUATION
    assert row.model_id == MODEL
    assert row.created_by == "agent:coach"
    assert row.session_id == uuid.UUID(session_id)
    assert row.plan_week is None
    assert row.dispute is None
    assert "agent_note.created" in await audit_actions(db_session)


async def test_the_athlete_may_not_author_a_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A "coach note" the athlete wrote would make every other note's
    # attribution worthless the first time it happened.
    session_id = await record(client)

    with pytest.raises(ForbiddenError):
        await write(db_session, actor=ATHLETE, session_id=uuid.UUID(session_id))

    assert await stored(db_session) == []


async def test_the_system_may_not_author_a_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ForbiddenError):
        await write(db_session, actor=SYSTEM, session_id=uuid.UUID(session_id))


async def test_a_note_needs_a_model_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ValidationError, match="model_id"):
        await write(db_session, model_id="  ", session_id=uuid.UUID(session_id))


async def test_a_note_needs_text(client: AsyncClient, db_session: AsyncSession) -> None:
    session_id = await record(client)

    with pytest.raises(ValidationError, match="empty note"):
        await write(db_session, text="   ", session_id=uuid.UUID(session_id))


async def test_a_note_about_a_session_that_does_not_exist_is_refused(
    db_session: AsyncSession,
) -> None:
    # Unreachable by every read there is, so writing one is a silent no-op
    # dressed as a success.
    with pytest.raises(NotFoundError):
        await write(db_session, session_id=uuid.uuid4())


# --- exactly one target ----------------------------------------------------------


async def test_a_note_about_both_a_session_and_a_week_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ValidationError, match="not both"):
        await write(
            db_session,
            kind=NoteKind.ANNOTATION,
            session_id=uuid.UUID(session_id),
            plan_week=MONDAY,
        )


async def test_a_note_about_nothing_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationError, match="needs a subject"):
        await write(db_session, kind=NoteKind.ANNOTATION)


async def test_the_database_refuses_two_targets_as_well(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The service checks it in words, but the CHECK constraint is what makes
    # the rule true regardless of which caller wrote the row.
    session_id = await record(client)
    db_session.add(
        AgentNoteRow(
            session_id=uuid.UUID(session_id),
            plan_week=MONDAY,
            kind=NoteKind.ANNOTATION,
            text="both at once",
            model_id=MODEL,
            created_by=str(AGENT),
            cites=[],
        )
    )

    with pytest.raises(Exception, match="one_target"):
        await db_session.flush()

    await db_session.rollback()


async def test_an_evaluation_may_not_be_about_a_week(
    db_session: AsyncSession,
) -> None:
    # A week is not a thing anyone did; commentary about one is an annotation.
    with pytest.raises(ValidationError, match="needs session_id"):
        await write(db_session, kind=NoteKind.EVALUATION, plan_week=MONDAY)


async def test_an_annotation_may_be_about_a_week(db_session: AsyncSession) -> None:
    row = await write(
        db_session,
        kind=NoteKind.ANNOTATION,
        text="Three weeks of threshold with no easy week.",
        plan_week=MONDAY,
    )

    assert row.plan_week == MONDAY
    assert row.session_id is None


async def test_a_week_that_is_not_a_monday_is_refused(
    db_session: AsyncSession,
) -> None:
    # One week, one key — otherwise two notes about one week file under two
    # keys and neither read finds the other's.
    with pytest.raises(ValidationError, match="Monday"):
        await write(
            db_session,
            kind=NoteKind.ANNOTATION,
            plan_week=MONDAY + dt.timedelta(days=2),
        )


# --- citations -------------------------------------------------------------------


async def test_a_note_may_cite_artefacts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    row = await write(db_session, session_id=uuid.UUID(session_id), cites=[session_id])

    assert row.cites == [session_id]


async def test_a_note_may_cite_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    row = await write(db_session, session_id=uuid.UUID(session_id))

    assert row.cites == []


async def test_a_citation_that_is_not_an_id_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ValidationError, match=r"cites\[1\]"):
        await write(
            db_session,
            session_id=uuid.UUID(session_id),
            cites=[session_id, "the ride on Tuesday"],
        )


async def test_too_many_citations_are_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    with pytest.raises(ValidationError, match="at most"):
        await write(
            db_session,
            session_id=uuid.UUID(session_id),
            cites=[str(uuid.uuid4()) for _ in range(MAX_CITES + 1)],
        )


# --- the dry run -----------------------------------------------------------------


async def test_a_dry_run_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)

    row = await write(db_session, session_id=uuid.UUID(session_id), dry_run=True)

    assert row.id is None, "a dry run must not invent an id for a row that is not there"
    assert row.text
    assert await stored(db_session) == []
    assert "agent_note.created" not in await audit_actions(db_session)


async def test_a_dry_run_still_refuses_what_the_write_would_refuse(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(NotFoundError):
        await write(db_session, session_id=uuid.uuid4(), dry_run=True)


async def test_a_dry_run_costs_no_rate_cap_budget(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checking before acting must be the cheap option, or an agent told to
    # dry-run first is being told to spend its budget twice.
    monkeypatch.setenv("MCP__WRITE_CAP_PER_HOUR", "1")
    get_settings.cache_clear()
    session_id = await record(client)

    for _ in range(5):
        await write(db_session, session_id=uuid.UUID(session_id), dry_run=True)

    row = await write(db_session, session_id=uuid.UUID(session_id))
    assert row.id is not None


async def test_the_write_cap_stops_an_agent_writing_notes(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP__WRITE_CAP_PER_HOUR", "1")
    get_settings.cache_clear()
    session_id = await record(client)

    await write(db_session, session_id=uuid.UUID(session_id))

    with pytest.raises(RateLimitedError, match="cap"):
        await write(db_session, session_id=uuid.UUID(session_id))


# --- disputing -------------------------------------------------------------------


async def test_the_athlete_rates_a_note_through_http(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)
    note = await write(db_session, session_id=uuid.UUID(session_id))

    response = await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": "down"})

    assert response.status_code == 200, response.text
    assert response.json()["dispute"] == "down"
    assert response.json()["disputed_at"] is not None
    assert "agent_note.disputed" in await audit_actions(db_session)


async def test_a_rating_overwrites_the_previous_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A toggle on a card: tapped, mistapped, retapped. Only current opinion is
    # read; what happened is in the audit log.
    session_id = await record(client)
    note = await write(db_session, session_id=uuid.UUID(session_id))

    await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": "up"})
    response = await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": "down"})

    assert response.json()["dispute"] == "down"
    assert (await audit_actions(db_session)).count("agent_note.disputed") == 2


async def test_a_rating_can_be_taken_back(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # An athlete who cannot take a rating back will stop giving them.
    session_id = await record(client)
    note = await write(db_session, session_id=uuid.UUID(session_id))
    await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": "up"})

    response = await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": None})

    assert response.status_code == 200, response.text
    assert response.json()["dispute"] is None
    assert response.json()["disputed_at"] is None


async def test_an_agent_may_not_rate_a_note(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A model rating its own output measures nothing.
    session_id = await record(client)
    note = await write(db_session, session_id=uuid.UUID(session_id))

    with pytest.raises(ForbiddenError):
        await AgentNoteService.from_session(db_session).dispute(
            note.id, actor=AGENT, rating=DisputeRating.UP
        )


async def test_rating_a_note_that_does_not_exist_is_a_404(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{NOTES}/{uuid.uuid4()}/dispute", json={"rating": "up"}
    )

    assert response.status_code == 404, response.text


async def test_an_unknown_rating_is_refused(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)
    note = await write(db_session, session_id=uuid.UUID(session_id))

    response = await client.post(f"{NOTES}/{note.id}/dispute", json={"rating": "meh"})

    assert response.status_code == 422, response.text


# --- reading -----------------------------------------------------------------------


async def test_the_notes_about_a_session_come_back_oldest_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await record(client)
    await write(db_session, text="First thought.", session_id=uuid.UUID(session_id))
    await write(db_session, text="Second thought.", session_id=uuid.UUID(session_id))

    response = await client.get(NOTES, params={"session_id": session_id})

    assert response.status_code == 200, response.text
    texts = [item["text"] for item in response.json()["items"]]
    assert texts == ["First thought.", "Second thought."]


async def test_the_notes_about_a_week_come_back_by_its_monday(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await write(
        db_session,
        kind=NoteKind.ANNOTATION,
        text="A big week.",
        plan_week=MONDAY,
    )

    response = await client.get(NOTES, params={"week": MONDAY.isoformat()})

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["plan_week"] == MONDAY.isoformat()
    assert item["model_id"] == MODEL
    assert item["created_by"] == "agent:coach"


async def test_a_read_with_no_subject_is_refused(client: AsyncClient) -> None:
    response = await client.get(NOTES)

    assert response.status_code == 422, response.text


async def test_a_read_with_two_subjects_is_refused(
    client: AsyncClient,
) -> None:
    response = await client.get(
        NOTES, params={"session_id": str(uuid.uuid4()), "week": MONDAY.isoformat()}
    )

    assert response.status_code == 422, response.text


async def test_a_read_of_a_week_that_is_not_a_monday_is_refused(
    client: AsyncClient,
) -> None:
    response = await client.get(
        NOTES, params={"week": (MONDAY + dt.timedelta(days=1)).isoformat()}
    )

    assert response.status_code == 422, response.text


async def test_notes_are_behind_the_session_guard(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(NOTES)).status_code == 401
    assert (
        await anon_client.post(f"{NOTES}/{uuid.uuid4()}/dispute", json={"rating": "up"})
    ).status_code == 401


async def test_there_is_no_athlete_facing_create_endpoint(
    client: AsyncClient,
) -> None:
    # The way in is the MCP tool. An endpoint here would be a second, weaker
    # way to write something that is attributed to a model.
    response = await client.post(
        NOTES, json={"kind": "annotation", "text": "mine", "model_id": MODEL}
    )

    assert response.status_code == 405, response.text
