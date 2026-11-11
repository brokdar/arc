"""`GET /strength/packet`: everything the phone needs to train offline.

The phone renders, it does not decide. These tests read the packet the way the
phone does — over HTTP — and assert the numbers it would put on the gym
screen: each line's pre-fill (AC-46), its warm-ups and plates (AC-47), which
sessions are in it at all, and the refusal without a login.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.clock import athlete_today
from app.domain.loading import Equipment, plates_per_side
from app.domain.warmups import warmup_sets
from tests.unit.prescriptions import WATT_HOUR

PACKET = "/api/v1/strength/packet"
PLANNED = "/api/v1/planned-sessions"
MANUAL = "/api/v1/manual-sessions"
PLANS = "/api/v1/plans"
SETTINGS = "/api/v1/settings/training"

VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "loading.json"

#: An hour in watts, so planning it needs no FTP anchor.
RIDE = WATT_HOUR


def item(exercise_id: str, **overrides: Any) -> dict[str, Any]:
    """One prescription line: 3 × 5 at 60 kg unless told otherwise."""
    line: dict[str, Any] = {
        "exercise_id": exercise_id,
        "sets": 3,
        "reps": 5,
        "load": {"kind": "kg", "value": 60},
    }
    line.update(overrides)
    return line


def lift(*items: dict[str, Any]) -> dict[str, Any]:
    """A strength structure with one group per line."""
    return {"discipline": "strength", "groups": [{"items": [i]} for i in items]}


def day(offset: int) -> str:
    return (athlete_today() + dt.timedelta(days=offset)).isoformat()


async def plan(
    client: AsyncClient,
    structure: dict[str, Any],
    *,
    offset: int = 0,
    purpose: str = "max_strength",
) -> str:
    response = await client.post(
        PLANNED,
        json={
            "date": day(offset),
            "purpose": purpose,
            "structure": structure,
            # No template criteria: a ride's would need an FTP anchor.
            "success_criteria": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def log_history(client: AsyncClient, *sets: dict[str, Any]) -> None:
    """A gym session three days ago holding ``sets``."""
    start = dt.datetime.combine(
        athlete_today() - dt.timedelta(days=3), dt.time(17, 30), tzinfo=dt.UTC
    )
    response = await client.post(
        MANUAL,
        json={
            "start_time": start.isoformat(),
            "timezone": "UTC",
            "duration_s": 3_600,
            "sets": list(sets),
        },
    )
    assert response.status_code == 201, response.text


async def packet(client: AsyncClient) -> dict[str, Any]:
    response = await client.get(PACKET)
    assert response.status_code == 200, response.text
    return response.json()


def lines_of(body: dict[str, Any], session_id: str) -> list[dict[str, Any]]:
    (session,) = [s for s in body["sessions"] if s["planned_session_id"] == session_id]
    return session["lines"]


# --- AC-46: pre-fill precedence ---------------------------------------------------


async def test_plain_logbook_prefills_prescription_then_last_logged_then_blank(
    client: AsyncClient,
) -> None:
    response = await client.patch(SETTINGS, json={"plain_logbook": True})
    assert response.status_code == 200, response.text
    # The row is logged as 50 then 55 kg: the last set is what "last" means.
    await log_history(
        client,
        {"exercise_id": "barbell_row", "reps": 10, "load_kg": 50.0},
        {"exercise_id": "barbell_row", "reps": 8, "load_kg": 55.0},
    )
    session_id = await plan(
        client,
        lift(
            item("back_squat", load={"kind": "kg", "value": 60}),
            item("barbell_row", reps=8, load={"kind": "rpe", "value": 8}),
            item("overhead_press", load={"kind": "rpe", "value": 8}),
        ),
    )

    body = await packet(client)

    assert body["settings"]["plain_logbook"] is True
    squat, row, press = lines_of(body, session_id)
    assert squat["prefill"] == {
        "source": "prescription",
        "load_kg": 60.0,
        "reps": 5,
        "duration_s": None,
        "starting_flow": False,
    }
    assert row["prefill"] == {
        "source": "last_logged",
        "load_kg": 55.0,
        "reps": 8,
        "duration_s": None,
        "starting_flow": False,
    }
    assert press["prefill"] == {
        "source": "blank",
        "load_kg": None,
        "reps": None,
        "duration_s": None,
        "starting_flow": True,
    }


async def test_the_last_numbers_table_holds_every_logged_exercise(
    client: AsyncClient,
) -> None:
    await log_history(
        client,
        {"exercise_id": "barbell_row", "reps": 10, "load_kg": 50.0},
        {"exercise_id": "barbell_row", "reps": 8, "load_kg": 55.0},
        {"exercise_id": "front_plank", "duration_s": 45},
        {
            "exercise_id": "back_squat",
            "set_kind": "warmup",
            "reps": 10,
            "load_kg": 20.0,
        },
    )

    body = await packet(client)

    table = {entry["exercise_id"]: entry for entry in body["last_numbers"]}
    assert set(table) == {"barbell_row", "front_plank"}
    assert (table["barbell_row"]["load_kg"], table["barbell_row"]["reps"]) == (55.0, 8)
    assert table["front_plank"]["duration_s"] == 45
    assert table["barbell_row"]["logged_on"] == day(-3)
    # Every exercise the packet names is in its library subset.
    assert {"barbell_row", "front_plank"} <= {e["id"] for e in body["library"]}


async def test_follow_exactly_prefills_every_line_with_its_prescription(
    client: AsyncClient,
) -> None:
    created = await client.post(
        PLANS,
        json={
            "name": "Physio block",
            "phase": "base",
            "follow_exactly": True,
            "starts_on": day(0),
            "ends_before": day(14),
        },
    )
    assert created.status_code == 201, created.text
    # History that disagrees with the prescription must not leak into it.
    await log_history(
        client,
        {"exercise_id": "back_squat", "reps": 8, "load_kg": 55.0},
        {"exercise_id": "split_squat", "reps": 12, "load_kg": 10.0, "per_side": True},
        {"exercise_id": "barbell_row", "reps": 10, "load_kg": 50.0},
    )
    prescribed = [
        item("back_squat", sets=5, reps=3, load={"kind": "kg", "value": 70}),
        item(
            "split_squat",
            sets=2,
            reps=10,
            per_side=True,
            load={"kind": "kg", "value": 12},
        ),
        item(
            "front_plank", sets=3, reps=None, duration_s=40, load={"kind": "bodyweight"}
        ),
        # An effort load names no weight to follow, so the weight is the last
        # one logged — but the reps stay the prescription's 6, not history's 10.
        item("barbell_row", sets=3, reps=6, load={"kind": "rpe", "value": 8}),
    ]
    session_id = await plan(client, lift(*prescribed))

    body = await packet(client)

    (session,) = body["sessions"]
    assert session["follow_exactly"] is True
    *kg_or_bodyweight, effort = session["lines"]
    for line, written in zip(kg_or_bodyweight, prescribed[:-1], strict=True):
        load = written["load"].get("value")
        assert line["prefill"]["source"] == "prescription"
        assert line["prefill"]["load_kg"] == (None if load is None else float(load))
        assert line["prefill"]["reps"] == written.get("reps")
        assert line["prefill"]["duration_s"] == written.get("duration_s")
        assert line["prefill"]["starting_flow"] is False
        assert line["prescription"]["sets"] == written["sets"]
    assert effort["exercise_id"] == "barbell_row"
    assert effort["prefill"] == {
        "source": "last_logged",
        "load_kg": 50.0,
        "reps": 6,
        "duration_s": None,
        "starting_flow": False,
    }
    assert effort["prescription"]["sets"] == 3
    assert session_id == session["planned_session_id"]


async def test_a_session_outside_the_plan_is_not_follow_exactly(
    client: AsyncClient,
) -> None:
    await plan(client, lift(item("back_squat")))

    (session,) = (await packet(client))["sessions"]

    assert session["follow_exactly"] is False


async def test_only_the_next_seven_days_of_strength_sessions_are_in_the_packet(
    client: AsyncClient,
) -> None:
    today = await plan(client, lift(item("back_squat")), offset=0)
    last_day = await plan(client, lift(item("back_squat")), offset=6)
    await plan(client, lift(item("back_squat")), offset=8)
    await plan(client, lift(item("back_squat")), offset=-1)
    await plan(client, RIDE, offset=1, purpose="endurance")

    body = await packet(client)

    assert [s["planned_session_id"] for s in body["sessions"]] == [today, last_day]
    assert body["starts_on"] == day(0)
    assert body["ends_before"] == day(7)


async def test_an_endurance_session_is_not_in_the_packet(client: AsyncClient) -> None:
    await plan(client, RIDE, purpose="endurance")

    assert (await packet(client))["sessions"] == []


async def test_the_packet_carries_its_version_time_gym_and_effort_mode(
    client: AsyncClient,
) -> None:
    await client.patch(SETTINGS, json={"mode": "standard", "ask_frequency": "last_set"})

    body = await packet(client)

    assert body["schema_version"] == 1
    generated = dt.datetime.fromisoformat(body["generated_at"])
    assert generated.tzinfo is not None
    assert body["effort"] == {"scale": "reps_left", "ask": "last_set"}
    assert body["gym"]["bar_kg"] == 20.0
    assert {"kg": 1.25, "pairs": 2} in body["gym"]["plate_pairs"]


async def test_simple_mode_asks_easy_good_hard(client: AsyncClient) -> None:
    body = await packet(client)

    assert body["effort"] == {"scale": "easy_good_hard", "ask": "always"}


# --- AC-47: warm-ups and plates are the domain's ---------------------------------


def vector_cases(fn: str, equipment: str) -> list[float]:
    document = json.loads(VECTORS.read_text())
    return [
        case["args"][0]
        for case in document["cases"]
        if case["fn"] == fn
        and case["args"][1] == equipment
        and "error" not in case["expect"]
        and 0 < case["args"][0] <= 500
    ]


BARBELL_LOADS = vector_cases("plates_per_side", "barbell")
DUMBBELL_LOADS = vector_cases("nearest_loadable", "dumbbell")


def test_the_vectors_give_cases_to_compare() -> None:
    # An empty list would make the comparisons below pass vacuously.
    assert len(BARBELL_LOADS) >= 5
    assert len(DUMBBELL_LOADS) >= 2


async def test_packet_warm_ups_and_plates_equal_the_domain_for_barbell_vectors(
    client: AsyncClient,
) -> None:
    session_id = await plan(
        client,
        lift(
            *(
                item("back_squat", warmup="auto", load={"kind": "kg", "value": kg})
                for kg in BARBELL_LOADS
            )
        ),
    )

    lines = lines_of(await packet(client), session_id)

    for line, kg in zip(lines, BARBELL_LOADS, strict=True):
        assert line["equipment"] == "barbell"
        assert line["plates_per_side"] == plates_per_side(kg)
        assert line["warmups"] == [
            {
                "kg": step.kg,
                "reps": step.reps,
                "plates_per_side": plates_per_side(step.kg),
            }
            for step in warmup_sets(kg)
        ]


async def test_packet_plates_match_the_vectors_expected_values(
    client: AsyncClient,
) -> None:
    document = json.loads(VECTORS.read_text())
    expected = {
        case["args"][0]: case["expect"]["value"]
        for case in document["cases"]
        if case["fn"] == "plates_per_side" and case["args"][1] == "barbell"
    }
    session_id = await plan(
        client,
        lift(
            *(
                item("back_squat", load={"kind": "kg", "value": kg})
                for kg in BARBELL_LOADS
            )
        ),
    )

    lines = lines_of(await packet(client), session_id)

    assert [line["plates_per_side"] for line in lines] == [
        expected[kg] for kg in BARBELL_LOADS
    ]


async def test_packet_dumbbell_warm_ups_equal_the_domain_and_carry_no_plates(
    client: AsyncClient,
) -> None:
    session_id = await plan(
        client,
        lift(
            *(
                item("goblet_squat", warmup="auto", load={"kind": "kg", "value": kg})
                for kg in DUMBBELL_LOADS
            )
        ),
    )

    lines = lines_of(await packet(client), session_id)

    for line, kg in zip(lines, DUMBBELL_LOADS, strict=True):
        assert line["equipment"] == "dumbbell"
        assert line["plates_per_side"] is None
        assert line["warmups"] == [
            {"kg": step.kg, "reps": step.reps, "plates_per_side": None}
            for step in warmup_sets(kg, Equipment.DUMBBELL)
        ]


async def test_only_the_first_main_lift_of_a_pattern_ramps_by_default(
    client: AsyncClient,
) -> None:
    session_id = await plan(
        client,
        lift(
            item("back_squat", load={"kind": "kg", "value": 100}),
            item("front_squat", load={"kind": "kg", "value": 80}),
            item("leg_extension", load={"kind": "kg", "value": 40}),
        ),
    )

    squat, front, extension = lines_of(await packet(client), session_id)

    assert [(w["kg"], w["reps"]) for w in squat["warmups"]] == [
        (20.0, 10),
        (40.0, 5),
        (60.0, 3),
        (80.0, 2),
    ]
    assert squat["main"] is True
    assert front["warmups"] == []
    assert extension["warmups"] == []
    assert extension["equipment"] == "stack"


async def test_each_line_names_its_place_in_the_prescription(
    client: AsyncClient,
) -> None:
    structure = {
        "discipline": "strength",
        "groups": [
            {"items": [item("back_squat")]},
            {"label": "A", "items": [item("bench_press"), item("barbell_row")]},
        ],
    }
    session_id = await plan(client, structure)

    lines = lines_of(await packet(client), session_id)

    assert [line["line_ref"] for line in lines] == ["0.0", "1.0", "1.1"]
    assert [line["group_label"] for line in lines] == [None, "A", "A"]
    assert [line["exercise_name"] for line in lines] == [
        "Back Squat",
        "Barbell Bench Press",
        "Barbell Row",
    ]


# --- auth -------------------------------------------------------------------------


async def test_the_packet_needs_a_login(anon_client: AsyncClient) -> None:
    response = await anon_client.get(PACKET)

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["post", "put", "delete"])
async def test_the_packet_is_read_only(client: AsyncClient, method: str) -> None:
    response = await getattr(client, method)(PACKET)

    assert response.status_code == 405
