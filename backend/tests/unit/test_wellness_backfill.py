"""The batch write: one transaction, errors named by date, and a real dry run.

Its own module because its invariant is a *negative* one — a batch containing
one bad day leaves **no** rows behind — and the cases that prove it are all
"assert a count of zero", not "assert a non-2xx".
"""

import datetime as dt
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.domain.wellness import MAX_BACKFILL_DAYS
from app.persistence.audit import AuditLogEntry
from app.persistence.wellness import WellnessDayRow

BACKFILL = "/api/v1/wellness/backfill"
DAYS = "/api/v1/wellness/days"

#: Today on the athlete's clock — the same one `WellnessService.local_today`
#: reads, because that is the day these tests are about. Not `dt.date.today()`,
#: which is the *container's* clock and a third answer to the question
#: (issue #62); the DTZ rules now refuse it.
TODAY = athlete_today()


def history(count: int, *, first: int = 60) -> list[dict[str, Any]]:
    """``count`` consecutive past days of plausible watch readings."""
    return [
        {
            "date": (TODAY - dt.timedelta(days=first - offset)).isoformat(),
            "resting_hr_bpm": 46 + offset % 4,
            "hrv_ms": 58.0 + offset % 7,
            "hrv_metric": "rmssd",
            "hrv_context": "sleeping",
            "sleep_duration_s": 25_200 + 600 * (offset % 5),
        }
        for offset in range(count)
    ]


def as_payload(days: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """The HTTP body, whose per-day key is `local_date` rather than `date`."""
    return {
        "days": [
            {**{k: v for k, v in day.items() if k != "date"}, "local_date": day["date"]}
            for day in days
        ],
        **extra,
    }


async def count(session: AsyncSession) -> int:
    """How many wellness days are stored."""
    session.expire_all()
    return (await session.scalar(select(func.count()).select_from(WellnessDayRow))) or 0


# --- one transaction (AC-27) --------------------------------------------------


async def test_a_batch_lands_whole(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(BACKFILL, json=as_payload(history(30)))

    assert response.status_code == 200, response.text
    assert response.json()["outcomes"] == {"created": 30}
    assert await count(db_session) == 30


async def test_one_bad_day_leaves_no_rows_behind_and_names_the_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    days = history(10)
    # An SpO2 of 97 rather than 0.97 — the units mistake that produces a
    # perfectly plausible-looking number.
    days[6]["spo2"] = 97
    offending = days[6]["date"]

    response = await client.post(BACKFILL, json=as_payload(days))

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert offending in str(detail)
    assert "spo2" in str(detail)
    assert await count(db_session) == 0, "a partial migration is worse than none"


async def test_a_future_day_anywhere_in_the_batch_refuses_the_whole_batch(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    days = [
        *history(5),
        {"date": (TODAY + dt.timedelta(days=1)).isoformat(), "resting_hr_bpm": 46},
    ]

    response = await client.post(BACKFILL, json=as_payload(days))

    assert response.status_code == 422, response.text
    assert "has not happened yet" in response.json()["detail"]
    assert await count(db_session) == 0


async def test_a_repeated_date_is_refused_rather_than_letting_one_win(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    repeated = (TODAY - dt.timedelta(days=3)).isoformat()
    days = [
        {"date": repeated, "resting_hr_bpm": 46},
        {"date": repeated, "resting_hr_bpm": 52},
    ]

    response = await client.post(BACKFILL, json=as_payload(days))

    assert response.status_code == 422, response.text
    assert repeated in response.json()["detail"]
    assert await count(db_session) == 0


async def test_a_batch_past_the_ceiling_is_refused_with_the_ceiling_named(
    client: AsyncClient,
) -> None:
    response = await client.post(
        BACKFILL, json=as_payload(history(MAX_BACKFILL_DAYS + 1, first=400))
    )

    assert response.status_code == 422, response.text


# --- create, update, and the diff (AC-28) -------------------------------------


async def test_a_mixed_batch_reports_per_day_whether_it_created_or_updated(
    client: AsyncClient,
) -> None:
    await client.post(BACKFILL, json=as_payload(history(3)))

    days = history(5)
    response = await client.post(BACKFILL, json=as_payload(days))

    body = response.json()
    assert body["outcomes"] == {"updated": 3, "created": 2}
    outcomes = {day["local_date"]: day["outcome"] for day in body["days"]}
    assert outcomes[(TODAY - dt.timedelta(days=60)).isoformat()] == "updated"
    assert outcomes[(TODAY - dt.timedelta(days=56)).isoformat()] == "created"


async def test_updating_never_discards_a_field_the_batch_did_not_mention(
    client: AsyncClient,
) -> None:
    date = (TODAY - dt.timedelta(days=10)).isoformat()
    await client.post(
        BACKFILL,
        json=as_payload([{"date": date, "resting_hr_bpm": 46, "weight_kg": 78.0}]),
    )

    await client.post(BACKFILL, json=as_payload([{"date": date, "resting_hr_bpm": 48}]))

    body = (await client.get(f"{DAYS}/{date}")).json()
    assert body["resting_hr_bpm"] == 48
    assert body["weight_kg"] == 78.0


async def test_a_re_run_of_an_identical_batch_reports_an_empty_diff(
    client: AsyncClient,
) -> None:
    days = history(3)
    await client.post(BACKFILL, json=as_payload(history(3)))

    body = (await client.post(BACKFILL, json=as_payload(days))).json()

    assert all(day["changed"] == {} for day in body["days"])


# --- the dry run (AC-29) ------------------------------------------------------


async def test_a_dry_run_writes_nothing_and_reports_what_the_real_call_would_do(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    days = history(12)

    preview = (
        await client.post(BACKFILL, json=as_payload(history(12), dry_run=True))
    ).json()

    assert preview["dry_run"] is True
    assert await count(db_session) == 0

    real = (await client.post(BACKFILL, json=as_payload(days))).json()
    assert real["outcomes"] == preview["outcomes"]
    assert [day["local_date"] for day in real["days"]] == [
        day["local_date"] for day in preview["days"]
    ]
    assert [day["changed"] for day in real["days"]] == [
        day["changed"] for day in preview["days"]
    ]


async def test_a_dry_run_refuses_exactly_what_the_write_refuses(
    client: AsyncClient,
) -> None:
    # The #17 invariant: bounds are domain rules on the shared path, so a dry
    # run cannot pass what the write then fails on.
    def bad() -> list[dict[str, Any]]:
        days = history(4)
        days[2]["hrv_ms"] = 9_000
        return days

    good = await client.post(BACKFILL, json=as_payload(history(4), dry_run=True))
    preview = await client.post(BACKFILL, json=as_payload(bad(), dry_run=True))
    real = await client.post(BACKFILL, json=as_payload(bad()))

    assert good.status_code == 200
    assert preview.status_code == real.status_code == 422


# --- the audit trail (AC-51) --------------------------------------------------


async def test_a_batch_is_audited_once_carrying_every_days_diff(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.post(BACKFILL, json=as_payload(history(4)))

    rows = (
        (
            await db_session.execute(
                select(AuditLogEntry).where(
                    AuditLogEntry.action == "wellness.backfilled"
                )
            )
        )
        .scalars()
        .all()
    )

    # One row, not four: the cap counts audit rows, and a batch is one
    # decision. The trail is still complete — every day's diff is in it.
    assert len(rows) == 1
    payload = rows[0].payload_json
    assert payload["day_count"] == 4
    assert len(payload["days"]) == 4
    assert all(day["changed"] for day in payload["days"])


async def test_a_null_in_a_batch_day_clears_that_field(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The batch follows the per-day contract in both directions: omitted leaves
    # alone, `null` clears. Documented in both write tools' docstrings, so it
    # needs a test that fails if it stops being true.
    date = (TODAY - dt.timedelta(days=5)).isoformat()
    await client.post(
        BACKFILL,
        json=as_payload([{"date": date, "resting_hr_bpm": 46, "fatigue": 3}]),
    )

    await client.post(
        BACKFILL, json=as_payload([{"date": date, "resting_hr_bpm": None}])
    )

    body = (await client.get(f"{DAYS}/{date}")).json()
    assert body["resting_hr_bpm"] is None
    assert body["fatigue"] == 3


async def test_a_batch_day_whose_last_value_is_cleared_is_retracted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    date = (TODAY - dt.timedelta(days=5)).isoformat()
    await client.post(BACKFILL, json=as_payload([{"date": date, "fatigue": 3}]))

    response = await client.post(
        BACKFILL, json=as_payload([{"date": date, "fatigue": None}])
    )

    assert response.json()["outcomes"] == {"retracted": 1}
    assert await count(db_session) == 0
