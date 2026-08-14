"""The wellness table against a real Postgres — the dialect-specific half.

The unit suite runs on SQLite, so the three things the two dialects spell
differently are only really tested here: the `JSONB` columns holding the
confounder list and the per-region soreness map, the non-native enum `VARCHAR`
that has to store the member *value*, and the unique index that is the whole
of the one-row-per-day promise.
"""

import datetime as dt

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

DAYS = "/api/v1/wellness/days"
BACKFILL = "/api/v1/wellness/backfill"

TODAY = dt.date.today()


async def raw(sql: str) -> list[tuple[object, ...]]:
    """Read rows through raw SQL, bypassing the ORM's own conversion."""
    engine = create_async_engine(get_settings().postgres.async_url)
    async with engine.connect() as conn:
        rows = list((await conn.execute(text(sql))).all())
    await engine.dispose()
    return [tuple(row) for row in rows]


async def test_the_json_columns_round_trip_through_jsonb(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        f"{DAYS}/{TODAY.isoformat()}",
        json={
            "confounders": ["alcohol", "travel"],
            "soreness_by_region": {"quads": 3, "lower_back": 2},
        },
    )
    assert response.status_code == 200, response.text

    body = (await client.get(f"{DAYS}/{TODAY.isoformat()}")).json()
    assert body["confounders"] == ["alcohol", "travel"]
    assert body["soreness_by_region"] == {"quads": 3, "lower_back": 2}
    # JSONB, so Postgres can index and query it rather than reparsing text on
    # every read. `->>` only works on a JSON type, which is the assertion.
    [(tag,)] = await raw("SELECT confounders->>0 FROM wellness_days")
    assert tag == "alcohol"


async def test_enum_columns_store_the_member_value(client: AsyncClient) -> None:
    # The row must say `athlete_reported` and `sleeping`, the same spelling the
    # API, the OpenAPI schema and every payload use — not the Python member
    # names the ORM would default to.
    await client.patch(
        f"{DAYS}/{TODAY.isoformat()}",
        json={"hrv_ms": 58.0, "hrv_metric": "sdnn", "hrv_context": "sleeping"},
    )

    [(provenance, source, metric, context)] = await raw(
        "SELECT provenance, source, hrv_metric, hrv_context FROM wellness_days"
    )

    assert (provenance, source) == ("athlete_reported", "athlete")
    assert (metric, context) == ("sdnn", "sleeping")


async def test_one_row_per_day_is_held_by_the_database(
    client: AsyncClient,
) -> None:
    # Not by a code path that could forget: two writes to one date are one row,
    # and the unique index is what makes that true of anything that ever writes
    # to this table.
    await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={"fatigue": 3})
    await client.patch(f"{DAYS}/{TODAY.isoformat()}", json={"resting_hr_bpm": 46})

    [(count,)] = await raw("SELECT count(*) FROM wellness_days")
    assert count == 1

    [(unique,)] = await raw(
        "SELECT indisunique FROM pg_index "
        "WHERE indexrelid = 'ix_wellness_days_local_date'::regclass"
    )
    assert unique is True


async def test_a_backfill_of_a_year_lands_in_one_transaction(
    client: AsyncClient,
) -> None:
    # The migration case, at the size it actually arrives in — and against the
    # dialect it will actually run on.
    days = [
        {
            "local_date": (TODAY - dt.timedelta(days=offset + 1)).isoformat(),
            "resting_hr_bpm": 44 + offset % 6,
            "hrv_ms": 55.0 + offset % 9,
            "hrv_metric": "rmssd",
            "hrv_context": "sleeping",
        }
        for offset in range(365)
    ]

    response = await client.post(BACKFILL, json={"days": days})

    assert response.status_code == 200, response.text
    assert response.json()["outcomes"] == {"created": 365}
    [(count,)] = await raw("SELECT count(*) FROM wellness_days")
    assert count == 365


async def test_a_rejected_backfill_leaves_the_table_empty(
    client: AsyncClient,
) -> None:
    days = [
        {"local_date": (TODAY - dt.timedelta(days=1)).isoformat(), "fatigue": 3},
        # A future date, refused by the domain after the first day validated.
        {"local_date": (TODAY + dt.timedelta(days=1)).isoformat(), "fatigue": 3},
    ]

    response = await client.post(BACKFILL, json={"days": days})

    assert response.status_code == 422, response.text
    [(count,)] = await raw("SELECT count(*) FROM wellness_days")
    assert count == 0
