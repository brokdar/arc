"""The WP-1 tables against a real Postgres — the dialect-specific half.

The unit suite runs on SQLite, so anything the two dialects spell differently
(JSONB, timestamptz, the non-native enum VARCHAR) is only really tested here.
"""

import datetime as dt

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

ATHLETE = "/api/v1/athlete"
ANCHORS = "/api/v1/anchors"
ZONES = "/api/v1/zones"


async def test_the_anchor_and_zone_flow_round_trips(client: AsyncClient) -> None:
    appended = await client.post(
        ANCHORS,
        json={
            "anchor_type": "ftp",
            "value": 265,
            "provenance": "tested",
            "protocol": "20min x0.95",
            "effective_date": "2026-03-01",
            "ci_low": 255,
            "ci_high": 275,
        },
    )
    assert appended.status_code == 201, appended.text

    listed = (await client.get(ANCHORS, params={"anchor_type": "ftp"})).json()
    assert listed["total"] == 1

    zones = (await client.get(ZONES, params={"anchor_type": "ftp"})).json()
    assert zones["anchor_version"]["id"] == appended.json()["id"]
    assert len(zones["zones"]) == 7


async def test_enum_columns_store_the_member_value(client: AsyncClient) -> None:
    # Read as raw SQL, bypassing the ORM's conversion: the row must say
    # `max_hr`, the same spelling the API and the OpenAPI schema use.
    await client.post(
        ANCHORS,
        json={"anchor_type": "max_hr", "value": 190, "provenance": "athlete_reported"},
    )

    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        stored = await conn.scalar(
            text("SELECT anchor_type, unit, staleness_state FROM anchor_versions")
        )
        row = (
            await conn.execute(
                text("SELECT anchor_type, unit, staleness_state FROM anchor_versions")
            )
        ).one()
    await engine.dispose()

    assert stored == "max_hr"
    assert tuple(row) == ("max_hr", "bpm", "fresh")


async def test_jsonb_capabilities_round_trip(client: AsyncClient) -> None:
    capabilities = {"cycling": {"weekly_hours": 8, "tags": ["climber"]}}

    updated = await client.patch(ATHLETE, json={"capabilities": capabilities})

    assert updated.status_code == 200, updated.text
    assert (await client.get(ATHLETE)).json()["capabilities"] == capabilities


async def test_jsonb_audit_payloads_are_queryable(client: AsyncClient) -> None:
    # The reason the column is JSONB and not TEXT: WP-8's guardrails ask
    # questions of the payload, not just of the row.
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )

    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        value = await conn.scalar(
            text(
                "SELECT payload_json->>'anchor_type' FROM audit_log "
                "WHERE action = 'anchor.appended'"
            )
        )
    await engine.dispose()

    assert value == "ftp"


async def test_timestamps_are_aware_utc_here_too(client: AsyncClient) -> None:
    # The Postgres half of the UtcDateTime contract; the SQLite half is in
    # tests/unit/test_persistence_types.py. Both dialects must agree, or the
    # unit suite is testing a different application than the one that ships.
    profile = (await client.get(ATHLETE)).json()

    for field in ("created_at", "updated_at"):
        parsed = dt.datetime.fromisoformat(profile[field])
        assert parsed.utcoffset() == dt.timedelta(0), profile[field]


async def test_the_singleton_athlete_cannot_be_duplicated(client: AsyncClient) -> None:
    # The fixed primary key is what makes "one athlete" a database fact
    # rather than a convention every future caller has to remember.
    await client.get(ATHLETE)

    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM athlete"))
    await engine.dispose()

    assert count == 1
