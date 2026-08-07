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
    assert payload["planned_duration_s"] == 0


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


async def test_a_cards_status_follows_the_session(client: AsyncClient) -> None:
    await append_ftp(client)
    session = await plan(client, MONDAY)
    await client.patch(f"{SESSIONS}/{session['id']}", json={"status": "completed"})

    (card,) = cards(await week(client, MONDAY))

    assert card["status"] == "completed"


# --- the guard ----------------------------------------------------------------


async def test_the_week_needs_a_session(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(WEEK)).status_code == 401


async def test_a_malformed_start_is_refused(client: AsyncClient) -> None:
    response = await client.get(WEEK, params={"start": "last monday"})

    assert response.status_code == 422
