"""`GET /api/v1/fitness` — fitness, fatigue and form over the recorded sessions.

Sessions are logged through the athlete's own API, so `local_date` is derived
the way the application derives it. Their load is then set on the metric
artefact the logging wrote — a manual session has no stream, so its load is
`not_assessed` until something prices it, and writing the priced block is the
state a ride's ingest leaves behind (`app.domain.session_analysis`'s `load`
block, field for field). A session left unpriced is exactly what the
application stores for a gym session with no heart rate.
"""

import datetime as dt
import math
import tomllib
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_today
from app.persistence.metrics import SessionMetricsRow

FITNESS = "/api/v1/fitness"
MANUAL = "/api/v1/manual-sessions"

ALPHA_C = 1 - math.exp(-1 / 42)
ALPHA_A = 1 - math.exp(-1 / 7)

DAY = dt.date(2026, 3, 2)
NEXT = (DAY + dt.timedelta(days=1)).isoformat()


async def log(
    client: AsyncClient,
    *,
    start_time: str = f"{DAY.isoformat()}T09:00:00Z",
    timezone: str = "UTC",
    discipline: str = "cycling",
) -> uuid.UUID:
    """Log a session through the API and return its id."""
    response = await client.post(
        MANUAL,
        json={
            "start_time": start_time,
            "timezone": timezone,
            "duration_s": 3_600,
            "discipline": discipline,
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def price(
    db_session: AsyncSession, session_id: uuid.UUID, load: float, basis: str
) -> None:
    """Put a selected load on the session's current metric artefact."""
    row = (
        await db_session.execute(
            select(SessionMetricsRow).where(
                SessionMetricsRow.session_id == session_id,
                SessionMetricsRow.superseded_by.is_(None),
            )
        )
    ).scalar_one()
    row.payload = {
        **row.payload,
        "load": {
            "not_assessed": None,
            "training_load": load,
            "load_basis": basis,
            "load_basis_rule": "set by the test",
            "power_load": load if basis == "power" else None,
            "hr_load": load if basis == "hr" else None,
        },
    }
    await db_session.commit()


async def day_of(client: AsyncClient, day: dt.date = DAY) -> dict[str, Any]:
    """The one point the series has on ``day``."""
    response = await client.get(
        FITNESS,
        params={
            "from": day.isoformat(),
            "to": (day + dt.timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    [point] = response.json()["points"]
    return point


# --- what feeds which average --------------------------------------------------------


async def test_strength_feeds_fatigue_not_fitness(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ride = await log(client, discipline="cycling")
    gym = await log(client, start_time=f"{DAY}T18:00:00Z", discipline="strength")
    await price(db_session, ride, 80.0, "power")
    await price(db_session, gym, 30.0, "hr")

    point = await day_of(client)

    assert point["local_date"] == DAY.isoformat()
    assert point["ctl"]["value"] == pytest.approx(80 * ALPHA_C, abs=1e-9)
    assert point["atl"]["value"] == pytest.approx((80 + 30) * ALPHA_A, abs=1e-9)
    assert point["load"] == pytest.approx(110.0)
    assert point["fitness_load"] == pytest.approx(80.0)
    assert point["n"] == 2
    assert point["load_incomplete"] is False


async def test_an_unpriced_strength_session_marks_its_day_and_adds_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ride = await log(client)
    await log(client, start_time=f"{DAY}T18:00:00Z", discipline="strength")
    await price(db_session, ride, 50.0, "power")

    point = await day_of(client)

    assert point["load_incomplete"] is True
    assert point["uncounted"] == 1
    assert point["n"] == 1
    # Nothing was added for it — not a silent zero, and not a guess.
    assert point["atl"]["value"] == pytest.approx(50 * ALPHA_A, abs=1e-9)
    assert point["load"] == pytest.approx(50.0)


async def test_a_day_of_only_unpriced_sessions_has_a_null_load(
    client: AsyncClient,
) -> None:
    await log(client, discipline="strength")

    point = await day_of(client)

    assert point["load_incomplete"] is True
    assert point["load"] is None
    assert point["n"] == 0
    assert point["atl"]["value"] == 0.0


async def test_two_rides_on_one_day_add_before_smoothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await log(client)
    second = await log(client, start_time=f"{DAY}T17:00:00Z")
    await price(db_session, first, 30.0, "power")
    await price(db_session, second, 50.0, "power")

    point = await day_of(client)

    assert point["ctl"]["value"] == pytest.approx(80 * ALPHA_C, abs=1e-9)
    assert point["n"] == 2


async def test_a_late_evening_session_lands_on_its_local_day(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # 23:30 in New York on 2 March is 04:30 UTC on the 3rd.
    ride = await log(
        client, start_time=f"{NEXT}T04:30:00Z", timezone="America/New_York"
    )
    await price(db_session, ride, 60.0, "power")

    response = await client.get(FITNESS, params={"from": DAY.isoformat()})
    first, second = response.json()["points"][:2]

    assert first["local_date"] == DAY.isoformat()
    assert first["fitness_load"] == pytest.approx(60.0)
    assert second["fitness_load"] == 0.0


# --- the shape -----------------------------------------------------------------------


async def test_each_value_is_a_display_only_metric_object(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ride = await log(client)
    await price(db_session, ride, 100.0, "power")

    body = (
        await client.get(FITNESS, params={"from": DAY.isoformat(), "to": NEXT})
    ).json()

    [point] = body["points"]
    for key in ("ctl", "atl", "tsb"):
        assert point[key]["display_only"] is True
        assert isinstance(point[key]["value"], float)
    assert point["tsb"]["value"] == pytest.approx(
        point["ctl"]["value"] - point["atl"]["value"], abs=1e-12
    )
    assert point["day"] == 1
    assert body["form_lag_days"] == 0
    maturity = body["maturity"]
    assert maturity["first_session"] == DAY.isoformat()
    assert maturity["as_of"] == DAY.isoformat()
    assert maturity["day"] == 1
    assert maturity["ctl"]["mature"] is False
    assert maturity["ctl"]["matures_on_day"] == 97
    assert maturity["ctl"]["matures_on"] == (DAY + dt.timedelta(days=96)).isoformat()
    assert maturity["atl"]["matures_on_day"] == 17
    assert maturity["atl"]["matures_on"] == (DAY + dt.timedelta(days=16)).isoformat()
    for key in ("ctl", "atl", "tsb"):
        assert body["explanations"][key]["display_only"] is True
        assert body["explanations"][key]["formula"]


async def test_before_the_first_session_there_are_no_points(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ride = await log(client)
    await price(db_session, ride, 40.0, "power")

    body = (
        await client.get(
            FITNESS,
            params={
                "from": (DAY - dt.timedelta(days=10)).isoformat(),
                "to": (DAY + dt.timedelta(days=3)).isoformat(),
            },
        )
    ).json()

    assert [point["local_date"] for point in body["points"]] == [
        (DAY + dt.timedelta(days=offset)).isoformat() for offset in range(3)
    ]


async def test_a_range_before_the_first_session_is_empty_not_missing(
    client: AsyncClient,
) -> None:
    await log(client)

    response = await client.get(
        FITNESS, params={"from": "2026-01-01", "to": "2026-02-01"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["points"] == []
    assert body["maturity"]["day"] is None
    assert body["maturity"]["first_session"] == DAY.isoformat()
    assert body["maturity"]["ctl"]["mature"] is False


async def test_no_history_is_an_empty_series_with_a_maturity(
    client: AsyncClient,
) -> None:
    body = (await client.get(FITNESS)).json()

    assert body["points"] == []
    assert body["maturity"]["day"] is None
    assert body["maturity"]["first_session"] is None
    assert body["maturity"]["ctl"]["matures_on_day"] == 97


async def test_a_range_into_the_future_stops_at_today(client: AsyncClient) -> None:
    today = athlete_today()
    await log(client, start_time=f"{today - dt.timedelta(days=2)}T09:00:00Z")

    body = (
        await client.get(
            FITNESS,
            params={
                "from": (today - dt.timedelta(days=5)).isoformat(),
                "to": (today + dt.timedelta(days=30)).isoformat(),
            },
        )
    ).json()

    dates = [point["local_date"] for point in body["points"]]
    assert dates[-1] == today.isoformat()
    assert all(date <= today.isoformat() for date in dates)
    assert body["maturity"]["as_of"] == today.isoformat()


async def test_with_no_bounds_the_series_runs_from_the_first_session_to_today(
    client: AsyncClient,
) -> None:
    today = athlete_today()
    await log(client, start_time=f"{today - dt.timedelta(days=4)}T09:00:00Z")

    body = (await client.get(FITNESS)).json()

    assert len(body["points"]) == 5
    assert body["maturity"]["day"] == 5


async def test_from_after_to_is_refused_naming_both(client: AsyncClient) -> None:
    response = await client.get(
        FITNESS, params={"from": "2026-03-10", "to": "2026-03-01"}
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "from (2026-03-10)" in detail
    assert "to (2026-03-01)" in detail


async def test_an_empty_range_is_legal(client: AsyncClient) -> None:
    await log(client)

    response = await client.get(
        FITNESS, params={"from": "2026-03-02", "to": "2026-03-02"}
    )

    assert response.status_code == 200
    assert response.json()["points"] == []


async def test_an_anonymous_caller_is_refused(anon_client: AsyncClient) -> None:
    response = await anon_client.get(FITNESS)

    assert response.status_code == 401


def test_the_bounds_are_optional_and_never_nullable(app: FastAPI) -> None:
    parameters = {
        parameter["name"]: parameter
        for parameter in app.openapi()["paths"][FITNESS]["get"]["parameters"]
    }

    assert set(parameters) == {"from", "to"}
    for parameter in parameters.values():
        assert parameter.get("required", False) is False
        schema = parameter["schema"]
        assert schema.get("format") == "date", schema
        assert "anyOf" not in schema, "a query parameter must not advertise null"


def test_schemathesis_is_told_this_operation_refuses_an_inverted_range() -> None:
    config = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "schemathesis.toml").read_text()
    )
    entry = next(
        (
            row
            for row in config["operations"]
            if row.get("include-name") == f"GET {FITNESS}"
        ),
        None,
    )

    assert entry is not None
    assert 422 in entry["checks"]["positive_data_acceptance"]["expected-statuses"]
