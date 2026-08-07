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

from app.domain.metrics import intensity_factor, normalized_power, training_load
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
