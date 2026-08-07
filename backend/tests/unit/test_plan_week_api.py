"""The calendar's week view through HTTP.

What is pinned here is the window and the card. The window is seven
consecutive days including the empty ones, inclusive at both ends, and taken
literally from ``?start=`` rather than snapped to a Monday. The card is what a
calendar renders — duration derived from the frozen step tree, the purpose,
the status, the one-line intent — and nothing that belongs to the session
sheet, which fetches the session itself.
"""

import datetime as dt
from typing import Any

import pytest
from httpx import AsyncClient

WEEK = "/api/v1/plan/week"
SESSIONS = "/api/v1/planned-sessions"
ANCHORS = "/api/v1/anchors"
WORKOUTS = "/api/v1/workouts"

#: A Monday, so a window starting here is also a plan week.
MONDAY = dt.date(2026, 8, 10)

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
#: 600 + 3 × (480 + 240).
RIDE_DURATION_S = 2_760

DISTANCE_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {"kind": "steady", "distance_m": 40_000, "role": "work"},
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


async def append_ftp(client: AsyncClient, value: float = 250) -> str:
    """Append an FTP anchor and return its version id."""
    response = await client.post(
        ANCHORS,
        json={"anchor_type": "ftp", "value": value, "provenance": "estimated"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def plan(client: AsyncClient, date: dt.date, **overrides: Any) -> dict[str, Any]:
    """Plan a session on ``date``, asserting it was accepted."""
    payload: dict[str, Any] = {
        "date": date.isoformat(),
        "purpose": "sweet_spot",
        "structure": RIDE,
    } | overrides
    response = await client.post(SESSIONS, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def week(client: AsyncClient, start: dt.date | None = None) -> dict[str, Any]:
    """Fetch a week, asserting it was served."""
    params = {} if start is None else {"start": start.isoformat()}
    response = await client.get(WEEK, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def dates(payload: dict[str, Any]) -> list[str]:
    """The dates of the days in a week payload, in order."""
    return [day["date"] for day in payload["days"]]


def cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every session card in a week payload, day order preserved."""
    return [card for day in payload["days"] for card in day["sessions"]]


# --- the window ---------------------------------------------------------------


async def test_a_week_is_seven_consecutive_days(client: AsyncClient) -> None:
    payload = await week(client, MONDAY)

    assert payload["start"] == "2026-08-10"
    assert payload["end"] == "2026-08-16"
    assert dates(payload) == [f"2026-08-{day}" for day in (10, 11, 12, 13, 14, 15, 16)]


async def test_an_empty_week_still_has_its_seven_days(client: AsyncClient) -> None:
    # A calendar renders a grid: the days are the answer, the sessions are
    # what is on them.
    payload = await week(client, MONDAY)

    assert [day["sessions"] for day in payload["days"]] == [[]] * 7
    assert payload["session_count"] == 0
    # No session contributed a duration, so there is no duration — a zero
    # here would read as a week of rest rather than as a week of nothing.
    assert payload["planned_duration_s"] is None
    assert payload["duration_sessions_counted"] == 0
    assert payload["duration_sessions_uncounted"] == 0


async def test_both_ends_of_the_window_are_included(client: AsyncClient) -> None:
    await append_ftp(client)
    first = await plan(client, MONDAY)
    last = await plan(client, MONDAY + dt.timedelta(days=6))

    payload = await week(client, MONDAY)

    assert [card["id"] for card in cards(payload)] == [first["id"], last["id"]]


async def test_the_days_either_side_of_the_window_are_excluded(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    await plan(client, MONDAY - dt.timedelta(days=1))
    await plan(client, MONDAY + dt.timedelta(days=7))
    inside = await plan(client, MONDAY + dt.timedelta(days=3))

    payload = await week(client, MONDAY)

    assert [card["id"] for card in cards(payload)] == [inside["id"]]


async def test_the_start_is_taken_literally_not_snapped_to_a_monday(
    client: AsyncClient,
) -> None:
    # Paging the calendar by a day is a client's business; the endpoint that
    # silently snapped would make "the week from Wednesday" unaskable.
    wednesday = MONDAY + dt.timedelta(days=2)

    payload = await week(client, wednesday)

    assert payload["start"] == wednesday.isoformat()
    assert payload["end"] == (wednesday + dt.timedelta(days=6)).isoformat()


async def test_the_default_window_is_the_monday_of_the_current_week(
    client: AsyncClient,
) -> None:
    today = dt.datetime.now(dt.UTC).date()
    monday = today - dt.timedelta(days=today.weekday())

    payload = await week(client)

    assert payload["start"] == monday.isoformat()
    assert payload["end"] == (monday + dt.timedelta(days=6)).isoformat()


async def test_a_session_lands_on_its_own_day(client: AsyncClient) -> None:
    await append_ftp(client)
    thursday = MONDAY + dt.timedelta(days=3)
    session = await plan(client, thursday)

    payload = await week(client, MONDAY)

    days = {day["date"]: day["sessions"] for day in payload["days"]}
    assert [card["id"] for card in days[thursday.isoformat()]] == [session["id"]]


async def test_two_sessions_on_one_day_both_appear(client: AsyncClient) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)
    await plan(client, MONDAY, purpose="technique", structure=DISTANCE_RIDE)

    payload = await week(client, MONDAY)

    assert len(payload["days"][0]["sessions"]) == 2
    assert payload["session_count"] == 2


# --- the card -----------------------------------------------------------------


async def test_a_card_carries_what_a_calendar_renders(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(
        client, MONDAY, intent_text="Steady sweet spot, hold the last rep."
    )

    (card,) = cards(await week(client, MONDAY))

    assert card == {
        "id": session["id"],
        "date": "2026-08-10",
        "discipline": "cycling",
        "purpose": "sweet_spot",
        "status": "planned",
        "title": None,
        "workout_id": None,
        "planned_duration_s": RIDE_DURATION_S,
        "total_sets": None,
        "step_count": 7,
        "intent_text": "Steady sweet spot, hold the last rep.",
        "intent_version": 1,
        "predicted_load": pytest.approx(44.5, abs=0.5),
        "predicted_intensity_factor": pytest.approx(0.762, abs=0.005),
        "predicted_volume_load_kg": None,
    }


async def test_the_duration_is_derived_from_the_frozen_step_tree(
    client: AsyncClient,
) -> None:
    # Not stored anywhere: the intent version is the source, so the number
    # follows an edit without anything having to be invalidated.
    await append_ftp(client)
    session = await plan(client, MONDAY)
    await client.patch(
        f"{SESSIONS}/{session['id']}",
        json={
            "structure": {
                "discipline": "cycling",
                "steps": [{"kind": "steady", "duration_s": 1_800}],
            },
            "purpose": "technique",
        },
    )

    (card,) = cards(await week(client, MONDAY))

    assert card["planned_duration_s"] == 1_800
    assert card["intent_version"] == 2


async def test_a_distance_step_has_no_duration_to_show(client: AsyncClient) -> None:
    session = await plan(client, MONDAY, purpose="technique", structure=DISTANCE_RIDE)

    (card,) = cards(await week(client, MONDAY))

    assert card["id"] == session["id"]
    assert card["planned_duration_s"] is None


async def test_a_strength_card_counts_sets_instead_of_seconds(
    client: AsyncClient,
) -> None:
    await plan(client, MONDAY, purpose="max_strength", structure=LIFT)

    (card,) = cards(await week(client, MONDAY))

    assert card["discipline"] == "strength"
    assert card["planned_duration_s"] is None
    assert card["total_sets"] == 5


async def test_a_session_planned_from_the_library_is_titled_by_it(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    created = await client.post(
        WORKOUTS, json={"name": "3 × 8 sweet spot", "structure": RIDE}
    )
    assert created.status_code == 201, created.text
    workout_id = created.json()["id"]
    await plan(client, MONDAY, structure=None, workout_id=workout_id)

    (card,) = cards(await week(client, MONDAY))

    assert card["title"] == "3 × 8 sweet spot"
    assert card["workout_id"] == workout_id


async def test_deleting_the_library_workout_leaves_the_card_untitled(
    client: AsyncClient,
) -> None:
    # The provenance link is nulled, the frozen prescription is not: the card
    # loses its borrowed title and keeps its duration.
    await append_ftp(client)
    created = await client.post(
        WORKOUTS, json={"name": "3 × 8 sweet spot", "structure": RIDE}
    )
    workout_id = created.json()["id"]
    await plan(client, MONDAY, structure=None, workout_id=workout_id)
    assert (await client.delete(f"{WORKOUTS}/{workout_id}")).status_code == 204

    (card,) = cards(await week(client, MONDAY))

    assert card["title"] is None
    assert card["workout_id"] is None
    assert card["planned_duration_s"] == RIDE_DURATION_S


async def test_the_week_totals_the_durations_it_has(client: AsyncClient) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)
    await plan(client, MONDAY + dt.timedelta(days=2))
    # No duration to add: a distance ride and a strength session.
    await plan(
        client,
        MONDAY + dt.timedelta(days=4),
        purpose="technique",
        structure=DISTANCE_RIDE,
    )
    await plan(
        client, MONDAY + dt.timedelta(days=5), purpose="max_strength", structure=LIFT
    )

    payload = await week(client, MONDAY)

    assert payload["session_count"] == 4
    assert payload["planned_duration_s"] == 2 * RIDE_DURATION_S
    # Two of the four had a duration to add. The total is only honest beside
    # the count it came from.
    assert payload["duration_sessions_counted"] == 2
    assert payload["duration_sessions_uncounted"] == 2


async def test_a_week_of_sessions_with_no_duration_has_none_rather_than_zero(
    client: AsyncClient,
) -> None:
    # A strength session and a distance ride: four hours of work between them
    # and not one prescribed second. `sum(... or 0)` called that a rest week.
    await plan(client, MONDAY, purpose="max_strength", structure=KG_LIFT)
    await plan(
        client,
        MONDAY + dt.timedelta(days=1),
        purpose="technique",
        structure=DISTANCE_RIDE,
    )

    payload = await week(client, MONDAY)

    assert payload["session_count"] == 2
    assert payload["planned_duration_s"] is None
    assert payload["duration_sessions_counted"] == 0
    assert payload["duration_sessions_uncounted"] == 2
    rows = {row["discipline"]: row for row in payload["by_discipline"]}
    assert rows["strength"]["planned_duration_s"] is None
    assert rows["strength"]["duration_sessions_uncounted"] == 1
    assert rows["cycling"]["planned_duration_s"] is None
    assert rows["cycling"]["duration_sessions_uncounted"] == 1


async def test_a_cards_status_follows_the_session(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client, MONDAY)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"status": "completed"})

    (card,) = cards(await week(client, MONDAY))

    assert card["status"] == "completed"


# --- the week's aggregates ----------------------------------------------------

#: A ride with a cadence target and no power target: there is a duration to
#: add up and nothing to integrate a load over.
CADENCE_RIDE: dict[str, Any] = {
    "discipline": "cycling",
    "steps": [
        {
            "kind": "steady",
            "duration_s": 1_800,
            "role": "work",
            "targets": {
                "cadence": {"kind": "absolute", "low": 95, "high": 105, "unit": "rpm"}
            },
        }
    ],
}

#: A strength session prescribed in kilograms, so it has a volume load.
KG_LIFT: dict[str, Any] = {
    "discipline": "strength",
    "groups": [
        {
            "items": [
                {
                    "exercise_id": "back_squat",
                    "sets": 5,
                    "reps": 3,
                    "load": {"kind": "kg", "value": 100},
                }
            ]
        }
    ],
}


async def test_a_week_reports_its_load_with_the_count_it_came_from(
    client: AsyncClient,
) -> None:
    # Three sessions, one predictable: the total is honest only next to the
    # coverage, so both travel together.
    await append_ftp(client)
    await plan(client, MONDAY)
    await plan(
        client, MONDAY + dt.timedelta(days=1), purpose="max_strength", structure=LIFT
    )
    await plan(
        client,
        MONDAY + dt.timedelta(days=2),
        purpose="technique",
        structure=CADENCE_RIDE,
    )

    payload = await week(client, MONDAY)

    assert payload["session_count"] == 3
    assert payload["planned_load"] == pytest.approx(44.5, abs=0.5)
    assert payload["load_sessions_counted"] == 1
    assert payload["load_sessions_uncounted"] == 2


async def test_a_week_with_nothing_predictable_has_no_load_rather_than_zero(
    client: AsyncClient,
) -> None:
    # Zero would read as a rest week. Missing data means "not assessed".
    await plan(client, MONDAY, purpose="max_strength", structure=LIFT)
    await plan(
        client,
        MONDAY + dt.timedelta(days=1),
        purpose="technique",
        structure=DISTANCE_RIDE,
    )

    payload = await week(client, MONDAY)

    assert payload["planned_load"] is None
    assert payload["load_sessions_counted"] == 0
    assert payload["load_sessions_uncounted"] == 2


async def test_a_strength_card_carries_kilograms_and_no_load(
    client: AsyncClient,
) -> None:
    # Volume load and TSS are different axes: neither card field holds both,
    # and no total adds them.
    await plan(client, MONDAY, purpose="max_strength", structure=KG_LIFT)

    (card,) = cards(await week(client, MONDAY))

    assert card["predicted_load"] is None
    assert card["predicted_intensity_factor"] is None
    assert card["predicted_volume_load_kg"] == 5 * 3 * 100


async def test_the_per_discipline_rows_reconcile_with_the_flat_totals(
    client: AsyncClient,
) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)
    await plan(client, MONDAY + dt.timedelta(days=2))
    await plan(
        client, MONDAY + dt.timedelta(days=4), purpose="max_strength", structure=KG_LIFT
    )

    payload = await week(client, MONDAY)
    rows = {row["discipline"]: row for row in payload["by_discipline"]}

    assert list(rows) == ["cycling", "strength"]
    assert (
        sum(row["session_count"] for row in rows.values()) == (payload["session_count"])
    )
    # The rows reconcile over the *counted* sessions, which is the only sum
    # that means anything: a row with no duration contributes no duration, and
    # `or 0` would have made it contribute a zero instead.
    assert (
        sum(
            row["planned_duration_s"]
            for row in rows.values()
            if row["planned_duration_s"] is not None
        )
        == payload["planned_duration_s"]
    )
    assert (
        sum(row["duration_sessions_counted"] for row in rows.values())
        == payload["duration_sessions_counted"]
    )
    assert (
        sum(row["duration_sessions_uncounted"] for row in rows.values())
        == payload["duration_sessions_uncounted"]
    )
    assert (
        sum(row["load_sessions_counted"] for row in rows.values())
        == payload["load_sessions_counted"]
    )
    assert (
        sum(row["load_sessions_uncounted"] for row in rows.values())
        == payload["load_sessions_uncounted"]
    )
    assert rows["cycling"]["planned_load"] == pytest.approx(
        payload["planned_load"], abs=1e-9
    )
    assert rows["cycling"]["total_sets"] is None
    # Strength contributes sets and no load, and is not in the load total.
    # The row says so itself rather than leaving a client to guess why.
    assert rows["strength"]["planned_load"] is None
    assert rows["strength"]["load_sessions_counted"] == 0
    assert rows["strength"]["load_sessions_uncounted"] == 1
    assert rows["strength"]["planned_duration_s"] is None
    assert rows["strength"]["duration_sessions_uncounted"] == 1
    assert rows["strength"]["total_sets"] == 5


async def test_a_discipline_row_explains_its_own_missing_load(
    client: AsyncClient,
) -> None:
    # The reason a cycling row has no load is not "it is a strength session".
    # Two cycling sessions, neither predictable, and the row has to carry the
    # coverage that says so — otherwise the only honest thing a client can
    # render is a hardcoded guess.
    await plan(client, MONDAY, purpose="technique", structure=CADENCE_RIDE)
    await plan(
        client,
        MONDAY + dt.timedelta(days=1),
        purpose="technique",
        structure=DISTANCE_RIDE,
    )

    (row,) = (await week(client, MONDAY))["by_discipline"]

    assert row["discipline"] == "cycling"
    assert row["session_count"] == 2
    assert row["planned_load"] is None
    assert row["load_sessions_counted"] == 0
    assert row["load_sessions_uncounted"] == 2
    # One has a prescribed duration (the cadence ride), one does not.
    assert row["planned_duration_s"] == 1_800
    assert row["duration_sessions_counted"] == 1
    assert row["duration_sessions_uncounted"] == 1


async def test_a_discipline_with_no_session_gets_no_row(client: AsyncClient) -> None:
    await append_ftp(client)
    await plan(client, MONDAY)

    payload = await week(client, MONDAY)

    assert [row["discipline"] for row in payload["by_discipline"]] == ["cycling"]


async def test_an_empty_week_has_no_load_and_no_discipline_rows(
    client: AsyncClient,
) -> None:
    payload = await week(client, MONDAY)

    assert payload["planned_load"] is None
    assert payload["load_sessions_counted"] == 0
    assert payload["load_sessions_uncounted"] == 0
    assert payload["by_discipline"] == []


async def test_a_truncated_week_says_its_totals_are_partial(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The render cap bounds what a corrupted date column can load; it must not
    # also make the week claim its totals cover everything. The sessions the
    # cap left behind are counted, and counted as *uncounted* on both axes.
    monkeypatch.setattr("app.services.plan.MAX_WEEK_SESSIONS", 2)
    await append_ftp(client)
    for day in range(3):
        await plan(client, MONDAY + dt.timedelta(days=day))

    payload = await week(client, MONDAY)

    assert len(cards(payload)) == 2
    assert payload["session_count"] == 3
    assert payload["duration_sessions_counted"] == 2
    assert payload["duration_sessions_uncounted"] == 1
    assert payload["load_sessions_counted"] == 2
    assert payload["load_sessions_uncounted"] == 1
    # The rendered rows are still whole in themselves: an unread session has
    # no discipline to be attributed to.
    (row,) = payload["by_discipline"]
    assert row["session_count"] == 2
    assert row["duration_sessions_uncounted"] == 0


async def test_the_load_follows_the_pinned_version_not_the_current_anchor(
    client: AsyncClient,
) -> None:
    # Predicted load is computed on read, but from the *frozen* pins: a new
    # FTP moves nothing that was already planned.
    await append_ftp(client, 250)
    await plan(client, MONDAY)
    before = (await week(client, MONDAY))["planned_load"]

    await append_ftp(client, 300)

    assert (await week(client, MONDAY))["planned_load"] == pytest.approx(before)


# --- the guard ----------------------------------------------------------------


async def test_the_week_needs_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(WEEK)).status_code == 401


async def test_a_malformed_start_is_refused(client: AsyncClient) -> None:
    response = await client.get(WEEK, params={"start": "last monday"})

    assert response.status_code == 422
