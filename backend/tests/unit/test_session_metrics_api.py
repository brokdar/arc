"""Metrics through HTTP: computed on ingest, versioned, and never overwritten.

Every fixture is a real golden file run through the real pipeline, so the
numbers the API returns are the numbers the domain computes. The test that
matters most is the version chain: recomputing after a new FTP must change the
**new** version's pin and leave the old version — and its numbers — exactly as
they were, because that is what invariant 1 promises and what WP-7's contested
verdicts will depend on.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.schemas.metrics import SessionMetricsRead
from app.domain.activity import SessionDiscipline
from app.domain.metrics import intensity_factor, normalized_power, training_load
from app.domain.session_analysis import SessionInputs, analyse_session, analysis_to_json
from app.domain.streams import MOVING_SPEED_MS
from tests.unit.golden_fit import golden

SESSIONS = "/api/v1/sessions"
MANUAL = "/api/v1/manual-sessions"
ANCHORS = "/api/v1/anchors"
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


async def append_anchor(
    client: AsyncClient, anchor_type: str, value: float, *, effective: str
) -> str:
    """Append one anchor version and return its id."""
    response = await client.post(
        ANCHORS,
        json={
            "anchor_type": anchor_type,
            "value": value,
            "provenance": "estimated",
            "effective_date": effective,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def full_anchor_set(client: AsyncClient) -> None:
    """Everything the metric set can pin, so nothing is missing by accident."""
    await append_anchor(client, "ftp", 250.0, effective="2026-01-01")
    await append_anchor(client, "lthr", 165.0, effective="2026-01-01")
    await append_anchor(client, "max_hr", 190.0, effective="2026-01-01")
    await append_anchor(client, "resting_hr", 50.0, effective="2026-01-01")
    await client.patch("/api/v1/athlete", json={"sex": "male"})


def slots(document: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every metric slot in a rendered artefact, with the path that reached it."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(document, dict):
        if "not_assessed" in document and "value" in document:
            return [(path, document)]
        for key, value in document.items():
            found.extend(slots(value, f"{path}.{key}"))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(slots(value, f"{path}[{index}]"))
    return found


# --- computed on ingest -------------------------------------------------------


async def test_ingesting_a_ride_computes_its_metrics(
    data_root: Path, client: AsyncClient
) -> None:
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()

    metrics = session["metrics"]
    assert metrics is not None
    assert metrics["version"] == 1
    assert metrics["recompute_reason"] is None
    assert metrics["power"]["normalized_power"]["value"] > 0
    assert metrics["load"]["training_load"] > 0
    assert metrics["load"]["load_basis"] == "power"


async def test_the_stored_numbers_agree_with_the_domain_run_directly(
    data_root: Path, client: AsyncClient
) -> None:
    """The artefact must not be a second implementation of the chain (A3.2)."""
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()
    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()

    [power] = [
        channel["values"]
        for channel in streams["channels"]
        if channel["channel"] == "power"
    ]
    expected_np = normalized_power([value for value in power if value is not None])
    expected_if = intensity_factor(expected_np, 250.0)
    recording_time_s = session["recordings"][0]["recording_time_s"]
    metrics = session["metrics"]

    assert metrics["power"]["normalized_power"]["value"] == pytest.approx(expected_np)
    assert metrics["power"]["intensity_factor"]["value"] == pytest.approx(expected_if)
    assert metrics["load"]["power_load"] == pytest.approx(
        training_load(round(recording_time_s), expected_if)
    )
    # A5.1: the duration term is recording time, not elapsed.
    assert metrics["recording_time_s"] == pytest.approx(recording_time_s)


async def test_every_metric_slot_answers_exactly_once(
    data_root: Path, client: AsyncClient
) -> None:
    # The invariant the UI branches on. Asserted over the wire because that is
    # where a serialisation bug would put both fields on one slot.
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    metrics = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]

    found = slots(metrics)
    assert found
    for path, slot in found:
        assert (slot["value"] is None) != (slot["not_assessed"] is None), path


async def test_a_ride_reports_the_basics_a_ride_log_is_read_for(
    data_root: Path, client: AsyncClient
) -> None:
    """Distance, speed, temperature and standing time, over the wire.

    They come from the same golden file the load does, so this also pins the
    relationship between them: the distance is the **odometer** channel that
    file carries, differenced end to end (D200), and the average speed is that
    distance over the moving time the same artefact reports (D194) — not over
    its recording time, which is longer.
    """
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()
    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()
    metrics = session["metrics"]

    columns = {channel["channel"]: channel["values"] for channel in streams["channels"]}
    odometer = [value for value in columns["distance"] if value is not None]
    expected_km = (odometer[-1] - odometer[0]) / 1000
    integrated_km = sum(v for v in columns["speed"] if v is not None) / 1000
    assert metrics["speed"]["distance_km"]["value"] == pytest.approx(expected_km)
    # And it is the odometer's number, not the speed column's: the golden file
    # is built so the two differ by more than any rounding could explain.
    assert expected_km > integrated_km * 1.01
    assert metrics["speed"]["average_speed_kmh"]["value"] == pytest.approx(
        expected_km / (metrics["moving_time_s"] / 3600)
    )
    assert metrics["speed"]["max_speed_kmh"]["value"] > 0
    assert metrics["temperature"]["average_temp_c"]["value"] == pytest.approx(17.0)
    # Elapsed minus moving, derived server-side so no client has to choose
    # which pair of durations to subtract.
    assert metrics["stopped_time_s"]["value"] == pytest.approx(
        metrics["elapsed_time_s"] - metrics["moving_time_s"]
    )
    # And the row in the log carries the distance, off the same artefact.
    [row] = [
        item
        for item in (await client.get(SESSIONS)).json()["items"]
        if item["id"] == session_id
    ]
    assert row["distance_km"] == pytest.approx(expected_km)


async def test_average_power_is_divided_by_moving_time_not_recording_time(
    data_root: Path, client: AsyncClient
) -> None:
    """D194 and D196, re-derived from the stream the same session serves.

    Which divisor was used is half of it; the other half is that the sum above
    the divisor covers **exactly** the seconds the divisor counted (D196). Both
    are recomputed here from the cleaned speed and power columns the streams
    endpoint returns — the same grid the artefact was computed over — so a
    numerator and a denominator that came to describe different stretches of
    the ride would show up here rather than in an athlete's support question.
    """
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    session = (await client.get(f"{SESSIONS}/{session_id}")).json()
    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()
    metrics = session["metrics"]
    columns = {channel["channel"]: channel["values"] for channel in streams["channels"]}

    moving = [
        index
        for index, value in enumerate(columns["speed"])
        if value is not None and value >= MOVING_SPEED_MS
    ]
    joules = sum(
        watts for index in moving if (watts := columns["power"][index]) is not None
    )
    assert moving
    assert metrics["moving_time_s"] == pytest.approx(len(moving))
    assert metrics["moving_time_s"] != metrics["recording_time_s"]
    assert metrics["power"]["average_power"]["value"] == pytest.approx(
        joules / len(moving)
    )
    # The load did not follow it: its duration term is still recording time.
    assert any(
        "recording time" in note
        for note in metrics["load"]["explanation"]["assumptions"]
    )


async def test_the_variability_index_is_a_ratio_over_one_series(
    data_root: Path, client: AsyncClient
) -> None:
    """D196: VI cannot mix the moving-time average with an NP over every row.

    Its two terms are statistics of the same recorded series, which is what
    puts it at or above 1; the average power beside it on the page is a
    different number over a different divisor, and the artefact says so.
    """
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    metrics = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]
    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()

    [watts] = [
        channel["values"]
        for channel in streams["channels"]
        if channel["channel"] == "power"
    ]
    recorded = [value for value in watts if value is not None]
    power = metrics["power"]
    assert power["variability_index"]["value"] >= 1.0
    assert power["variability_index"]["value"] == pytest.approx(
        power["normalized_power"]["value"] / (sum(recorded) / len(recorded))
    )
    assert "recorded rows" in power["variability_index"]["explanation"]["formula"]


def test_an_artefact_written_before_these_numbers_holds_their_slots() -> None:
    """An older payload has no key for a metric added later.

    It must still validate — the version chain is append-only and every
    artefact already written stays readable — and the slot must carry a reason
    naming the remedy, not a null the page would render as a gap or a zero.
    """
    payload = analysis_to_json(
        analyse_session(
            SessionInputs(
                discipline=SessionDiscipline.CYCLING,
                recording_time_s=0.0,
                elapsed_time_s=0.0,
                columns={},
            )
        )
    )
    for key in ("speed", "temperature", "stopped_time_s"):
        del payload[key]

    read = SessionMetricsRead.model_validate(
        payload
        | {
            "version": 1,
            "computed_at": dt.datetime(2026, 8, 5, tzinfo=dt.UTC),
            "recompute_reason": None,
            "pins": [],
            "power_zone_model": None,
            "hr_zone_model": None,
        }
    )

    for slot in (
        read.speed.distance_km,
        read.speed.average_speed_kmh,
        read.speed.max_speed_kmh,
        read.temperature.average_temp_c,
        read.stopped_time_s,
    ):
        assert slot.value is None
        assert slot.not_assessed is not None
        assert "recompute" in slot.not_assessed


async def test_a_ride_with_no_anchors_still_gets_an_artefact(
    data_root: Path, client: AsyncClient
) -> None:
    # Absence is never an error: a metric failure must not un-ingest a file.
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    metrics = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]

    assert metrics is not None
    assert metrics["pins"] == []
    assert metrics["power"]["normalized_power"]["value"] > 0
    assert metrics["power"]["intensity_factor"]["not_assessed"]
    assert metrics["load"]["not_assessed"]
    assert metrics["time_in_zone"]["power"]["not_assessed"]


async def test_the_artefact_pins_what_it_was_computed_against(
    data_root: Path, client: AsyncClient
) -> None:
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    metrics = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]

    pinned = {pin["anchor_type"]: pin for pin in metrics["pins"]}
    assert set(pinned) == {"ftp", "lthr", "max_hr", "resting_hr"}
    assert pinned["ftp"]["value"] == 250.0
    assert pinned["ftp"]["provenance"] == "estimated"
    # A5.5: the zone model is pinned too, or the distribution silently
    # re-derives the day a second model exists.
    assert metrics["power_zone_model"] == "coggan_7"
    assert metrics["hr_zone_model"] == "lthr_5"


# --- the version chain --------------------------------------------------------


async def test_recompute_supersedes_and_the_old_version_stays_readable(
    data_root: Path, client: AsyncClient
) -> None:
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")
    first = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]

    response = await client.post(
        f"{SESSIONS}/{session_id}/metrics/recompute",
        json={"reason": "anchor changed"},
    )

    assert response.status_code == 200, response.text
    second = response.json()
    assert second["version"] == 2
    assert second["recompute_reason"] == "anchor changed"
    # The detail now serves the new tip.
    current = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]
    assert current["version"] == 2
    # Nothing about the ride changed, so the numbers did not either.
    assert current["power"]["normalized_power"]["value"] == pytest.approx(
        first["power"]["normalized_power"]["value"]
    )
    # And a recompute is how a session written by an earlier metric set gains
    # a number added since: the new version is computed by the current domain
    # over the same stored stream, so the whole set is there.
    assert current["speed"]["distance_km"]["value"] > 0
    assert current["stopped_time_s"]["value"] is not None


async def test_a_new_ftp_moves_the_new_versions_pin_and_not_the_old_ones(
    data_root: Path, client: AsyncClient
) -> None:
    """Invariant 1, at its sharpest: history is not re-derived.

    The old version keeps the FTP it was computed against **and** the IF it
    computed from it. If recomputation reached backwards, a verdict confirmed
    last month would silently change meaning.
    """
    await full_anchor_set(client)
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")
    before = (await client.get(f"{SESSIONS}/{session_id}")).json()["metrics"]
    old_pin = next(pin for pin in before["pins"] if pin["anchor_type"] == "ftp")

    new_ftp = await append_anchor(client, "ftp", 300.0, effective="2026-06-01")
    after = (
        await client.post(
            f"{SESSIONS}/{session_id}/metrics/recompute",
            json={"reason": "FTP re-tested"},
        )
    ).json()

    new_pin = next(pin for pin in after["pins"] if pin["anchor_type"] == "ftp")
    assert new_pin["version_id"] == new_ftp
    assert new_pin["value"] == 300.0
    assert old_pin["value"] == 250.0
    # A higher FTP means a lower IF for the same ride.
    assert (
        after["power"]["intensity_factor"]["value"]
        < (before["power"]["intensity_factor"]["value"])
    )
    assert after["load"]["power_load"] < before["load"]["power_load"]


async def test_recompute_without_a_body_still_states_a_reason(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    response = await client.post(f"{SESSIONS}/{session_id}/metrics/recompute")

    assert response.status_code == 200, response.text
    assert response.json()["recompute_reason"]


async def test_recomputing_an_unknown_session_is_a_404(
    data_root: Path, client: AsyncClient
) -> None:
    missing = "0199a1b2-0000-7000-8000-000000000000"

    response = await client.post(f"{SESSIONS}/{missing}/metrics/recompute")

    assert response.status_code == 404


async def test_recompute_with_an_unparseable_body_returns_documented_400(
    data_root: Path, client: AsyncClient
) -> None:
    """Found by Schemathesis: FastAPI answers 400, so the contract must say so."""
    missing = "0199a1b2-0000-7000-8000-000000000000"

    response = await client.post(
        f"{SESSIONS}/{missing}/metrics/recompute",
        content=b"\x0f\xff\xfe not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "detail" in response.json()


# --- streams ------------------------------------------------------------------


async def test_the_stream_payload_is_one_index_aligned_grid(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()

    assert streams["length"] > 0
    # A4.1's invariant, at the wire boundary: every column is the same height,
    # which is what lets a client index them together without checking.
    for channel in streams["channels"]:
        assert len(channel["values"]) == streams["length"], channel["channel"]
    assert {channel["channel"] for channel in streams["channels"]} >= {"power", "hr"}
    assert dt.datetime.fromisoformat(streams["t0"]).tzinfo is not None


async def test_a_recording_stop_is_null_across_every_channel(
    data_root: Path, client: AsyncClient
) -> None:
    # A paused recording is a hole, not a run of zeros — the chart draws a
    # break, and reading the nulls as zero would invent an hour of soft-pedal.
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()

    stops = streams["recording_stops"]
    assert stops, "the outdoor-ride golden file has a ten-minute coffee stop"
    start, end = stops[0]["start_index"], stops[0]["end_index"]
    for channel in streams["channels"]:
        assert all(value is None for value in channel["values"][start:end]), channel[
            "channel"
        ]


async def test_the_repairs_are_marked_and_the_certificates_are_not(
    data_root: Path, client: AsyncClient
) -> None:
    session_id = await ingest(client, "ride.fit", "outdoor_ride.fit")

    streams = (await client.get(f"{SESSIONS}/{session_id}/streams")).json()

    kinds = {anomaly["kind"] for anomaly in streams["anomalies"]}
    assert "resampled_only" not in kinds
    for anomaly in streams["anomalies"]:
        assert 0 <= anomaly["start_index"] < anomaly["end_index"] <= streams["length"]


async def test_a_manual_session_has_no_streams_and_says_why(
    data_root: Path, client: AsyncClient
) -> None:
    created = await client.post(
        MANUAL,
        json={
            "start_time": "2026-05-11T17:30:00+02:00",
            "timezone": "Europe/Zurich",
            "duration_s": 3600,
            "sets": [{"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0}],
        },
    )
    session_id = created.json()["id"]

    response = await client.get(f"{SESSIONS}/{session_id}/streams")

    assert response.status_code == 404
    # The empty state the page renders *is* this sentence.
    assert "hand" in response.json()["detail"]


# --- the manual (strength) path ----------------------------------------------


async def test_a_manual_session_gets_its_volume_load_on_creation(
    data_root: Path, client: AsyncClient
) -> None:
    created = await client.post(
        MANUAL,
        json={
            "start_time": "2026-05-11T17:30:00+02:00",
            "timezone": "Europe/Zurich",
            "duration_s": 3600,
            "sets": [
                {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0},
                {"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0},
                {"exercise_name": "Copenhagen plank", "reps": 8},
            ],
        },
    )

    metrics = (await client.get(f"{SESSIONS}/{created.json()['id']}")).json()["metrics"]

    assert metrics["strength"]["volume_load_kg"] == pytest.approx(1_000.0)
    assert metrics["strength"]["sets_completed"] == 3
    assert metrics["strength"]["coverage"] == pytest.approx(2 / 3)
    # Kilograms are not a load, and nothing pretends otherwise.
    assert metrics["load"]["not_assessed"]


# --- the list column ----------------------------------------------------------


async def test_the_list_row_carries_the_load_and_its_basis(
    data_root: Path, client: AsyncClient
) -> None:
    await full_anchor_set(client)
    await ingest(client, "ride.fit", "outdoor_ride.fit")

    [item] = (await client.get(SESSIONS)).json()["items"]

    assert item["load"] > 0
    assert item["load_basis"] == "power"


async def test_a_list_row_with_no_load_keeps_its_slot(
    data_root: Path, client: AsyncClient
) -> None:
    # No anchors, so neither model is computable. The column is null, and the
    # reason lives on the detail — a list is not where an explanation fits.
    await ingest(client, "ride.fit", "outdoor_ride.fit")

    [item] = (await client.get(SESSIONS)).json()["items"]

    assert item["load"] is None
    assert item["load_basis"] is None


# --- the manual path resolves the same inputs a recompute will ----------------


async def manual_session(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Type in a gym session and return it as the API answers."""
    payload: dict[str, Any] = {
        "start_time": "2026-05-11T17:30:00+02:00",
        "timezone": "Europe/Zurich",
        "duration_s": 3600,
        "sets": [{"exercise_id": "back_squat", "reps": 5, "load_kg": 100.0}],
    } | overrides
    response = await client.post(MANUAL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def numbers(metrics: dict[str, Any]) -> dict[str, Any]:
    """One version's payload, without the fields a new version must change."""
    return {
        key: value
        for key, value in metrics.items()
        if key not in {"version", "computed_at", "recompute_reason"}
    }


async def test_a_manual_session_pins_the_anchors_in_force(
    data_root: Path, client: AsyncClient
) -> None:
    # The artefact records what the computation looked at. Without the pins,
    # it claimed "no anchor is in force" for an athlete who has four.
    await full_anchor_set(client)

    created = await manual_session(client)

    metrics = created["metrics"]
    assert {pin["anchor_type"] for pin in metrics["pins"]} == {
        "ftp",
        "lthr",
        "max_hr",
        "resting_hr",
    }
    # And the reason HRSS is missing is the honest one: there was no heart
    # rate, not an absent anchor.
    assert "heart rate" in metrics["heart_rate"]["hrss"]["not_assessed"]


async def test_recomputing_an_unchanged_manual_session_reproduces_it(
    data_root: Path, client: AsyncClient
) -> None:
    """Two paths, one answer.

    Creating a manual session computes its metrics in the service; recomputing
    goes through `app.ingest.analysis`. When those resolved their inputs
    separately, version 2 of an untouched session differed from version 1 —
    a version chain whose links disagree for no reason anyone can point at.
    """
    await full_anchor_set(client)
    created = await manual_session(client)
    session_id = created["id"]

    second = (await client.post(f"{SESSIONS}/{session_id}/metrics/recompute")).json()

    assert second["version"] == 2
    assert numbers(second) == numbers(created["metrics"])


async def test_a_metric_failure_does_not_lose_a_typed_in_session(
    data_root: Path, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session is committed before its metrics are computed.

    So a failure afterwards must leave the athlete with a stored session and
    no numbers — not a 500 over work that already succeeded, which a client
    retries and thereby creates a *second* session.
    """

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the metrics writer fell over")

    monkeypatch.setattr(
        "app.services.metrics.SessionMetricsService.record_strength", boom
    )

    created = await manual_session(client)

    assert created["metrics"] is None
    # And it really is stored: the log has exactly one of it.
    listed = (await client.get(SESSIONS)).json()
    assert [item["id"] for item in listed["items"]] == [created["id"]]


async def test_a_metric_failure_does_not_break_a_correction(
    data_root: Path, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await manual_session(client)

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the metrics writer fell over")

    monkeypatch.setattr(
        "app.services.metrics.SessionMetricsService.record_strength", boom
    )
    response = await client.patch(
        f"{SESSIONS}/{created['id']}", json={"discipline": "cycling"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["discipline"] == "cycling"


# --- only what changes the numbers appends a version --------------------------


async def test_a_timezone_correction_leaves_the_version_chain_alone(
    data_root: Path, client: AsyncClient
) -> None:
    # A timezone touches no metric input. A chain that grew a link every time
    # the athlete fixed one would drown the question it exists to answer.
    created = await manual_session(client)
    assert created["metrics"]["version"] == 1

    corrected = await client.patch(
        f"{SESSIONS}/{created['id']}", json={"timezone": "UTC"}
    )

    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["metrics"]["version"] == 1


async def test_re_submitting_the_same_discipline_is_not_a_change(
    data_root: Path, client: AsyncClient
) -> None:
    created = await manual_session(client)

    corrected = await client.patch(
        f"{SESSIONS}/{created['id']}", json={"discipline": "strength"}
    )

    assert corrected.json()["metrics"]["version"] == 1


async def test_a_discipline_correction_does_append_a_version(
    data_root: Path, client: AsyncClient
) -> None:
    # It changes which load model is preferred (A5.2), so the artefact is
    # stale the moment it lands.
    created = await manual_session(client)

    corrected = await client.patch(
        f"{SESSIONS}/{created['id']}", json={"discipline": "cycling"}
    )

    metrics = corrected.json()["metrics"]
    assert metrics["version"] == 2
    assert metrics["recompute_reason"] == "session corrected"
