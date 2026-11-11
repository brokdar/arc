"""Everything logged can leave arc: full JSON, set-level CSV, Strong CSV, text.

Lock-in is one of the athletes' top complaints, and the second thing asked
for was handing one workout to an AI assistant. Every assertion here reads a
response body — the artefact the athlete downloads or pastes — never the
service's intermediate values.

The fixture session is posted through `POST /manual-sessions`, so it is a
session the real API stores; the one legacy row (an assistance weight stored
as a negative, which today's API refuses) is written straight to the table,
because that is the only way such a row exists.

The openweight schema under `tests/vectors/openweight/` is vendored verbatim
from https://github.com/radupana/openweight at commit
d4f625c872893ee4b053d8e0f0a6642a44985b3e
(`docs/public/schemas/workout-log.schema.json`, Apache-2.0).
"""

import csv
import datetime as dt
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from jsonschema import Draft7Validator
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.activity import LoggedSetRow

MANUAL = "/api/v1/manual-sessions"
SESSIONS = "/api/v1/sessions"
WORKOUTS_JSON = "/api/v1/exports/workouts.json"
SETS_CSV = "/api/v1/exports/sets.csv"
STRONG_CSV = "/api/v1/exports/strong.csv"

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
#: The committed text the single-workout export must equal, byte for byte.
#: The frontend's session export test serves this same file from its mock.
EXPECTED_TEXT = VECTORS / "session_export.txt"
OPENWEIGHT = VECTORS / "openweight" / "workout-log.schema.json"

SETS_HEADER = (
    "workout_id,workout_start,workout_end,workout_title,workout_notes,"
    "exercise_order,exercise_id,exercise_name,equipment,superset_id,"
    "exercise_notes,set_index,set_type,weight_kg,added_weight_kg,assistance_kg,"
    "bodyweight_kg,reps,duration_s,distance_m,rpe,rir,side,is_warmup,is_failure,"
    "is_drop,set_notes,completed_at,arc_set_kind,arc_client_id"
)
STRONG_HEADER = (
    "Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps,Distance,"
    "Seconds,Notes,Workout Notes,RPE"
)

LEFT_ID = "0199a1b2-0000-7000-8000-00000000000a"
RIGHT_ID = "0199a1b2-0000-7000-8000-00000000000b"
PULL_UP_ID = "0199a1b2-0000-7000-8000-00000000000c"


def fixture_session() -> dict[str, Any]:
    """A gym session touching every shape a set can take in an export.

    A warm-up, two working sets (one with a formula-looking note), a one-side
    pair, a timed hold, and a weighted bodyweight drop set.
    """
    return {
        "start_time": "2026-05-11T17:30:00+02:00",
        "timezone": "Europe/Zurich",
        "duration_s": 3600,
        "rpe": 7,
        "notes": "Felt strong",
        "sets": [
            {
                "exercise_id": "back_squat",
                "reps": 5,
                "load_kg": 60.0,
                "set_kind": "warmup",
            },
            {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0, "rir": 2},
            {
                "exercise_id": "back_squat",
                "reps": 5,
                "load_kg": 102.5,
                "rir": 1,
                "rpe": 9,
                "notes": "=SUM(A1)",
            },
            {
                "exercise_id": "bulgarian_split_squat",
                "reps": 8,
                "load_kg": 20.0,
                "side": "left",
                "client_id": LEFT_ID,
            },
            {
                "exercise_id": "bulgarian_split_squat",
                "reps": 8,
                "load_kg": 20.0,
                "side": "right",
                "client_id": RIGHT_ID,
            },
            {"exercise_id": "front_plank", "duration_s": 45},
            {
                "exercise_id": "pull_up",
                "reps": 6,
                "load_kg": 5.0,
                "set_kind": "drop",
                "client_id": PULL_UP_ID,
            },
        ],
    }


def frontend_gym_session() -> dict[str, Any]:
    """The gym session `frontend/tests/mocks/fixtures.ts` seeds, as posted.

    `ACTIVITY_IDS.gym` there: same start, zone, length, effort, note and
    sets. Change one and the other together — the session-page export test
    serves `EXPECTED_TEXT` as this session's export.
    """
    return {
        "start_time": "2026-08-06T16:30:00Z",
        "timezone": "Europe/Zurich",
        "duration_s": 3600,
        "rpe": 7,
        "notes": "Felt strong; added a set of pull-ups at the end.",
        "sets": [
            {"exercise_id": "back_squat", "reps": 5, "load_kg": 100, "rir": 2},
            {"exercise_id": "back_squat", "reps": 5, "load_kg": 102.5, "rir": 1},
            {"exercise_name": "Pull-up", "reps": 8, "notes": "strict"},
            {
                "exercise_id": "single_arm_dumbbell_row",
                "reps": 11,
                "per_side": True,
                "load_kg": 15,
                "rir": 2,
            },
            {"exercise_id": "front_plank", "duration_s": 45},
        ],
    }


async def post_session(client: AsyncClient, payload: dict[str, Any]) -> str:
    """Store a manual session and return its id."""
    response = await client.post(MANUAL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def rows_of(text: str, delimiter: str = ",") -> list[dict[str, str]]:
    """A CSV body as dicts keyed by its own header."""
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


async def make_legacy_assistance(db_session: AsyncSession) -> None:
    """Turn the pull-up into a legacy assisted set: load stored as -5.0."""
    await db_session.execute(
        update(LoggedSetRow)
        .where(LoggedSetRow.client_id == uuid.UUID(PULL_UP_ID))
        .values(load_kg=-5.0)
    )
    await db_session.commit()


# --- AC-80: one workout as text, exactly --------------------------------------


async def test_session_text_export_equals_the_committed_text(
    client: AsyncClient,
) -> None:
    """The text a person pastes into an assistant is the committed text.

    The session is the frontend's gym fixture, posted as the phone would, so
    the committed file is what the real API says about the session the
    session-page test opens — and that test serves this very file.
    """
    session_id = await post_session(client, frontend_gym_session())

    response = await client.get(f"{SESSIONS}/{session_id}/export?format=text")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == EXPECTED_TEXT.read_text(encoding="utf-8")


async def test_session_text_export_spells_out_every_kind_of_set(
    client: AsyncClient,
) -> None:
    """Warm-up lettered, a left-right pair numbered once, kinds in words."""
    session_id = await post_session(client, fixture_session())

    response = await client.get(f"{SESSIONS}/{session_id}/export")

    assert response.status_code == 200, response.text
    assert response.text == (
        "Strength — Mon 11 May 2026, 17:30 Europe/Zurich, 60 min\n"
        "Session RPE: 7\n"
        "Notes: Felt strong\n"
        "\n"
        "Back Squat\n"
        "  W  60 kg × 5\n"
        "  1  100 kg × 5, 2 reps left\n"
        "  2  102.5 kg × 5, 1 rep left, RPE 9 — =SUM(A1)\n"
        "Bulgarian Split Squat\n"
        "  1  20 kg × 8, left\n"
        "  1  20 kg × 8, right\n"
        "Front Plank\n"
        "  1  bodyweight, 45 s\n"
        "Pull-Up\n"
        "  1  bodyweight + 5 kg × 6, drop\n"
    )


async def test_session_csv_export_has_one_row_per_set(client: AsyncClient) -> None:
    """The one-workout CSV is the set-level CSV, for that workout alone."""
    session_id = await post_session(client, fixture_session())
    other = {
        "start_time": "2026-05-12T17:30:00+02:00",
        "sets": [{"exercise_id": "back_squat", "reps": 3, "load_kg": 90.0}],
    }
    await post_session(client, fixture_session() | other)

    response = await client.get(f"{SESSIONS}/{session_id}/export?format=csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == SETS_HEADER
    rows = rows_of(response.text)
    assert len(rows) == 7
    assert {row["workout_id"] for row in rows} == {session_id}
    assert rows[2]["set_notes"] == "'=SUM(A1)"


async def test_a_session_without_sets_exports_a_header_only_csv(
    client: AsyncClient,
) -> None:
    """Nothing logged is a header and no rows — not an error, not a blank file."""
    session_id = await post_session(client, fixture_session() | {"sets": []})

    response = await client.get(f"{SESSIONS}/{session_id}/export?format=csv")

    assert response.status_code == 200, response.text
    assert response.text == SETS_HEADER + "\n"


async def test_exporting_an_unknown_session_is_a_404(client: AsyncClient) -> None:
    """A session id that names nothing is answered as one."""
    response = await client.get(
        f"{SESSIONS}/0199a1b2-0000-7000-8000-0000000000ff/export?format=text"
    )

    assert response.status_code == 404, response.text


async def test_an_unknown_export_format_is_refused(client: AsyncClient) -> None:
    """Only text and csv exist."""
    session_id = await post_session(client, fixture_session())

    response = await client.get(f"{SESSIONS}/{session_id}/export?format=pdf")

    assert response.status_code == 422, response.text


# --- AC-79 through the endpoint: escaping, numbers, the semicolon variant -----


async def test_sets_csv_escapes_notes_and_leaves_negative_weights_alone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """'=SUM(A1) for the note; -5.0, unquoted and unprefixed, for the weight."""
    await post_session(client, fixture_session())
    await make_legacy_assistance(db_session)

    response = await client.get(SETS_CSV)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    rows = rows_of(response.text)
    assert [row["set_notes"] for row in rows] == ["", "", "'=SUM(A1)", "", "", "", ""]
    pull_up = rows[-1]
    assert pull_up["weight_kg"] == "-5.0"
    assert pull_up["assistance_kg"] == "5.0"
    assert pull_up["added_weight_kg"] == ""
    assert ",-5.0," in response.text


async def test_sets_csv_rows_carry_order_kind_and_side(client: AsyncClient) -> None:
    """Exercise blocks are numbered, set_index restarts per block, flags agree."""
    session_id = await post_session(client, fixture_session())

    rows = rows_of((await client.get(SETS_CSV)).text)

    assert [row["exercise_order"] for row in rows] == [
        "1",
        "1",
        "1",
        "2",
        "2",
        "3",
        "4",
    ]
    assert [row["set_index"] for row in rows] == ["1", "2", "3", "1", "2", "1", "1"]
    assert [row["set_type"] for row in rows] == [
        "warmup",
        "working",
        "working",
        "working",
        "working",
        "working",
        "drop",
    ]
    assert [row["side"] for row in rows][3:5] == ["left", "right"]
    assert rows[0]["is_warmup"] == "true"
    assert rows[6]["is_drop"] == "true"
    assert rows[6]["added_weight_kg"] == "5.0"
    assert rows[5]["duration_s"] == "45"
    assert rows[5]["reps"] == ""
    assert rows[3]["arc_client_id"] == LEFT_ID
    assert rows[0]["workout_id"] == session_id
    assert rows[0]["workout_start"] == "2026-05-11T17:30:00+02:00"
    assert rows[0]["workout_end"] == "2026-05-11T18:30:00+02:00"
    assert rows[0]["equipment"] == "barbell"


async def test_sets_csv_semicolon_variant_writes_decimal_commas(
    client: AsyncClient,
) -> None:
    """German Excel: 102,5 in a semicolon-separated file."""
    await post_session(client, fixture_session())

    response = await client.get(f"{SETS_CSV}?variant=semicolon")

    assert response.status_code == 200, response.text
    assert response.text.splitlines()[0] == SETS_HEADER.replace(",", ";")
    rows = rows_of(response.text, delimiter=";")
    assert rows[2]["weight_kg"] == "102,5"
    assert ";102,5;" in response.text


async def test_strong_csv_uses_strongs_columns_and_set_order(
    client: AsyncClient,
) -> None:
    """W for a warm-up, D for a drop set, numbers for the rest, per exercise."""
    await post_session(client, fixture_session())

    response = await client.get(STRONG_CSV)

    assert response.status_code == 200, response.text
    assert response.text.splitlines()[0] == STRONG_HEADER
    rows = rows_of(response.text)
    assert [row["Set Order"] for row in rows] == ["W", "1", "2", "1", "2", "1", "D"]
    assert rows[0]["Date"] == "2026-05-11 17:30:00"
    assert rows[0]["Duration"] == "1h 0m"
    assert rows[0]["Workout Name"] == "Strength"
    assert rows[0]["Workout Notes"] == "Felt strong"
    assert rows[2]["Weight"] == "102.5"
    assert rows[2]["Notes"] == "'=SUM(A1)"
    assert rows[2]["RPE"] == "9.0"
    assert rows[5]["Seconds"] == "45"


# --- AC-81: workouts.json is openweight, with arc's extensions ----------------


async def test_workouts_json_validates_against_openweight(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Every workout validates; every set carries arcClientId/arcSide/arcSetKind."""
    await post_session(client, fixture_session())
    await post_session(
        client,
        fixture_session()
        | {
            "start_time": "2026-05-13T07:00:00+02:00",
            "sets": [{"exercise_name": "Sled push", "reps": 4}],
        },
    )
    await make_legacy_assistance(db_session)
    validator = Draft7Validator(json.loads(OPENWEIGHT.read_text(encoding="utf-8")))

    response = await client.get(WORKOUTS_JSON)

    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    document = response.json()
    assert document["format"] == "arc-export"
    assert document["version"] == 1
    workouts = document["workouts"]
    assert len(workouts) == 2
    for workout in workouts:
        errors = [error.message for error in validator.iter_errors(workout)]
        assert errors == [], errors
        # The schema says date-time; jsonschema checks formats only with an
        # optional extra, so the offset is asserted here instead.
        assert dt.datetime.fromisoformat(workout["date"]).utcoffset() is not None
        for exercise in workout["exercises"]:
            for entry in exercise["sets"]:
                assert {"arcClientId", "arcSide", "arcSetKind"} <= entry.keys()

    first = workouts[0]
    assert first["date"] == "2026-05-11T17:30:00+02:00"
    assert first["durationSeconds"] == 3600
    sets = [entry for exercise in first["exercises"] for entry in exercise["sets"]]
    assert [entry["arcSetKind"] for entry in sets] == [
        "warmup",
        "working",
        "working",
        "working",
        "working",
        "working",
        "drop",
    ]
    assert [entry["type"] for entry in sets][-1] == "dropset"
    assert [entry["arcSide"] for entry in sets][3:5] == ["left", "right"]
    assert sets[3]["arcClientId"] == LEFT_ID
    assert sets[4]["arcClientId"] == RIGHT_ID
    assert sets[2]["weight"] == 102.5
    assert sets[2]["unit"] == "kg"
    assert sets[2]["notes"] == "=SUM(A1)"
    # openweight forbids a negative weight; the assistance survives as arc's own.
    assert "weight" not in sets[-1]
    assert sets[-1]["arcAssistanceKg"] == 5.0
    assert sets[5]["durationSeconds"] == 45
    assert "reps" not in sets[5]


async def test_workouts_json_skips_sessions_with_no_sets(client: AsyncClient) -> None:
    """openweight requires at least one exercise; a ride has none to export."""
    await post_session(client, fixture_session() | {"sets": []})

    response = await client.get(WORKOUTS_JSON)

    assert response.status_code == 200, response.text
    assert response.json()["workouts"] == []


# --- the guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        WORKOUTS_JSON,
        SETS_CSV,
        STRONG_CSV,
        f"{SESSIONS}/0199a1b2-0000-7000-8000-0000000000ff/export?format=text",
    ],
)
async def test_exports_need_a_session(anon_client: AsyncClient, path: str) -> None:
    """Your whole training history is not public."""
    response = await anon_client.get(path)

    assert response.status_code == 401, response.text
