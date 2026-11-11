"""Swaps and the athlete's preferences per exercise, over HTTP (AC-75, AC-77).

The ranking itself is pinned in `test_domain_swaps.py`; these tests check that
what the athlete states — avoid, less often, a remembered pick, *Ease back
in* — reaches every surface that offers a movement: the substitutes endpoint
and the session packet the phone trains from offline.
"""

import datetime as dt
from typing import Any

from httpx import AsyncClient

from app.core.clock import athlete_today

PACKET = "/api/v1/strength/packet"
PLANNED = "/api/v1/planned-sessions"
MANUAL = "/api/v1/manual-sessions"
EXERCISES = "/api/v1/exercises"
PREFERENCES = "/api/v1/exercise-preferences"

#: The back squat's three swaps in the default gym (rack free), as the domain
#: ranks them: two squats that load both its main groups, then the best lunge.
SQUAT_SWAPS = ["box_squat", "safety_bar_squat", "bulgarian_split_squat"]


def prefs(slug: str) -> str:
    return f"{EXERCISES}/{slug}/preferences"


async def plan_squat(client: AsyncClient) -> str:
    """Today's session: three sets of back squat at 60 kg."""
    response = await client.post(
        PLANNED,
        json={
            "date": athlete_today().isoformat(),
            "purpose": "max_strength",
            "structure": {
                "discipline": "strength",
                "groups": [
                    {
                        "items": [
                            {
                                "exercise_id": "back_squat",
                                "sets": 3,
                                "reps": 5,
                                "load": {"kind": "kg", "value": 60},
                            }
                        ]
                    }
                ],
            },
            "success_criteria": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def squat_line(client: AsyncClient, session_id: str) -> dict[str, Any]:
    response = await client.get(PACKET)
    assert response.status_code == 200, response.text
    body = response.json()
    (session,) = [s for s in body["sessions"] if s["planned_session_id"] == session_id]
    (line,) = session["lines"]
    return line | {"_library": {e["id"] for e in body["library"]}}


async def swaps_of(client: AsyncClient, slug: str) -> list[str]:
    response = await client.get(f"{EXERCISES}/{slug}/substitutes")
    assert response.status_code == 200, response.text
    return [s["exercise_id"] for s in response.json()["substitutes"]]


async def patch(client: AsyncClient, slug: str, **body: Any) -> dict[str, Any]:
    response = await client.patch(prefs(slug), json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- AC-77: an avoided exercise is never offered ------------------------------------


async def test_an_avoided_exercise_never_appears_in_swaps_or_the_packet(
    client: AsyncClient,
) -> None:
    session_id = await plan_squat(client)
    assert await swaps_of(client, "back_squat") == SQUAT_SWAPS
    line = await squat_line(client, session_id)
    assert [s["exercise_id"] for s in line["swaps"]] == SQUAT_SWAPS

    await patch(client, "bulgarian_split_squat", avoid=True)

    offered = await swaps_of(client, "back_squat")
    assert "bulgarian_split_squat" not in offered
    assert len(offered) == 3
    line = await squat_line(client, session_id)
    packet_swaps = [s["exercise_id"] for s in line["swaps"]]
    assert "bulgarian_split_squat" not in packet_swaps
    assert packet_swaps == offered
    # Every swap's library entry rides along, so the phone can name it offline.
    assert set(packet_swaps) <= line["_library"]
    # Nor is it ever an Ease back in step.
    started = await patch(client, "back_squat", ease_back=True)
    steps = [s["exercise_id"] for s in started["ease_back"]["steps"]]
    assert steps
    assert "bulgarian_split_squat" not in steps


async def test_avoiding_the_planned_exercise_itself_prompts_a_swap_at_session_start(
    client: AsyncClient,
) -> None:
    session_id = await plan_squat(client)
    assert (await squat_line(client, session_id))["avoided"] is False

    await patch(client, "back_squat", avoid=True)

    line = await squat_line(client, session_id)
    # The line stays what the plan says; the phone opens on the swap prompt.
    assert line["exercise_id"] == "back_squat"
    assert line["avoided"] is True
    assert [s["exercise_id"] for s in line["swaps"]] == SQUAT_SWAPS


async def test_less_often_moves_a_swap_behind_the_others(client: AsyncClient) -> None:
    await patch(client, "box_squat", less_often=True)

    assert await swaps_of(client, "back_squat") == [
        "safety_bar_squat",
        "bulgarian_split_squat",
        "forward_lunge",
    ]


# --- AC-75 over HTTP: the remembered pick -----------------------------------------


async def test_a_remembered_pick_ranks_first_and_clearing_it_restores_the_order(
    client: AsyncClient,
) -> None:
    stored = await patch(client, "back_squat", remembered_swap_id="leg_press")
    assert stored["remembered_swap_id"] == "leg_press"
    assert (await swaps_of(client, "back_squat"))[0] == "leg_press"

    cleared = await patch(client, "back_squat", remembered_swap_id=None)

    assert cleared["remembered_swap_id"] is None
    assert await swaps_of(client, "back_squat") == SQUAT_SWAPS


async def test_an_exercise_is_never_remembered_as_its_own_swap(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        prefs("back_squat"), json={"remembered_swap_id": "back_squat"}
    )

    assert response.status_code == 422
    assert "own swap" in response.json()["detail"]


async def test_an_unknown_exercise_is_404_on_either_side(client: AsyncClient) -> None:
    assert (
        await client.get(f"{EXERCISES}/no_such_lift/substitutes")
    ).status_code == 404
    assert (await client.get(prefs("no_such_lift"))).status_code == 404
    response = await client.patch(
        prefs("back_squat"), json={"remembered_swap_id": "no_such_lift"}
    )
    assert response.status_code == 404


# --- AC-77: Ease back in advances only on the athlete's tap -------------------------


async def test_ease_back_in_advances_only_on_the_athletes_tap(
    client: AsyncClient,
) -> None:
    session_id = await plan_squat(client)
    started = await patch(client, "back_squat", ease_back=True)
    ladder = [s["exercise_id"] for s in started["ease_back"]["steps"]]
    # Easiest first; `frontend/tests/mocks/handlers-preferences.ts` answers
    # with this ladder, so it is pinned here where it comes from.
    assert ladder == ["forward_lunge", "box_squat", "bulgarian_split_squat"]
    assert started["ease_back"]["step"] == 0
    assert started["ease_back"]["current"]["exercise_id"] == ladder[0]

    # Reading it, and training the current step well, move nothing.
    assert (await client.get(prefs("back_squat"))).json()["ease_back"]["step"] == 0
    start = dt.datetime.combine(
        athlete_today() - dt.timedelta(days=3), dt.time(17, 0), tzinfo=dt.UTC
    )
    logged = await client.post(
        MANUAL,
        json={
            "start_time": start.isoformat(),
            "timezone": "UTC",
            "duration_s": 3_600,
            "sets": [
                {"exercise_id": ladder[0], "reps": 10, "load_kg": 20.0, "rir": 4}
                for _ in range(3)
            ],
        },
    )
    assert logged.status_code == 201, logged.text
    assert (await client.get(prefs("back_squat"))).json()["ease_back"]["step"] == 0
    line = await squat_line(client, session_id)
    assert line["ease_back"]["step"] == 0
    assert line["ease_back"]["current"]["exercise_id"] == ladder[0]

    # The tap.
    advanced = await client.post(f"{prefs('back_squat')}/ease-back/advance")
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["ease_back"]["step"] == 1
    assert advanced.json()["ease_back"]["current"]["exercise_id"] == ladder[1]

    # Past the last step is the exercise itself: the ladder ends.
    last = advanced
    for _ in range(len(ladder) - 1):
        last = await client.post(f"{prefs('back_squat')}/ease-back/advance")
        assert last.status_code == 200, last.text
    assert last.json()["ease_back"] is None
    assert (await squat_line(client, session_id))["ease_back"] is None


async def test_advancing_without_easing_back_is_a_conflict(client: AsyncClient) -> None:
    response = await client.post(f"{prefs('back_squat')}/ease-back/advance")

    assert response.status_code == 409
    assert "not easing back" in response.json()["detail"]


async def test_nothing_easier_means_no_ladder_to_start(client: AsyncClient) -> None:
    response = await client.patch(prefs("air_squat"), json={"ease_back": True})

    assert response.status_code == 409
    assert "easier" in response.json()["detail"]


async def test_stopping_ease_back_in_clears_it(client: AsyncClient) -> None:
    await patch(client, "back_squat", ease_back=True)

    stopped = await patch(client, "back_squat", ease_back=False)

    assert stopped["ease_back"] is None


# --- the list, defaults and the guard -------------------------------------------------


async def test_an_exercise_nobody_said_anything_about_reads_as_defaults(
    client: AsyncClient,
) -> None:
    response = await client.get(prefs("back_squat"))

    assert response.status_code == 200
    assert response.json() == {
        "exercise_id": "back_squat",
        "avoid": False,
        "less_often": False,
        "remembered_swap_id": None,
        "ease_back": None,
    }


async def test_the_list_holds_only_exercises_with_something_stated(
    client: AsyncClient,
) -> None:
    await patch(client, "leg_press", avoid=True)
    await patch(client, "goblet_squat", avoid=True)
    await patch(client, "goblet_squat", avoid=False)

    response = await client.get(PREFERENCES)

    assert response.status_code == 200
    assert [p["exercise_id"] for p in response.json()] == ["leg_press"]


async def test_preferences_need_a_login(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(PREFERENCES)).status_code == 401
    assert (await anon_client.get(prefs("back_squat"))).status_code == 401
    assert (
        await anon_client.patch(prefs("back_squat"), json={"avoid": True})
    ).status_code == 401
    assert (
        await anon_client.get(f"{EXERCISES}/back_squat/substitutes")
    ).status_code == 401


# --- the gym's swap, through the outbox (AC-76) ------------------------------------

SYNC = "/api/v1/sync"
CARDS = "/api/v1/cards"
SESSION_CLIENT_ID = "0199a1b2-0000-7000-8000-00000000c001"
SET_CLIENT_ID = "0199a1b2-0000-7000-8000-00000000c101"
GYM_START = dt.datetime(2026, 11, 9, 17, 30, tzinfo=dt.UTC)


def mutation_id(n: int) -> str:
    return f"0199a1b2-0000-7000-8000-{n:012d}"


def at(minutes: int) -> str:
    return (GYM_START + dt.timedelta(minutes=minutes)).isoformat()


def gym_start() -> dict[str, Any]:
    return {
        "kind": "session_start",
        "mutation_id": mutation_id(1),
        "client_edit_time": at(0),
        "session_client_id": SESSION_CLIENT_ID,
        "start_time": GYM_START.isoformat(),
    }


def swap(n: int, scope: str, pick: str = "box_squat") -> dict[str, Any]:
    return {
        "kind": "swap",
        "mutation_id": mutation_id(n),
        "client_edit_time": at(n),
        "session_client_id": SESSION_CLIENT_ID,
        "exercise_id": "back_squat",
        "swap_exercise_id": pick,
        "scope": scope,
    }


async def upload(client: AsyncClient, *mutations: dict[str, Any]) -> Any:
    return await client.post(
        SYNC, json={"schema_version": 1, "mutations": list(mutations)}
    )


async def test_a_swap_in_the_gym_is_remembered_and_logs_under_the_substitute(
    client: AsyncClient,
) -> None:
    response = await upload(
        client,
        gym_start(),
        swap(2, "session", pick="leg_press"),
        {
            "kind": "set_upsert",
            "mutation_id": mutation_id(3),
            "client_edit_time": at(3),
            "session_client_id": SESSION_CLIENT_ID,
            "set_client_id": SET_CLIENT_ID,
            "set": {
                "exercise_id": "leg_press",
                "replaced_exercise_id": "back_squat",
                "planned_line_ref": "0.0",
                "reps": 5,
                "load_kg": 140.0,
            },
        },
    )
    assert response.status_code == 200, response.text

    stored = (await client.get(prefs("back_squat"))).json()
    assert stored["remembered_swap_id"] == "leg_press"
    assert (await swaps_of(client, "back_squat"))[0] == "leg_press"
    # "This session" is the default and asks for nothing more: no card.
    assert (await client.get(CARDS)).json() == []


async def test_rest_of_block_is_stored_as_a_pending_card(client: AsyncClient) -> None:
    response = await upload(
        client,
        gym_start(),
        swap(2, "rest_of_block"),
        # Cards never show between Start and Finish.
        {
            "kind": "session_finish",
            "mutation_id": mutation_id(3),
            "client_edit_time": at(30),
            "session_client_id": SESSION_CLIENT_ID,
            "end_time": at(30),
        },
    )
    assert response.status_code == 200, response.text

    (card,) = (await client.get(CARDS)).json()
    assert card["kind"] == "swap_rest_of_block"
    assert card["state"] == "shown"
    assert card["payload"]["exercise_id"] == "back_squat"
    assert card["payload"]["swap_exercise_id"] == "box_squat"
    assert "box squats" in card["headline"].lower()
    # The pick is remembered at once, like any swap; the plan waits for the card.
    assert (await client.get(prefs("back_squat"))).json()[
        "remembered_swap_id"
    ] == "box_squat"


async def test_a_swap_naming_an_exercise_outside_the_catalogue_is_rejected(
    client: AsyncClient,
) -> None:
    response = await upload(client, gym_start(), swap(2, "session", pick="no_such"))

    assert response.status_code == 422
    assert response.json()["mutation_id"] == mutation_id(2)


async def test_accepting_the_pain_cards_swap_remembers_it(client: AsyncClient) -> None:
    # Sharp pain on a forward lunge queues the swap offer (`test_safety.py`).
    lunge_set = {
        "exercise_id": "forward_lunge",
        "reps": 8,
        "load_kg": 12.0,
        "per_side": True,
        "planned_line_ref": "0.0",
        "pain_kind": "sharp",
        "pain_band": "4-5",
    }
    response = await upload(
        client,
        gym_start(),
        {
            "kind": "set_upsert",
            "mutation_id": mutation_id(2),
            "client_edit_time": at(2),
            "session_client_id": SESSION_CLIENT_ID,
            "set_client_id": SET_CLIENT_ID,
            "set": lunge_set,
        },
        {
            "kind": "session_finish",
            "mutation_id": mutation_id(3),
            "client_edit_time": at(30),
            "session_client_id": SESSION_CLIENT_ID,
            "end_time": at(30),
        },
    )
    assert response.status_code == 200, response.text
    (card,) = (await client.get(CARDS)).json()
    assert card["payload"]["swap_exercise_id"] == "step_up"

    answered = await client.post(f"{CARDS}/{card['id']}/act", json={"action": "accept"})
    assert answered.status_code == 200, answered.text

    stored = (await client.get(prefs("forward_lunge"))).json()
    assert stored["remembered_swap_id"] == "step_up"


async def test_the_frontend_packet_fixtures_swaps_are_the_servers(
    client: AsyncClient,
) -> None:
    """`frontend/tests/mocks/handlers-packet.ts` types these swaps in; this is
    where they come from — the captured plan (a one-arm row at 20 kg, then a
    45 s plank) planned here and read back from the packet. A ranking change
    that moves them fails here first, and the fixture is updated to match.
    """
    response = await client.post(
        PLANNED,
        json={
            "date": athlete_today().isoformat(),
            "purpose": "max_strength",
            "structure": {
                "discipline": "strength",
                "groups": [
                    {
                        "items": [
                            {
                                "exercise_id": "single_arm_dumbbell_row",
                                "sets": 3,
                                "reps": 10,
                                "per_side": True,
                                "load": {"kind": "kg", "value": 20},
                                "warmup": "none",
                            }
                        ]
                    },
                    {
                        "items": [
                            {
                                "exercise_id": "front_plank",
                                "sets": 3,
                                "duration_s": 45,
                                "load": {"kind": "bodyweight"},
                            }
                        ]
                    },
                ],
            },
            "success_criteria": [],
        },
    )
    assert response.status_code == 201, response.text
    body = (await client.get(PACKET)).json()
    (session,) = body["sessions"]
    row, plank = session["lines"]

    assert row["swaps"] == [
        {"exercise_id": "barbell_row", "exercise_name": "Barbell Row"},
        {"exercise_id": "pendlay_row", "exercise_name": "Pendlay Row"},
        {"exercise_id": "inverted_row", "exercise_name": "Inverted Row"},
    ]
    assert plank["swaps"] == [
        {"exercise_id": "dead_bug", "exercise_name": "Dead Bug"},
        {"exercise_id": "hollow_body_hold", "exercise_name": "Hollow Body Hold"},
        {"exercise_id": "ab_wheel_rollout", "exercise_name": "Ab Wheel Rollout"},
    ]
    assert row["avoided"] is plank["avoided"] is False
    assert row["ease_back"] is None
    assert [e["id"] for e in body["library"]] == [
        "ab_wheel_rollout",
        "barbell_row",
        "dead_bug",
        "front_plank",
        "hollow_body_hold",
        "inverted_row",
        "pendlay_row",
        "single_arm_dumbbell_row",
    ]
