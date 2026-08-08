"""The match tables against a real Postgres — the dialect-specific half.

Three things the unit suite cannot prove on SQLite:

* ``session_matches.breakdown`` is `JSONColumn`, which is **JSONB** here and
  TEXT there. A column that arrived as text would answer every ORM read
  correctly and fail the first time anything asked Postgres a question about
  its contents, so the assertions below go through JSONB operators rather than
  through the ORM;
* the two unique constraints that carry the MVP's one-to-one restriction. The
  service checks both before it writes, and that check is a read that can
  always lose a race — the constraints are what turn the loser into a 409
  instead of a session with two links;
* the ``ON DELETE CASCADE`` on both foreign keys, which is what stops a link
  or a prompt from outliving the session it is about.
"""

import datetime as dt
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.domain.activity import SessionMatchStatus
from app.domain.matching import (
    EveningPromptKind,
    EveningPromptStatus,
    MatchLinkStatus,
)
from app.domain.sessions import SessionStatus
from app.persistence.matching import EveningPromptRow, SessionMatchRow

ANCHORS = "/api/v1/anchors"
PLANNED = "/api/v1/planned-sessions"
MANUAL = "/api/v1/manual-sessions"

MONDAY = dt.date(2026, 8, 10)

#: 600 + 3 x (480 + 240), with a power target on the work steps — the same
#: prescription the unit suite scores against.
RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {"kind": "steady", "duration_s": 600, "role": "warmup"},
        {
            "kind": "repeat",
            "times": 3,
            "children": [
                {
                    "kind": "steady",
                    "duration_s": 480,
                    "role": "work",
                    "targets": {
                        "power": {
                            "kind": "percent_of_anchor",
                            "anchor_type": "ftp",
                            "pct_low": 0.88,
                            "pct_high": 0.93,
                        }
                    },
                },
                {"kind": "steady", "duration_s": 240, "role": "recovery"},
            ],
        },
    ],
}
RIDE_DURATION_S = 2_760


async def a_matched_pair(client: AsyncClient) -> tuple[str, str, str]:
    """Plan a ride, record it, and return ``(planned, session, link)`` ids."""
    anchor = await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    assert anchor.status_code == 201, anchor.text
    planned = await client.post(
        PLANNED,
        json={
            "date": MONDAY.isoformat(),
            "purpose": "sweet_spot",
            "structure": RIDE,
        },
    )
    assert planned.status_code == 201, planned.text
    done = await client.post(
        MANUAL,
        json={
            "start_time": f"{MONDAY.isoformat()}T09:00:00+00:00",
            "timezone": "UTC",
            "duration_s": RIDE_DURATION_S,
            "discipline": "cycling",
            "sets": [],
        },
    )
    assert done.status_code == 201, done.text
    link = done.json()["match"]
    assert link is not None, done.text
    return planned.json()["id"], done.json()["id"], link["id"]


async def test_the_breakdown_is_queryable_jsonb(client: AsyncClient) -> None:
    await a_matched_pair(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT jsonb_typeof(breakdown) AS shape,
                               (breakdown ->> 'score')::float AS score,
                               (breakdown -> 'weights' ->> 'duration')::float
                                   AS duration_weight,
                               jsonb_array_length(breakdown -> 'components')
                                   AS assessed,
                               jsonb_array_length(breakdown -> 'not_assessed')
                                   AS absent,
                               breakdown -> 'components' -> 0 ->> 'component'
                                   AS first_component
                        FROM session_matches
                        """
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert row.shape == "object"
    assert row.duration_weight == pytest.approx(0.4)
    # A typed-in ride has no stream, so duration is the one component that
    # could be assessed and the other two say why they could not (D138) — the
    # renormalisation, visible to Postgres itself.
    assert row.assessed == 1
    assert row.absent == 2
    assert row.first_component == "duration"
    assert row.score == pytest.approx(1.0)


async def test_neither_side_can_be_linked_twice(client: AsyncClient) -> None:
    """The MVP's one-to-one restriction, in the database rather than a check."""
    planned_id, session_id, _ = await a_matched_pair(client)
    # Each collides on one side and is fresh on the other, so the constraint
    # that fires is the one being tested rather than whichever comes first.
    collisions = (
        (uuid.UUID(session_id), uuid.uuid7()),
        (uuid.uuid7(), uuid.UUID(planned_id)),
    )
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        for taken_session, taken_planned in collisions:
            async with AsyncSession(engine) as session:
                session.add(
                    SessionMatchRow(
                        session_id=taken_session,
                        planned_session_id=taken_planned,
                        status=MatchLinkStatus.PENDING,
                        similarity=0.5,
                        breakdown={},
                        created_by="athlete",
                        previous_session_status=SessionMatchStatus.UNMATCHED,
                        previous_planned_status=SessionStatus.PLANNED,
                    )
                )
                with pytest.raises(IntegrityError, match="uq_session_matches"):
                    await session.flush()
    finally:
        await engine.dispose()


async def test_a_second_prompt_for_one_session_is_refused(
    client: AsyncClient,
) -> None:
    """The sweep runs hourly over the same backlog and must not accumulate."""
    planned_id, _, _ = await a_matched_pair(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    now = dt.datetime.now(dt.UTC)
    try:
        async with AsyncSession(engine) as session:
            for _ in range(2):
                session.add(
                    EveningPromptRow(
                        planned_session_id=uuid.UUID(planned_id),
                        kind=EveningPromptKind.MISSED_SESSION,
                        status=EveningPromptStatus.PENDING,
                        expires_at=now + dt.timedelta(hours=72),
                    )
                )
            with pytest.raises(IntegrityError, match="uq_evening_prompts"):
                await session.flush()
    finally:
        await engine.dispose()


async def test_deleting_a_planned_session_takes_its_link_and_prompt_with_it(
    client: AsyncClient,
) -> None:
    """``ON DELETE CASCADE`` on both foreign keys, proved around the ORM.

    Around it deliberately: the ORM has no relationship to either table, so a
    unit of work that appeared to work would prove nothing about what the
    database does when a row is removed by anything else.
    """
    planned_id, _, _ = await a_matched_pair(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    now = dt.datetime.now(dt.UTC)
    try:
        async with AsyncSession(engine) as session:
            session.add(
                EveningPromptRow(
                    planned_session_id=uuid.UUID(planned_id),
                    kind=EveningPromptKind.MISSED_SESSION,
                    status=EveningPromptStatus.PENDING,
                    expires_at=now + dt.timedelta(hours=72),
                )
            )
            await session.commit()
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("DELETE FROM planned_sessions WHERE id = :id"),
                {"id": planned_id},
            )
        async with engine.connect() as conn:
            links = await conn.scalar(sa.text("SELECT count(*) FROM session_matches"))
            prompts = await conn.scalar(sa.text("SELECT count(*) FROM evening_prompts"))
    finally:
        await engine.dispose()

    assert links == 0
    assert prompts == 0
