"""Every write appends an audit row (build plan WP-1.6).

Audit is not a feature that gets added later: WP-8's agent guardrails are
stated in terms of `actor=agent:<key-label>`, and a trail with holes in it is
worse than none, because it looks complete. These tests walk every mutating
path the API exposes and demand a row from each.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.actor import Actor
from app.domain.anchors import AnchorSource, AnchorType, Provenance
from app.persistence.audit import AuditLogEntry, AuditRepository
from app.persistence.db import session_scope
from app.services.anchors import AnchorService

ATHLETE = "/api/v1/athlete"
ANCHORS = "/api/v1/anchors"


async def entries(session: AsyncSession) -> list[AuditLogEntry]:
    """Every audit row, oldest first.

    Ordered by id as well as time: SQLite's ``CURRENT_TIMESTAMP`` has
    second resolution, so rows written in one request tie on ``at`` — and
    uuid7 ids are time-ordered, which is exactly what breaks the tie right.
    """
    result = await session.execute(
        select(AuditLogEntry).order_by(AuditLogEntry.at, AuditLogEntry.id)
    )
    return list(result.scalars())


async def test_bootstrapping_the_profile_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.get(ATHLETE)

    rows = await entries(db_session)

    assert [row.action for row in rows] == ["athlete.created"]
    assert rows[0].entity_type == "athlete"
    assert rows[0].payload_json == {"bootstrap": True}


async def test_reading_an_existing_profile_writes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.get(ATHLETE)
    await client.get(ATHLETE)
    await client.get(ATHLETE)

    # The bootstrap is the only write a read can ever do.
    assert len(await entries(db_session)) == 1


async def test_updating_the_profile_is_audited_with_the_diff(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(ATHLETE, json={"name": "Alex"})
    await client.patch(ATHLETE, json={"name": "Alex Rider", "height_cm": 181.0})

    rows = await entries(db_session)

    assert [row.action for row in rows] == [
        "athlete.created",
        "athlete.updated",
        "athlete.updated",
    ]
    assert rows[-1].payload_json == {
        "changed": {
            "name": {"from": "Alex", "to": "Alex Rider"},
            "height_cm": {"from": None, "to": 181.0},
        }
    }


async def test_appending_an_anchor_is_audited_with_the_value(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = (
        await client.post(
            ANCHORS,
            json={
                "anchor_type": "ftp",
                "value": 265,
                "provenance": "tested",
                "protocol": "20min x0.95",
                "effective_date": "2026-03-01",
            },
        )
    ).json()

    rows = await entries(db_session)

    assert [row.action for row in rows] == ["anchor.appended"]
    assert rows[0].entity_type == "anchor_version"
    assert str(rows[0].entity_id) == created["id"]
    assert rows[0].payload_json == {
        "anchor_type": "ftp",
        "value": 265.0,
        "unit": "W",
        "provenance": "tested",
        "protocol": "20min x0.95",
        "effective_date": "2026-03-01",
        "ci_low": None,
        "ci_high": None,
        "source": "athlete",
    }


async def test_a_write_through_the_api_is_credited_to_the_athlete(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(ATHLETE, json={"name": "Alex"})

    rows = await entries(db_session)

    assert {row.actor for row in rows} == {"athlete"}
    assert all(Actor.parse(row.actor) == Actor.athlete() for row in rows)


async def test_a_write_through_a_non_http_caller_names_the_agent(
    session_factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    # What an MCP tool will do in WP-8: same service, same audit row, an
    # actor string that says which key was presented.
    async with session_scope() as session:
        await AnchorService.from_session(session).append(
            actor=Actor.agent("coach"),
            anchor_type=AnchorType.FTP,
            value=250,
            provenance=Provenance.ESTIMATED,
            source=AnchorSource.AGENT,
        )

    rows = await entries(db_session)

    assert [row.actor for row in rows] == ["agent:coach"]
    assert rows[0].payload_json["source"] == "agent"


async def test_a_rejected_write_leaves_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The audit row is flushed into the same transaction as the write it
    # describes, so a rollback takes both.
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": 25_000, "provenance": "estimated"},
    )

    assert response.status_code == 422
    assert await entries(db_session) == []


async def test_the_audit_log_offers_no_way_to_rewrite_history() -> None:
    assert not {"update", "delete", "remove"} & set(vars(AuditRepository))


async def test_audit_rows_are_listed_newest_first(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await client.patch(ATHLETE, json={"name": "Alex"})
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )

    async with session_factory() as session:
        rows, total = await AuditRepository(session).list()

    assert total == 3
    assert rows[0].action == "anchor.appended"


WELLNESS_DAYS = "/api/v1/wellness/days"


async def test_recording_a_wellness_day_is_audited_with_the_diff(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"resting_hr_bpm": 47})

    [row] = [
        entry
        for entry in await entries(db_session)
        if entry.entity_type == "wellness_day"
    ]

    assert row.action == "wellness.created"
    assert row.payload_json == {
        "local_date": "2026-06-01",
        "changed": {"resting_hr_bpm": {"from": None, "to": 47}},
    }


async def test_correcting_a_wellness_day_records_what_it_used_to_say(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A day is corrigible in place, so the audit row is the only place the
    # superseded value survives — which is what makes correcting it safe.
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"sleep_duration_s": 650})
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"sleep_duration_s": 23_400})

    rows = [
        entry
        for entry in await entries(db_session)
        if entry.entity_type == "wellness_day"
    ]

    assert [row.action for row in rows] == ["wellness.created", "wellness.updated"]
    assert rows[1].payload_json["changed"] == {
        "sleep_duration_s": {"from": 650, "to": 23_400}
    }


async def test_retracting_a_wellness_day_keeps_what_the_day_said(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"resting_hr_bpm": 47})
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"resting_hr_bpm": None})

    rows = [
        entry
        for entry in await entries(db_session)
        if entry.entity_type == "wellness_day"
    ]

    assert [row.action for row in rows] == ["wellness.created", "wellness.retracted"]
    assert rows[1].payload_json["changed"] == {
        "resting_hr_bpm": {"from": 47, "to": None}
    }
