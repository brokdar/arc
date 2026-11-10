"""Manual sessions carry what a set logged in the gym knows about itself.

A phone uploads a set more than once when the signal drops mid-request, so a
set has an identity of its own (`client_id`) and storing it twice is the
server's problem, not the athlete's. Beyond that a set says what kind it was,
which side, how hard it *felt* — never how hard it was planned to be — which
planned line it answered, and whether it replaced another movement; the
session says whether it should teach arc anything and how hard it was overall.

Every assertion reads the stored session back through `GET /sessions/{id}`
rather than trusting the create response, because the claim is about the rows.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

SESSIONS = "/api/v1/sessions"
MANUAL = "/api/v1/manual-sessions"

#: Two phone-minted ids, fixed so a replay can carry the same ones.
SQUAT_ID = "0199a1b2-0000-7000-8000-000000000001"
PLANK_ID = "0199a1b2-0000-7000-8000-000000000002"


def a_set(**overrides: Any) -> dict[str, Any]:
    """One working squat set, as the phone sends it."""
    entry: dict[str, Any] = {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0}
    entry.update(overrides)
    return entry


def a_session(**overrides: Any) -> dict[str, Any]:
    """A gym session whose sets carry their phone ids."""
    payload: dict[str, Any] = {
        "start_time": "2026-05-11T17:30:00+02:00",
        "timezone": "Europe/Zurich",
        "duration_s": 3600,
        "sets": [
            a_set(client_id=SQUAT_ID, rir=2),
            {"exercise_name": "Copenhagen plank", "reps": 8, "client_id": PLANK_ID},
        ],
    }
    payload.update(overrides)
    return payload


async def stored(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """The session as the database holds it now."""
    response = await client.get(f"{SESSIONS}/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def post(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Post a session that must be accepted, and read it back from storage."""
    response = await client.post(MANUAL, json=payload)
    assert response.status_code == 201, response.text
    return await stored(client, response.json()["id"])


def located(response: Any, *loc: Any) -> None:
    """Assert a request-validation 422 that names the field it refused."""
    assert response.status_code == 422, response.text
    locations = [error["loc"] for error in response.json()["detail"]]
    assert list(loc) in locations, locations


# --- AC-22: a set uploaded twice is stored once -------------------------------


async def test_two_sets_with_one_client_id_are_stored_as_one(
    client: AsyncClient,
) -> None:
    """The same set twice in one upload is one set; the later copy is the edit."""
    session = await post(
        client,
        a_session(
            sets=[
                a_set(client_id=SQUAT_ID, reps=5),
                a_set(client_id=SQUAT_ID, reps=6),
            ]
        ),
    )

    assert len(session["logged_sets"]) == 1
    (only,) = session["logged_sets"]
    assert only["client_id"] == SQUAT_ID
    assert only["reps"] == 6
    assert only["set_index"] == 0


async def test_posting_the_same_session_twice_stores_each_set_once(
    client: AsyncClient,
) -> None:
    """A retried upload is the same session, not a second one beside it."""
    first = await client.post(MANUAL, json=a_session())
    second = await client.post(MANUAL, json=a_session())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    listed = (await client.get(SESSIONS)).json()
    assert [row["id"] for row in listed["items"]] == [first.json()["id"]]
    session = await stored(client, first.json()["id"])
    assert sorted(entry["client_id"] for entry in session["logged_sets"]) == sorted(
        [SQUAT_ID, PLANK_ID]
    )


async def test_two_sessions_may_reuse_a_client_id(client: AsyncClient) -> None:
    """Uniqueness is per session: another day's set may carry the same id."""
    monday = await post(client, a_session(sets=[a_set(client_id=SQUAT_ID)]))
    thursday = await post(
        client,
        a_session(
            start_time="2026-05-14T17:30:00+02:00", sets=[a_set(client_id=SQUAT_ID)]
        ),
    )

    assert monday["id"] != thursday["id"]
    assert [entry["client_id"] for entry in monday["logged_sets"]] == [SQUAT_ID]
    assert [entry["client_id"] for entry in thursday["logged_sets"]] == [SQUAT_ID]


async def test_a_missing_client_id_is_minted_by_the_server(
    client: AsyncClient,
) -> None:
    """Coach and legacy callers send none; every stored set still has one."""
    session = await post(
        client, a_session(sets=[a_set(), {"exercise_name": "Plank", "duration_s": 45}])
    )

    ids = [uuid.UUID(entry["client_id"]) for entry in session["logged_sets"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert all(value.version == 7 for value in ids)


# --- AC-24: effort is reported, bounded, and never the target -----------------


@pytest.mark.parametrize("rpe", [6.0, 6.5, 8.5, 10.0])
async def test_rpe_takes_half_steps_from_six_to_ten(
    client: AsyncClient, rpe: float
) -> None:
    session = await post(client, a_session(sets=[a_set(rpe=rpe)]))

    assert session["logged_sets"][0]["rpe"] == rpe


@pytest.mark.parametrize("rpe", [8.3, 5.5, 10.5])
async def test_rpe_off_the_half_step_scale_is_a_located_422(
    client: AsyncClient, rpe: float
) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(rpe=rpe)]))

    located(response, "body", "sets", 0, "rpe")


async def test_four_plus_reps_left_is_stored_as_four_and_capped(
    client: AsyncClient,
) -> None:
    session = await post(client, a_session(sets=[a_set(rir=4, rir_capped=True)]))

    (entry,) = session["logged_sets"]
    assert entry["rir"] == 4
    assert entry["rir_capped"] is True


@pytest.mark.parametrize("rir", [3, None])
async def test_capped_reps_left_needs_exactly_four(
    client: AsyncClient, rir: int | None
) -> None:
    response = await client.post(
        MANUAL, json=a_session(sets=[a_set(rir=rir, rir_capped=True)])
    )

    assert response.status_code == 422, response.text
    assert "rir_capped" in response.json()["detail"]


@pytest.mark.parametrize("band", ["0-3", "4-5", "6+"])
async def test_pain_comes_in_three_bands(client: AsyncClient, band: str) -> None:
    session = await post(
        client, a_session(sets=[a_set(pain_kind="sharp", pain_band=band)])
    )

    (entry,) = session["logged_sets"]
    assert entry["pain_kind"] == "sharp"
    assert entry["pain_band"] == band


@pytest.mark.parametrize("band", ["7", "6-10", "0-5", ""])
async def test_any_other_pain_band_is_a_located_422(
    client: AsyncClient, band: str
) -> None:
    response = await client.post(
        MANUAL, json=a_session(sets=[a_set(pain_kind="sharp", pain_band=band)])
    )

    located(response, "body", "sets", 0, "pain_band")


@pytest.mark.parametrize(("rir", "rpe"), [(2, 8.0), (1, 8.5), (0, 10.0), (4, 6.0)])
async def test_reps_left_and_rpe_together_must_agree(
    client: AsyncClient, rir: int, rpe: float
) -> None:
    """RPE 8.5 is 1.5 reps left, which a whole-rep count records as 1."""
    session = await post(client, a_session(sets=[a_set(rir=rir, rpe=rpe)]))

    (entry,) = session["logged_sets"]
    assert (entry["rir"], entry["rpe"]) == (rir, rpe)


@pytest.mark.parametrize(("rir", "rpe"), [(1, 8.0), (2, 8.5), (3, 8.0)])
async def test_reps_left_and_rpe_that_disagree_are_refused(
    client: AsyncClient, rir: int, rpe: float
) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(rir=rir, rpe=rpe)]))

    assert response.status_code == 422, response.text
    assert "rpe" in response.json()["detail"]


async def test_a_set_that_answers_a_planned_line_does_not_inherit_its_effort(
    client: AsyncClient,
) -> None:
    """S10.1: unrated stays unrated — the planned line's target is not a report."""
    session = await post(client, a_session(sets=[a_set(planned_line_ref="0")]))

    (entry,) = session["logged_sets"]
    assert entry["planned_line_ref"] == "0"
    assert entry["rir"] is None
    assert entry["rpe"] is None
    assert entry["rir_capped"] is False


# --- the rest of what a set and a session now say -----------------------------


async def test_every_new_field_is_stored_and_read_back(client: AsyncClient) -> None:
    session = await post(
        client,
        a_session(
            dont_learn=True,
            dont_learn_reason="slept three hours",
            session_rating=8,
            sets=[
                a_set(client_id=SQUAT_ID, set_kind="warmup", reps=10, load_kg=20.0),
                {
                    "exercise_id": "bulgarian_split_squat",
                    "replaced_exercise_id": "back_squat",
                    "reps": 8,
                    "load_kg": 16.0,
                    "side": "left",
                    "set_kind": "working",
                    "source": "app",
                    "confidence": "low",
                    "rir": 2,
                    "rest_s": 90,
                    "completed_at": "2026-05-11T17:42:10+02:00",
                    "utc_offset_min": 120,
                    "planned_line_ref": "1",
                    "pain_kind": "ache",
                },
                {
                    "exercise_name": "Side plank",
                    "duration_s": 40,
                    "side": "right",
                    "hold_quality": "shaky",
                },
            ],
        ),
    )

    assert session["dont_learn"] is True
    assert session["dont_learn_reason"] == "slept three hours"
    assert session["session_rating"] == 8
    warmup, left, hold = session["logged_sets"]
    assert warmup["set_kind"] == "warmup"
    assert warmup["side"] == "both"
    assert warmup["client_id"] == SQUAT_ID
    assert left["side"] == "left"
    assert left["replaced_exercise_id"] == "back_squat"
    assert left["source"] == "app"
    assert left["confidence"] == "low"
    assert left["rest_s"] == 90
    assert left["completed_at"] == "2026-05-11T15:42:10Z"
    assert left["utc_offset_min"] == 120
    assert left["planned_line_ref"] == "1"
    assert left["pain_kind"] == "ache"
    assert left["pain_band"] is None
    assert hold["hold_quality"] == "shaky"
    assert hold["side"] == "right"


async def test_a_typed_in_set_defaults_to_a_working_recap_of_both_sides(
    client: AsyncClient,
) -> None:
    session = await post(client, a_session(sets=[a_set()]))

    (entry,) = session["logged_sets"]
    assert entry["set_kind"] == "working"
    assert entry["side"] == "both"
    assert entry["source"] == "recap"
    assert entry["confidence"] == "high"
    assert session["dont_learn"] is False
    assert session["dont_learn_reason"] is None
    assert session["session_rating"] is None


async def test_one_side_of_a_two_sided_movement_is_refused(
    client: AsyncClient,
) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(side="left")]))

    assert response.status_code == 422, response.text
    assert "both sides at once" in response.json()["detail"]


async def test_a_replaced_movement_must_be_in_the_catalogue(
    client: AsyncClient,
) -> None:
    response = await client.post(
        MANUAL, json=a_session(sets=[a_set(replaced_exercise_id="moon_squat")])
    )

    assert response.status_code == 404, response.text


async def test_a_movement_does_not_replace_itself(client: AsyncClient) -> None:
    response = await client.post(
        MANUAL, json=a_session(sets=[a_set(replaced_exercise_id="back_squat")])
    )

    assert response.status_code == 422, response.text
    assert "replaced_exercise_id" in response.json()["detail"]


async def test_hold_quality_belongs_to_a_hold(client: AsyncClient) -> None:
    response = await client.post(
        MANUAL, json=a_session(sets=[a_set(hold_quality="clean")])
    )

    assert response.status_code == 422, response.text
    assert "hold_quality" in response.json()["detail"]


async def test_a_pain_band_needs_a_pain_kind(client: AsyncClient) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(pain_band="4-5")]))

    assert response.status_code == 422, response.text
    assert "pain_kind" in response.json()["detail"]


async def test_the_athlete_cannot_write_a_set_as_the_coachs(
    client: AsyncClient,
) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(source="coach")]))

    assert response.status_code == 422, response.text
    assert "coach" in response.json()["detail"]


async def test_a_reason_not_to_learn_needs_the_flag(client: AsyncClient) -> None:
    response = await client.post(
        MANUAL, json=a_session(dont_learn_reason="bad day", dont_learn=False)
    )

    assert response.status_code == 422, response.text
    assert "dont_learn" in response.json()["detail"]


@pytest.mark.parametrize("rating", [0, 11])
async def test_the_session_rating_is_one_to_ten(
    client: AsyncClient, rating: int
) -> None:
    response = await client.post(MANUAL, json=a_session(session_rating=rating))

    located(response, "body", "session_rating")


@pytest.mark.parametrize(
    ("field", "value"),
    [("rest_s", -1), ("utc_offset_min", 900), ("completed_at", "2026-05-11T17:42:10")],
)
async def test_set_timing_is_bounded(
    client: AsyncClient, field: str, value: Any
) -> None:
    response = await client.post(MANUAL, json=a_session(sets=[a_set(**{field: value})]))

    located(response, "body", "sets", 0, field)
