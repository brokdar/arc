"""What actually happened, beside what was planned (A3.3's second half, A5.4).

The week rail's completed columns. Two things are pinned here and neither is
arithmetic: **planned and completed never merge into one number**, and every
completed total carries the coverage pair that says how many sessions could
not contribute to it. A week of five rides where two have no artefact must not
read as a light week.

The polarization index has its own rule and its own test: exactly one channel
per session, or a session's duration is counted twice and the whole
distribution is meaningless.
"""

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.plan import PlanService, PlanWeek
from tests.unit.golden_fit import golden
from tests.unit.prescriptions import EASY_RIDE

WEEK = "/api/v1/plan/week"
ANCHORS = "/api/v1/anchors"
MANUAL = "/api/v1/manual-sessions"
MATCHES = "/api/v1/matches"
PLANNED = "/api/v1/planned-sessions"
UPLOAD = "/api/v1/ingest/upload"

#: The Monday of the week the golden files were recorded in.
MONDAY = dt.date(2026, 5, 4)


async def anchors(client: AsyncClient) -> None:
    """Everything the metric set can pin, so no load is missing by accident."""
    for anchor_type, value in (
        ("ftp", 250.0),
        ("lthr", 165.0),
        ("max_hr", 190.0),
        ("resting_hr", 50.0),
    ):
        response = await client.post(
            ANCHORS,
            json={
                "anchor_type": anchor_type,
                "value": value,
                "provenance": "estimated",
                "effective_date": "2026-01-01",
            },
        )
        assert response.status_code == 201, response.text
    await client.patch("/api/v1/athlete", json={"sex": "male"})


async def ingest(client: AsyncClient, name: str, golden_name: str) -> str:
    """Upload one golden file and return the session it created."""
    response = await client.post(
        UPLOAD,
        files={
            "file": (name, golden(golden_name).read_bytes(), "application/octet-stream")
        },
    )
    assert response.status_code == 200, response.text
    [session_id] = response.json()["session_ids"]
    return session_id


async def gym(client: AsyncClient, date: dt.date) -> str:
    """A typed-in strength session on ``date``."""
    response = await client.post(
        MANUAL,
        json={
            "start_time": f"{date.isoformat()}T17:30:00+00:00",
            "timezone": "UTC",
            "duration_s": 3_600,
            "sets": [
                {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0},
                {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def week(client: AsyncClient, start: dt.date = MONDAY) -> dict[str, Any]:
    """Fetch a week, asserting it was served."""
    response = await client.get(WEEK, params={"start": start.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


async def test_an_empty_week_has_no_completed_totals_rather_than_zeros(
    data_root: Path, client: AsyncClient
) -> None:
    # A zero would read as a rest week; nothing happened is a different fact.
    payload = await week(client)

    assert payload["completed_session_count"] == 0
    assert payload["completed_duration_s"] is None
    assert payload["completed_load"] is None
    assert payload["completed_load_sessions_counted"] == 0
    assert payload["completed_polarization_index"] is None
    assert payload["completed_polarization_rule"]


async def test_a_recorded_ride_lands_on_its_own_day(
    data_root: Path, client: AsyncClient
) -> None:
    await anchors(client)
    await ingest(client, "ride.fit", "outdoor_ride.fit")  # 2026-05-04

    payload = await week(client)

    monday, tuesday, *_ = payload["days"]
    assert monday["date"] == "2026-05-04"
    assert monday["completed_session_count"] == 1
    assert monday["completed_duration_s"] > 0
    assert monday["completed_load"] > 0
    assert tuesday["completed_session_count"] == 0
    assert tuesday["completed_load"] is None


async def test_the_completed_duration_is_recording_time(
    data_root: Path, client: AsyncClient
) -> None:
    # The week and the session log must not answer this differently: pauses
    # are out of both (A4.4).
    await anchors(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")
    session = (await client.get(f"/api/v1/sessions/{session_id}")).json()

    payload = await week(client)

    assert payload["completed_duration_s"] == pytest.approx(session["recording_time_s"])


async def test_the_honesty_case_counts_what_it_could_and_says_what_it_could_not(
    data_root: Path, client: AsyncClient
) -> None:
    """One power ride, one HR-only session, one typed-in gym session.

    The gym session has kilograms and no training load — kilograms are a
    different axis (v2 §5.4) — so it counts in the session total, contributes
    its duration, and counts as **uncounted** against the load. Without the
    pair, a week with a gym session in it would read as lighter than it was.
    """
    await anchors(client)
    await ingest(client, "ride.fit", "outdoor_ride.fit")  # power + HR
    await ingest(client, "gym.fit", "strength_watch.fit")  # HR only
    await gym(client, MONDAY + dt.timedelta(days=2))  # no stream at all

    payload = await week(client)

    assert payload["completed_session_count"] == 3
    assert payload["completed_load_sessions_counted"] == 2
    assert payload["completed_load_sessions_uncounted"] == 1
    assert payload["completed_load"] > 0
    # And every session contributed its duration, load or no load.
    assert payload["completed_duration_s"] > 3_600


async def test_an_hr_only_session_is_loaded_from_the_hr_model(
    data_root: Path, client: AsyncClient
) -> None:
    await anchors(client)
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")

    metrics = (await client.get(f"/api/v1/sessions/{session_id}")).json()["metrics"]

    assert metrics["load"]["load_basis"] == "hr"
    assert metrics["load"]["power_load"] is None
    assert metrics["load"]["hr_load"] > 0
    # A5.2: the rule is a sentence, not an inference the client has to make.
    assert metrics["load"]["load_basis_rule"]


async def test_a_session_with_no_artefact_still_counts_as_a_session(
    data_root: Path, client: AsyncClient
) -> None:
    # No anchors at all: neither load model is computable, so the ride has an
    # artefact with a `not_assessed` load. It happened; it has a duration.
    await ingest(client, "ride.fit", "outdoor_ride.fit")

    payload = await week(client)

    assert payload["completed_session_count"] == 1
    assert payload["completed_duration_s"] > 0
    assert payload["completed_load"] is None
    assert payload["completed_load_sessions_counted"] == 0
    assert payload["completed_load_sessions_uncounted"] == 1


async def test_the_discipline_rows_split_the_completed_side_too(
    data_root: Path, client: AsyncClient
) -> None:
    await anchors(client)
    await ingest(client, "ride.fit", "outdoor_ride.fit")
    await gym(client, MONDAY + dt.timedelta(days=2))

    payload = await week(client)

    rows = {row["discipline"]: row for row in payload["by_discipline"]}
    assert set(rows) == {"cycling", "strength"}
    assert rows["cycling"]["completed_session_count"] == 1
    assert rows["cycling"]["completed_load"] > 0
    assert rows["strength"]["completed_session_count"] == 1
    assert rows["strength"]["completed_load"] is None
    assert rows["strength"]["completed_load_sessions_uncounted"] == 1
    # Planned and completed stay in their own columns.
    assert rows["cycling"]["planned_load"] is None
    assert rows["cycling"]["session_count"] == 0


async def test_the_polarization_index_counts_one_channel_per_session(
    data_root: Path, client: AsyncClient
) -> None:
    """A5.4's one rule, and the sentence that has to travel with the number.

    The outdoor ride has both power and heart-rate distributions. Counting
    both would total 2x its duration and put the index somewhere meaningless;
    the payload states which channel was counted and how many sessions
    contributed at all.
    """
    await anchors(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")
    metrics = (await client.get(f"/api/v1/sessions/{session_id}")).json()["metrics"]
    power_zones = metrics["time_in_zone"]["power"]
    assert power_zones["total_s"] is not None
    assert metrics["time_in_zone"]["hr"]["total_s"] is not None

    payload = await week(client)

    assert payload["completed_polarization_sessions_counted"] == 1
    assert payload["completed_polarization_sessions_uncounted"] == 0
    assert "one channel per session" in payload["completed_polarization_rule"]
    # Present or explained, never silently absent.
    assert (payload["completed_polarization_index"] is None) == (
        payload["completed_polarization_not_assessed"] is not None
    )


async def test_a_week_with_no_zone_time_explains_its_missing_index(
    data_root: Path, client: AsyncClient
) -> None:
    # A typed-in gym session has no stream, so no channel produced a
    # distribution: the index is missing and says which band was empty.
    await gym(client, MONDAY)

    payload = await week(client)

    assert payload["completed_polarization_index"] is None
    assert payload["completed_polarization_not_assessed"]
    assert payload["completed_polarization_sessions_counted"] == 0
    assert payload["completed_polarization_sessions_uncounted"] == 1


# --- what no card claims (#49) ------------------------------------------------


async def plan(client: AsyncClient, date: dt.date, **overrides: Any) -> dict[str, Any]:
    """Plan an easy ride on ``date``, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": date.isoformat(),
        "purpose": "endurance",
        "structure": EASY_RIDE,
    } | overrides
    response = await client.post(PLANNED, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def ride(client: AsyncClient, date: dt.date) -> str:
    """A typed-in cycling session on ``date``, an hour long."""
    response = await client.post(
        MANUAL,
        json={
            "start_time": f"{date.isoformat()}T09:00:00+00:00",
            "timezone": "UTC",
            "duration_s": 3_600,
            "discipline": "cycling",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def link(client: AsyncClient, session_id: str, planned_id: str) -> None:
    """Match a recording to a card by hand, the way the athlete does."""
    response = await client.post(
        MATCHES,
        json={"session_id": session_id, "planned_session_id": planned_id},
    )
    assert response.status_code == 201, response.text


async def projection(
    session_factory: async_sessionmaker[AsyncSession], start: dt.date = MONDAY
) -> PlanWeek:
    """The week as `PlanService` returns it — no HTTP, no MCP, no schema."""
    async with session_factory() as session:
        return await PlanService.from_session(session).week(start)


def claimed(week: PlanWeek) -> set[uuid.UUID]:
    """Every session id a card in this projection carries."""
    return {
        card.matched_session_id
        for day in week.days
        for card in day.sessions
        if card.matched_session_id is not None
    }


async def test_a_recording_is_listed_exactly_when_no_card_claims_it(
    data_root: Path,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The rule, stated as the biconditional it is.

    On the projection itself: what a card claims decides this, not the
    recording's own `match_status`, which is a fact about the ride and not
    about this window.
    """
    await anchors(client)
    await plan(client, MONDAY)
    matched = await ride(client, MONDAY)  # matching links this one to the card
    loose = await ride(client, MONDAY + dt.timedelta(days=2))

    week = await projection(session_factory)

    listed = {entry.id for entry in week.unplanned_sessions}
    for session_id in (uuid.UUID(matched), uuid.UUID(loose)):
        assert (session_id in listed) is (session_id not in claimed(week))
    assert listed == {uuid.UUID(loose)}
    assert claimed(week) == {uuid.UUID(matched)}


async def test_a_card_matched_outside_the_window_invents_no_entry(
    data_root: Path,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The list is drawn from the recordings *in* the window, never from what
    # the cards point at: a card claiming last week's ride must not conjure
    # that ride into this week.
    await anchors(client)
    outside = await ride(client, MONDAY - dt.timedelta(days=3))
    await link(client, outside, (await plan(client, MONDAY))["id"])

    week = await projection(session_factory)

    assert claimed(week) == {uuid.UUID(outside)}
    assert week.unplanned_sessions == ()
    assert week.completed_session_count == 0


async def test_two_recordings_on_one_day_split_by_what_the_card_claims(
    data_root: Path,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await anchors(client)
    await plan(client, MONDAY)
    first = await ride(client, MONDAY)  # matched to the card on record
    second = await ride(client, MONDAY)

    week = await projection(session_factory)

    assert claimed(week) == {uuid.UUID(first)}
    assert [entry.id for entry in week.unplanned_sessions] == [uuid.UUID(second)]
    assert week.completed_session_count == 2
