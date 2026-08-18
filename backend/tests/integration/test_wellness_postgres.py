"""The wellness table against a real Postgres — the dialect-specific half.

The unit suite runs on SQLite, so the three things the two dialects spell
differently are only really tested here: the `JSONB` columns holding the
confounder list and the per-region soreness map, the non-native enum `VARCHAR`
that has to store the member *value*, and the unique index that is the whole
of the one-row-per-day promise.
"""

import asyncio
import datetime as dt

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.clock import athlete_today
from app.core.config import get_settings
from app.domain.actor import Actor
from app.persistence.db import session_scope
from app.services.wellness import WellnessService

DAYS = "/api/v1/wellness/days"
BACKFILL = "/api/v1/wellness/backfill"

#: Today on the athlete's clock — the same one `WellnessService.local_today`
#: reads, because that is the day these tests are about. Not `dt.date.today()`,
#: which is the *container's* clock and a third answer to the question
#: (issue #62); the DTZ rules now refuse it.
TODAY = athlete_today()


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


async def test_two_writes_racing_to_create_one_day_both_land(
    client: AsyncClient,
) -> None:
    """Concurrent creates for one date merge instead of one of them failing.

    Found by running the fuzzer with `--workers`: two writes for a date with no
    row both read "absent", both INSERT, and `ix_wellness_days_local_date`
    refused the second — which reached the client as a 409 quoting a Postgres
    constraint. Two browser tabs, or the athlete's form saving while the agent
    transcribes the same morning, is all it takes.

    Here rather than in the unit suite because the unit suite is one in-memory
    SQLite connection shared by every session: two "concurrent" requests
    serialize on it and the race cannot happen. Two real connections is
    dialect-specific behaviour, which is what this file is for.
    """
    date = (TODAY - dt.timedelta(days=1)).isoformat()

    first, second = await asyncio.gather(
        client.patch(f"{DAYS}/{date}", json={"resting_hr_bpm": 46}),
        client.patch(f"{DAYS}/{date}", json={"fatigue": 3}),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    # The loser re-read and applied itself on top of the winner, so the result
    # is what arriving second sequentially would have produced: both fields
    # present and neither write lost.
    body = (await client.get(f"{DAYS}/{date}")).json()
    assert body["resting_hr_bpm"] == 46
    assert body["fatigue"] == 3
    [(count,)] = await raw("SELECT count(*) FROM wellness_days")
    assert count == 1


# --- the daily prompt: one row per date, held by the database ---------------------


async def raise_prompt(local_date: dt.date) -> None:
    """Raise one day's prompt through the service, on a real connection."""
    async with session_scope() as session:
        await WellnessService.from_session(session).raise_prompt(
            local_date, actor=Actor.system()
        )


async def test_one_prompt_per_day_is_held_by_the_database() -> None:
    """The decision: a unique constraint, not scheduler discipline.

    Here rather than only in the unit suite because this is what the constraint
    *is* on the dialect it will run on — an index Postgres refuses to duplicate
    a row in, not a pre-check the sweep performs on itself.
    """
    await raise_prompt(TODAY)
    await raise_prompt(TODAY)

    [(count,)] = await raw("SELECT count(*) FROM wellness_prompts")
    assert count == 1

    [(unique,)] = await raw(
        "SELECT indisunique FROM pg_index "
        "WHERE indexrelid = 'uq_wellness_prompts_local_date'::regclass"
    )
    assert unique is True


async def test_two_sweeps_racing_to_raise_one_day_leave_one_row() -> None:
    """Two connections, both finding no prompt, both inserting.

    The unit suite cannot stage this: it shares one in-memory SQLite
    connection, so two "concurrent" raises serialize on it. The loser here
    re-reads the winner's row rather than surfacing a constraint violation.
    """
    await asyncio.gather(raise_prompt(TODAY), raise_prompt(TODAY))

    [(count,)] = await raw("SELECT count(*) FROM wellness_prompts")
    assert count == 1


async def test_the_prompt_status_column_stores_the_member_value() -> None:
    await raise_prompt(TODAY)

    [(status,)] = await raw("SELECT status FROM wellness_prompts")

    assert status == "pending"
