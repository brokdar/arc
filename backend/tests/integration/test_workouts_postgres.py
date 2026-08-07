"""The WP-2 tables against a real Postgres — the dialect-specific half.

The unit suite runs on SQLite, so what is only really tested here is what the
two dialects spell differently: JSONB storage and querying of the structure
document, real `ILIKE` (SQLite emulates it with `lower()`), the non-native
enum VARCHARs, and — the one that bit in the unit suite — the referential
actions, which SQLite ignores unless a pragma turns them on.
"""

import json
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

WORKOUTS = "/api/v1/workouts"
SESSIONS = "/api/v1/planned-sessions"
ANCHORS = "/api/v1/anchors"
EXERCISES = "/api/v1/exercises"

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

LIFT: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": "back_squat",
                    "sets": 5,
                    "reps": 3,
                    "load": {"kind": "percent_e1rm", "value": 0.85},
                }
            ]
        }
    ],
}


async def scalar(statement: str) -> Any:
    """Run one raw SQL query against the real database."""
    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        value = await conn.scalar(text(statement))
    await engine.dispose()
    return value


async def execute(statement: str, **params: Any) -> None:
    """Run one raw SQL statement against the real database."""
    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.begin() as conn:
        await conn.execute(text(statement), params or None)
    await engine.dispose()


async def create_workout(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Add a workout to the library, asserting it was accepted."""
    payload: dict[str, Any] = {"name": "Sweet spot 3x8", "structure": RIDE} | overrides
    response = await client.post(WORKOUTS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- JSONB --------------------------------------------------------------------


async def test_the_structure_round_trips_through_jsonb(client: AsyncClient) -> None:
    created = await create_workout(client)

    fetched = (await client.get(f"{WORKOUTS}/{created['id']}")).json()

    assert fetched["structure"] == created["structure"]
    assert fetched["summary"]["step_count"] == 7


async def test_the_structure_is_queryable_as_jsonb(client: AsyncClient) -> None:
    # The reason the column is JSONB and not TEXT: WP-3's calendar and WP-7's
    # scorer ask questions of the prescription, not just of the row.
    await create_workout(client)

    times = await scalar("SELECT structure->'steps'->1->>'times' FROM workouts")

    assert times == "3"


async def test_pinned_anchor_versions_are_queryable(client: AsyncClient) -> None:
    appended = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"},
    )
    await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
    )

    pinned = await scalar(
        "SELECT pinned_anchor_versions->>'ftp' FROM planned_session_intents"
    )

    assert pinned == appended.json()["id"]


async def test_criteria_stored_before_smoothing_existed_still_read_back(
    client: AsyncClient,
) -> None:
    # Criteria are tagged-union JSONB, not columns, so `smoothing_s` shipped
    # without a migration and the decoder's tolerance is what stands in for
    # one. This writes a WP-2-era row through raw SQL — no `smoothing_s`
    # anywhere — and reads the session back through the API.
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    session = (
        await client.post(
            SESSIONS,
            json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
        )
    ).json()
    legacy = [
        {
            "kind": "time_in_band",
            "selector": {"kind": "role", "role": "work"},
            "band": {"channel": "power", "low": 0.95, "high": 1.05},
            "min_fraction": 0.8,
        },
        {
            "kind": "ceiling",
            "channel": "power",
            "limit": {"kind": "percent_of_anchor", "anchor_type": "ftp", "pct": 0.6},
            "max_seconds_above": 120,
        },
    ]
    # Written as raw SQL rather than through the API: the row has to look
    # exactly as WP-2 wrote it, and every write path now adds the key.
    await execute(
        "UPDATE planned_session_intents "
        "SET success_criteria = CAST(:criteria AS jsonb)",
        criteria=json.dumps(legacy),
    )

    fetched = await client.get(f"{SESSIONS}/{session['id']}")

    assert fetched.status_code == 200, fetched.text
    band, ceiling = fetched.json()["intent"]["success_criteria"]
    assert band["band"]["smoothing_s"] == 30
    assert ceiling["smoothing_s"] == 0


# --- ILIKE --------------------------------------------------------------------


async def test_search_uses_a_real_case_insensitive_match(
    client: AsyncClient,
) -> None:
    await create_workout(client, name="Sweet Spot 3x8")

    assert (await client.get(WORKOUTS, params={"q": "sweet"})).json()["total"] == 1
    assert (await client.get(WORKOUTS, params={"q": "SWEET"})).json()["total"] == 1


async def test_a_like_wildcard_is_escaped_on_postgres_too(
    client: AsyncClient,
) -> None:
    await create_workout(client, name="Sweet Spot 3x8")

    assert (await client.get(WORKOUTS, params={"q": "%"})).json()["total"] == 0
    assert (await client.get(EXERCISES, params={"q": "_"})).json()["total"] == 0


# --- referential actions ------------------------------------------------------


async def test_deleting_a_workout_nulls_the_provenance_link_only(
    client: AsyncClient,
) -> None:
    # ON DELETE SET NULL, enforced by the database: the frozen snapshot in the
    # intent version has to survive the library entry it came from.
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    workout = await create_workout(client)
    session = (
        await client.post(
            SESSIONS,
            json={
                "date": "2026-08-10",
                "purpose": "sweet_spot",
                "workout_id": workout["id"],
            },
        )
    ).json()

    assert (await client.delete(f"{WORKOUTS}/{workout['id']}")).status_code == 204

    intent = (await client.get(f"{SESSIONS}/{session['id']}")).json()["intent"]
    assert intent["workout_id"] is None
    assert intent["summary"]["step_count"] == 7


async def test_deleting_a_session_takes_its_whole_intent_chain(
    client: AsyncClient,
) -> None:
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    session = (
        await client.post(
            SESSIONS,
            json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
        )
    ).json()
    await client.patch(f"{SESSIONS}/{session['id']}", json={"coach_notes": "note"})

    await client.delete(f"{SESSIONS}/{session['id']}")

    assert await scalar("SELECT count(*) FROM planned_session_intents") == 0


async def test_the_cascade_is_the_migration_schema_s_own(
    client: AsyncClient,
) -> None:
    # The test above deletes through the ORM, which removes the rows it has
    # loaded whatever the schema says. This one deletes in SQL, so the only
    # thing that can take the intent chain and the tags with their parents is
    # the ON DELETE CASCADE in migration 0003 — the schema that ships, rather
    # than the one `create_all` builds from the models in the unit suite.
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    await create_workout(client, tags=["bike", "z2"])
    await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
    )

    await execute("DELETE FROM planned_sessions")
    await execute("DELETE FROM workouts")

    assert await scalar("SELECT count(*) FROM planned_session_intents") == 0
    assert await scalar("SELECT count(*) FROM workout_tags") == 0


async def test_a_duplicate_intent_version_is_refused_by_the_database(
    client: AsyncClient,
) -> None:
    # The version chain is what the freeze rule is enforced through, so a
    # duplicate version number is a corruption of it.
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    session = (
        await client.post(
            SESSIONS,
            json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
        )
    ).json()

    engine = create_async_engine(get_settings().postgres.async_url)
    failed = False
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO planned_session_intents "
                    "(id, planned_session_id, version, edited_post_hoc, purpose, "
                    " success_criteria, pinned_anchor_versions, structure) "
                    "VALUES (gen_random_uuid(), :sid, 1, false, 'sweet_spot', "
                    " '[]'::jsonb, '{}'::jsonb, '{}'::jsonb)"
                ),
                {"sid": session["id"]},
            )
    except Exception:  # noqa: BLE001 - any driver error proves the constraint bit
        failed = True
    await engine.dispose()

    assert failed


# --- enum columns -------------------------------------------------------------


async def test_enum_columns_store_the_member_value(client: AsyncClient) -> None:
    await client.post(
        ANCHORS, json={"anchor_type": "ftp", "value": 250, "provenance": "estimated"}
    )
    await create_workout(client, name="Squat day", structure=LIFT)
    await client.post(
        SESSIONS,
        json={"date": "2026-08-10", "purpose": "sweet_spot", "structure": RIDE},
    )

    assert await scalar("SELECT discipline FROM workouts") == "strength"
    assert await scalar("SELECT purpose FROM planned_session_intents") == "sweet_spot"
    assert await scalar("SELECT status FROM planned_sessions") == "planned"
    assert await scalar("SELECT category FROM exercises WHERE id = 'back_squat'") == (
        "squat"
    )


# --- the catalogue ------------------------------------------------------------


async def test_the_catalogue_seeds_itself_on_first_access(
    client: AsyncClient,
) -> None:
    # The integration fixture truncates every table between tests, which is
    # exactly the situation a migration-seeded catalogue could not survive.
    assert await scalar("SELECT count(*) FROM exercises") == 0

    page = (await client.get(EXERCISES, params={"limit": 200})).json()

    assert page["total"] > 80
    assert await scalar("SELECT count(*) FROM exercises") == page["total"]
