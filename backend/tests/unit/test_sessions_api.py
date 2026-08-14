"""The completed-session API: the list, the detail, the two overrides, manual entry.

Every fixture here is produced by running a real file through the pipeline
rather than by inserting rows, so the numbers the API returns are the numbers
the domain computes — which is the only way this suite can notice that a
projection stopped agreeing with the thing it projects.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from tests.unit.golden_fit import golden

SESSIONS = "/api/v1/sessions"
MANUAL = "/api/v1/manual-sessions"
UPLOAD = "/api/v1/ingest/upload"


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


def a_manual_session(**overrides: Any) -> dict[str, Any]:
    """A gym session as the form submits it."""
    payload: dict[str, Any] = {
        "start_time": "2026-05-11T17:30:00+02:00",
        "timezone": "Europe/Zurich",
        "duration_s": 3600,
        "rpe": 7,
        "notes": "Felt strong",
        "sets": [
            {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0, "rir": 2},
            {"exercise_name": "Copenhagen plank", "reps": 8, "notes": "each side"},
        ],
    }
    payload.update(overrides)
    return payload


async def test_the_list_reads_newest_first_and_carries_the_badge_state(
    data_root: Path, client: AsyncClient
) -> None:
    await ingest(client, "ride.fit", "outdoor_ride.fit")  # 2026-05-04
    await ingest(client, "gym.fit", "strength_watch.fit")  # 2026-05-07

    listed = (await client.get(SESSIONS)).json()

    assert listed["total"] == 2
    assert [item["local_date"] for item in listed["items"]] == [
        "2026-05-07",
        "2026-05-04",
    ]
    assert [item["discipline"] for item in listed["items"]] == ["strength", "cycling"]
    # WP-6 matches on ingest. Nothing is planned here, so both sessions come
    # out `unplanned` — decided, not undecided — and neither carries a link.
    assert {item["status"] for item in listed["items"]} == {"unplanned"}
    assert {item["match"] for item in listed["items"]} == {None}
    assert {item["recording_kind"] for item in listed["items"]} == {"device"}


async def test_the_list_row_reports_recording_time_not_elapsed(
    data_root: Path, client: AsyncClient
) -> None:
    # A5.1: the duration a ride is judged by has the coffee stop taken out.
    await ingest(client, "ride.fit", "outdoor_ride.fit")

    [item] = (await client.get(SESSIONS)).json()["items"]

    # 2400 s elapsed minus the stop's 599 rows.
    assert item["recording_time_s"] == 1801.0
    assert item["duration_s"] == item["recording_time_s"]


async def test_the_list_can_be_bounded_by_date_and_discipline(
    data_root: Path, client: AsyncClient
) -> None:
    await ingest(client, "ride.fit", "outdoor_ride.fit")
    await ingest(client, "gym.fit", "strength_watch.fit")

    window = await client.get(
        SESSIONS, params={"start": "2026-05-06", "end": "2026-05-08"}
    )
    strength = await client.get(SESSIONS, params={"discipline": "strength"})

    assert [item["local_date"] for item in window.json()["items"]] == ["2026-05-07"]
    assert strength.json()["total"] == 1
    assert (await client.get(SESSIONS, params={"limit": 500})).status_code == 422


async def test_the_detail_explains_where_its_numbers_came_from(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "trainer.fit", "indoor_trainer.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()

    [recording] = session["recordings"]
    # A4.3: two meters, both recorded, and the tie-break said out loud.
    assert recording["power_source_candidates"] == [
        "srm/7 #1",
        "wahoo_fitness/42 #2",
    ]
    assert recording["power_source"] == "srm/7 #1"
    assert "device_index" in recording["power_source_rule"]
    assert recording["recording_stops"] == []
    assert recording["median_time_delta_s"] == 1.0
    assert sorted(recording["channels"]) == ["cadence", "hr", "power", "speed"]
    assert session["logged_sets"] == []


async def test_the_detail_counts_repairs_and_not_the_channels_that_needed_none(
    data_root: Path, client: AsyncClient
) -> None:
    # The outdoor ride has exactly one repair — the 2 900 W spike. The seven
    # untouched channels store a `resampled_only` row each, and counting those
    # would tell the athlete a clean ride had eight anomalies.
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()

    [recording] = session["recordings"]
    assert recording["anomaly_count"] == 1
    [stop] = recording["recording_stops"]
    assert stop == {"start_index": 601, "end_index": 1200}
    # Exactly the stop's row range separates the two durations.
    assert recording["elapsed_time_s"] - recording["recording_time_s"] == (
        stop["end_index"] - stop["start_index"]
    )


async def test_the_detail_does_not_carry_streams(
    data_root: Path, client: AsyncClient
) -> None:
    # WP-5 owns the stream endpoints; a detail response with 14 400 rows per
    # channel would be the wrong resource for every page that exists today.
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()

    assert "streams" not in session
    assert "samples" not in session["recordings"][0]


async def test_an_unknown_session_is_a_404(
    data_root: Path, client: AsyncClient
) -> None:
    missing = "0199a1b2-0000-7000-8000-000000000000"

    assert (await client.get(f"{SESSIONS}/{missing}")).status_code == 404
    patch = await client.patch(f"{SESSIONS}/{missing}", json={"discipline": "other"})
    assert patch.status_code == 404


async def test_sessions_need_a_session_cookie(
    data_root: Path, anon_client: AsyncClient
) -> None:
    assert (await anon_client.get(SESSIONS)).status_code == 401
    assert (await anon_client.post(MANUAL, json=a_manual_session())).status_code == 401
    # The detail and the override, too. The id need not exist: the session
    # guard runs before the lookup, so a stranger gets 401 rather than the
    # 404 that would tell them whether the id is real.
    missing = "0199a1b2-0000-7000-8000-000000000000"
    assert (await anon_client.get(f"{SESSIONS}/{missing}")).status_code == 401
    patch = await anon_client.patch(
        f"{SESSIONS}/{missing}", json={"discipline": "other"}
    )
    assert patch.status_code == 401


async def test_overriding_the_discipline_records_that_the_athlete_decided(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")

    response = await client.patch(
        f"{SESSIONS}/{session_id}", json={"discipline": "other"}
    )

    assert response.status_code == 200, response.text
    session = response.json()
    assert session["discipline"] == "other"
    assert session["discipline_overridden"] is True
    # No later re-classification may quietly claim it guessed this.
    assert session["classification_source"] == "manual"


async def test_overriding_the_timezone_re_derives_the_local_date(
    data_root: Path, client: AsyncClient
) -> None:
    # The gym session starts at 17:00 UTC. Fourteen hours east of that is the
    # next day — which is the whole reason the column holds a zone and not an
    # offset that happened to be true once.
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")

    response = await client.patch(
        f"{SESSIONS}/{session_id}", json={"timezone": "Pacific/Kiritimati"}
    )

    assert response.status_code == 200, response.text
    session = response.json()
    assert session["timezone"] == "Pacific/Kiritimati"
    assert session["local_date"] == "2026-05-08"
    assert session["start_time"].startswith("2026-05-07T17:00:00")


async def test_an_unresolvable_timezone_is_refused(
    data_root: Path, client: AsyncClient
) -> None:
    # A stored timezone that cannot be resolved makes the session's date
    # unrecoverable, so it fails where it is written.
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")
    before = (await client.get(f"{SESSIONS}/{session_id}")).json()

    response = await client.patch(
        f"{SESSIONS}/{session_id}", json={"timezone": "Middle/Earth"}
    )

    assert response.status_code == 422
    # And the refusal left the row alone. A 422 that had already written the
    # zone would put the session on a day it was never on, which is the exact
    # damage the check exists to prevent.
    assert (await client.get(f"{SESSIONS}/{session_id}")).json() == before


# A session always has a discipline and always has a timezone, so neither
# field has a null to mean anything and the service refuses one. The *schema*
# used to advertise `X | None` anyway, which is the same schema/parser mismatch
# query parameters have: the contract promised something the API rejects,
# and Schemathesis' fuzzer
# fails on exactly that (`API rejected schema-compliant request`). The two
# tests below hold both ends — the contract no longer offers `null`, and the
# service still refuses one from a caller who sends it anyway.


@pytest.mark.parametrize("field", ["discipline", "timezone"])
async def test_a_session_field_that_cannot_be_cleared_is_refused(
    data_root: Path, client: AsyncClient, field: str
) -> None:
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")
    before = (await client.get(f"{SESSIONS}/{session_id}")).json()

    response = await client.patch(f"{SESSIONS}/{session_id}", json={field: None})

    assert response.status_code == 422, response.text
    assert f"{field} cannot be cleared" in response.json()["detail"]
    assert (await client.get(f"{SESSIONS}/{session_id}")).json() == before


async def test_the_session_patch_contract_offers_null_exactly_where_it_means_clear(
    data_root: Path, client: AsyncClient
) -> None:
    spec = (await client.get("/openapi.json")).json()

    update = spec["components"]["schemas"]["SessionUpdate"]["properties"]

    # `SkipJsonSchema[None]` keeps the Python-side unset default and drops the
    # `null` branch from the contract, so the schema promises exactly what the
    # parser accepts: omit a non-clearable field to leave it alone, never
    # null it.
    assert "null" not in str(update["discipline"]), update["discipline"]
    assert update["timezone"]["type"] == "string"
    # The context fields are the opposite on purpose: an explicit null
    # clears them, so the contract must advertise it.
    assert "null" in str(update["rpe"]), update["rpe"]
    assert "null" in str(update["temperature_c"]), update["temperature_c"]


# --- measurement context on any recorded session (#23) -------------------------


async def test_context_can_be_recorded_on_an_ingested_session(
    data_root: Path, client: AsyncClient
) -> None:
    # The point of #23: a device file never carries an RPE or a temperature,
    # and before this the only write path was manual creation — which an
    # ingested session never passes through.
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    response = await client.patch(
        f"{SESSIONS}/{session_id}", json={"rpe": 4, "temperature_c": 29.5}
    )

    assert response.status_code == 200, response.text
    session = response.json()
    assert session["rpe"] == 4
    assert session["temperature_c"] == 29.5
    # And the list row carries both, so the log is filterable on them.
    [item] = (await client.get(SESSIONS)).json()["items"]
    assert item["rpe"] == 4
    assert item["temperature_c"] == 29.5


async def test_an_explicit_null_clears_a_context_field_and_omission_keeps_it(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")
    await client.patch(f"{SESSIONS}/{session_id}", json={"rpe": 6, "temperature_c": 22})

    cleared = await client.patch(f"{SESSIONS}/{session_id}", json={"rpe": None})

    assert cleared.status_code == 200, cleared.text
    session = cleared.json()
    # Patch semantics: the nulled field is cleared, the omitted one untouched.
    assert session["rpe"] is None
    assert session["temperature_c"] == 22


@pytest.mark.parametrize(("temperature_c", "bound"), [(-31, "-30"), (51, "50")])
async def test_an_implausible_temperature_is_refused_naming_the_bound(
    data_root: Path, client: AsyncClient, temperature_c: float, bound: str
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    response = await client.patch(
        f"{SESSIONS}/{session_id}", json={"temperature_c": temperature_c}
    )

    assert response.status_code == 422, response.text
    assert bound in response.text
    assert (await client.get(f"{SESSIONS}/{session_id}")).json()[
        "temperature_c"
    ] is None


async def test_an_out_of_scale_rpe_is_refused(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    response = await client.patch(f"{SESSIONS}/{session_id}", json={"rpe": 11})

    assert response.status_code == 422


async def test_an_empty_patch_is_refused(data_root: Path, client: AsyncClient) -> None:
    session_id = await ingest(client, "gym.fit", "strength_watch.fit")

    response = await client.patch(f"{SESSIONS}/{session_id}", json={})

    assert response.status_code == 422
    assert "at least one" in response.json()["detail"]


async def test_a_manual_session_is_stored_with_its_sets(
    data_root: Path, client: AsyncClient
) -> None:
    response = await client.post(MANUAL, json=a_manual_session())

    assert response.status_code == 201, response.text
    session = response.json()
    assert session["recording_kind"] == "manual"
    assert session["discipline"] == "strength"
    # Nobody classified it: the athlete said what it was.
    assert session["classification_source"] == "manual"
    assert session["recordings"] == []
    assert session["recording_time_s"] is None
    assert session["duration_s"] == 3600.0
    assert session["local_date"] == "2026-05-11"
    assert session["rpe"] == 7
    assert session["temperature_c"] is None
    assert [entry["set_index"] for entry in session["logged_sets"]] == [0, 1]
    catalogue, free_text = session["logged_sets"]
    # A catalogue set stores the catalogue's name, so the row stays readable
    # if the catalogue moves on.
    assert catalogue["exercise_id"] == "back_squat"
    assert catalogue["exercise_name"] == "Back Squat"
    assert free_text["exercise_id"] is None
    assert free_text["exercise_name"] == "Copenhagen plank"


async def test_a_manual_session_can_state_the_temperature_it_was_performed_at(
    data_root: Path, client: AsyncClient
) -> None:
    response = await client.post(MANUAL, json=a_manual_session(temperature_c=31.0))

    assert response.status_code == 201, response.text
    assert response.json()["temperature_c"] == 31.0
    too_hot = await client.post(MANUAL, json=a_manual_session(temperature_c=85))
    assert too_hot.status_code == 422


async def test_a_manual_session_appears_in_the_list_beside_device_sessions(
    data_root: Path, client: AsyncClient
) -> None:
    await ingest(client, "ride.fit", "outdoor_ride.fit")
    await client.post(MANUAL, json=a_manual_session())

    listed = (await client.get(SESSIONS)).json()

    assert listed["total"] == 2
    kinds = {item["recording_kind"] for item in listed["items"]}
    assert kinds == {"device", "manual"}
    [manual] = [item for item in listed["items"] if item["recording_kind"] == "manual"]
    assert manual["recording_time_s"] is None
    assert manual["duration_s"] == 3600.0


async def test_a_set_names_a_catalogue_movement_or_its_own_but_not_both(
    data_root: Path, client: AsyncClient
) -> None:
    both = await client.post(
        MANUAL,
        json=a_manual_session(
            sets=[{"exercise_id": "back_squat", "exercise_name": "Squat", "reps": 5}]
        ),
    )
    neither = await client.post(MANUAL, json=a_manual_session(sets=[{"reps": 5}]))

    assert both.status_code == 422
    assert "exactly one" in both.json()["detail"]
    assert neither.status_code == 422


async def test_a_set_naming_an_unknown_catalogue_movement_is_a_404(
    data_root: Path, client: AsyncClient
) -> None:
    response = await client.post(
        MANUAL, json=a_manual_session(sets=[{"exercise_id": "moon_squat", "reps": 5}])
    )

    assert response.status_code == 404


async def test_a_manual_session_needs_a_resolvable_timezone_and_a_real_duration(
    data_root: Path, client: AsyncClient
) -> None:
    bad_zone = await client.post(MANUAL, json=a_manual_session(timezone="Middle/Earth"))
    too_short = await client.post(MANUAL, json=a_manual_session(duration_s=5))
    naive = await client.post(
        MANUAL, json=a_manual_session(start_time="2026-05-11T17:30:00")
    )

    assert bad_zone.status_code == 422
    assert too_short.status_code == 422
    assert naive.status_code == 422
    # None of the three wrote a row. A half-created session is worse than a
    # refusal: it shows up in the log as a session that never happened.
    assert (await client.get(SESSIONS)).json()["total"] == 0


async def test_a_manual_session_defaults_to_utc_and_no_sets(
    data_root: Path, client: AsyncClient
) -> None:
    response = await client.post(
        MANUAL,
        json={
            "start_time": dt.datetime(2026, 5, 11, 17, 30, tzinfo=dt.UTC).isoformat(),
            "duration_s": 1800,
        },
    )

    assert response.status_code == 201, response.text
    session = response.json()
    assert session["timezone"] == "UTC"
    assert session["local_date"] == "2026-05-11"
    assert session["logged_sets"] == []
    assert session["rpe"] is None


# --- the wellness of the session's own day (AC-12, AC-41) ----------------------

WELLNESS_DAYS = "/api/v1/wellness/days"


async def test_a_session_carries_the_wellness_of_its_own_day(
    client: AsyncClient,
) -> None:
    # The question worth answering is not "was HRV low on the 11th" but "does
    # poor sleep predict poor execution for this athlete", and after this the
    # two halves are one read rather than two and a date match.
    session_id = (await client.post(MANUAL, json=a_manual_session())).json()["id"]
    await client.patch(
        f"{WELLNESS_DAYS}/2026-05-11",
        json={"sleep_duration_s": 21_600, "fatigue": 4, "confounders": ["short_sleep"]},
    )

    body = (await client.get(f"{SESSIONS}/{session_id}")).json()

    assert body["local_date"] == "2026-05-11"
    assert body["wellness"]["sleep_duration_s"] == 21_600
    assert body["wellness"]["markers"]["actionable"] is False


async def test_a_session_on_a_day_with_no_wellness_still_reads(
    client: AsyncClient,
) -> None:
    # "Not recorded" rather than a 404 on the session: the athlete not
    # answering their morning form is not a broken session.
    session_id = (await client.post(MANUAL, json=a_manual_session())).json()["id"]

    response = await client.get(f"{SESSIONS}/{session_id}")

    assert response.status_code == 200, response.text
    assert response.json()["wellness"] is None
    assert response.json()["weight_kg_in_force"] is None


async def test_a_session_carries_the_weight_governing_its_date(
    client: AsyncClient,
) -> None:
    # AC-12: watts per kilogram is derivable per session without a second call,
    # and the weight names the day it was recorded on — a w/kg computed against
    # a three-week-old weight should say so.
    await client.patch(f"{WELLNESS_DAYS}/2026-05-01", json={"weight_kg": 78.0})
    await client.patch(f"{WELLNESS_DAYS}/2026-06-01", json={"weight_kg": 82.0})
    session_id = (await client.post(MANUAL, json=a_manual_session())).json()["id"]

    body = (await client.get(f"{SESSIONS}/{session_id}")).json()

    assert body["weight_kg_in_force"] == {
        "weight_kg": 78.0,
        "effective_date": "2026-05-01",
        "on": "2026-05-11",
    }
