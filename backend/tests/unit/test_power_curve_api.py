"""`GET /api/v1/sessions/{id}/power-curve`, through HTTP.

Every session here comes through the real ingest pipeline (or the manual-entry
endpoint), so the stored curve is the one `SessionAnalyser` actually wrote —
and the expected numbers are recomputed by the domain over the stored stream
the endpoint's row was computed from, never typed in.
"""

import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.power_curve import GRID_VERSION, duration_grid, power_curve
from app.domain.streams import AnomalyKind, StreamChannel
from app.ingest.parquet import read_streams, stream_path
from app.persistence.activity import RecordingRow, StreamAnomalyRow
from app.persistence.power_curve import SessionMmpRow
from app.services.power_curve import NO_POWER, NO_RECORDING, NOT_COMPUTED
from tests.unit.activity_files import gpx_document
from tests.unit.golden_fit import golden

UPLOAD = "/api/v1/ingest/upload"


def curve_url(session_id: str) -> str:
    """The endpoint for one session."""
    return f"/api/v1/sessions/{session_id}/power-curve"


async def upload(client: AsyncClient, name: str, content: bytes) -> str:
    """Ingest one file and return the session it became."""
    response = await client.post(
        UPLOAD, files={"file": (name, content, "application/octet-stream")}
    )
    assert response.status_code == 200, response.text
    [session_id] = response.json()["session_ids"]
    return str(session_id)


async def expected_points(
    db_session: AsyncSession, data_root: Path, session_id: str
) -> list[dict[str, Any]]:
    """The domain's curve over the session's stored stream, as the API spells it."""
    recording = (
        await db_session.execute(
            sa.select(RecordingRow).where(
                RecordingRow.session_id == uuid.UUID(session_id)
            )
        )
    ).scalar_one()
    stored = read_streams(stream_path(data_root / "streams", recording.id))
    anomalies = (
        await db_session.execute(
            sa.select(StreamAnomalyRow).where(
                StreamAnomalyRow.recording_id == recording.id,
                StreamAnomalyRow.channel == StreamChannel.POWER,
                StreamAnomalyRow.kind != AnomalyKind.RESAMPLED_ONLY,
            )
        )
    ).scalars()
    curve = power_curve(
        stored.fixed[StreamChannel.POWER],
        anomalies=[(row.start_index, row.end_index) for row in anomalies],
    )
    assert curve is not None
    return [
        {
            "duration_s": point.duration_s,
            "watts": point.watts,
            "start_index": point.start_index,
            "clean": point.clean,
        }
        for point in curve.points
    ]


async def test_a_ride_returns_its_curve(
    data_root: Path, client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await upload(
        client, "ride.fit", golden("outdoor_ride.fit").read_bytes()
    )

    response = await client.get(curve_url(session_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "assessed"
    assert body["grid_version"] == GRID_VERSION
    assert body["points"] == await expected_points(db_session, data_root, session_id)
    durations = [point["duration_s"] for point in body["points"]]
    assert durations == list(duration_grid(durations[-1]))
    assert {5, 60, 300, 1200} <= set(durations)
    # The golden ride has a clipped 2 900 W spike and a coffee stop: the short
    # bests that stand on the repair, and the long ones that span the stop,
    # say so.
    assert any(not point["clean"] for point in body["points"])
    assert body["points"][-1]["clean"] is False


async def test_a_session_with_no_power_meter_is_not_assessed_never_zero(
    data_root: Path, client: AsyncClient
) -> None:
    # The strength watch file: thirty minutes of heart rate and nothing else.
    session_id = await upload(
        client, "watch.fit", golden("strength_watch.fit").read_bytes()
    )

    response = await client.get(curve_url(session_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "not_assessed", "reason": NO_POWER}


async def test_a_session_entered_by_hand_is_not_assessed(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/manual-sessions",
        json={
            "start_time": "2026-08-10T17:00:00Z",
            "timezone": "UTC",
            "duration_s": 3_600,
            "discipline": "cycling",
        },
    )
    assert created.status_code == 201, created.text

    response = await client.get(curve_url(created.json()["id"]))

    assert response.json() == {"status": "not_assessed", "reason": NO_RECORDING}


async def test_a_ride_from_before_the_curve_existed_says_so(
    data_root: Path, client: AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await upload(
        client, "ride.fit", golden("outdoor_ride.fit").read_bytes()
    )
    await db_session.execute(sa.delete(SessionMmpRow))
    await db_session.commit()

    response = await client.get(curve_url(session_id))

    assert response.json() == {"status": "not_assessed", "reason": NOT_COMPUTED}


async def test_a_ride_with_four_seconds_of_power_answers_four_durations(
    data_root: Path, client: AsyncClient
) -> None:
    # A 1 Hz track whose meter reported only for its first four seconds.
    marker = "<power>210</power>"
    head, *rest = gpx_document(seconds=range(600), power=210).split(marker)
    document = head + "".join(
        (marker if index < 4 else "") + chunk for index, chunk in enumerate(rest)
    )
    assert document.count(marker) == 4
    session_id = await upload(client, "ride.gpx", document.encode())

    response = await client.get(curve_url(session_id))

    body = response.json()
    assert body["status"] == "assessed", body
    assert [point["duration_s"] for point in body["points"]] == [1, 2, 3, 4]
    assert all(point["watts"] == 210.0 for point in body["points"])


async def test_an_unknown_session_is_a_404(client: AsyncClient) -> None:
    response = await client.get(curve_url(str(uuid.uuid7())))

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


async def test_the_curve_needs_a_login(anon_client: AsyncClient) -> None:
    response = await anon_client.get(curve_url(str(uuid.uuid7())))

    assert response.status_code == 401
