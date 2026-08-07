"""The ingest API: upload, the quarantine queue, and the two decisions.

Through HTTP, because that is the contract Phase C builds on. What the
pipeline does with the file tree is asserted in `test_ingest_pipeline`; what
is asserted here is the shape of the answers and the statuses.
"""

from pathlib import Path
from typing import Any

from httpx import AsyncClient

from tests.unit.activity_files import gpx_document, tcx_document
from tests.unit.golden_fit import golden

UPLOAD = "/api/v1/ingest/upload"
QUARANTINE = "/api/v1/ingest/quarantine"
EVENTS = "/api/v1/ingest/events"


async def upload(client: AsyncClient, name: str, content: bytes) -> dict[str, Any]:
    """Upload one file and return the ingest report."""
    response = await client.post(
        UPLOAD, files={"file": (name, content, "application/octet-stream")}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_uploading_a_ride_creates_a_session(
    data_root: Path, client: AsyncClient
) -> None:
    report = await upload(client, "ride.fit", golden("outdoor_ride.fit").read_bytes())

    assert report["outcome"] == "ingested"
    assert report["filename"] == "ride.fit"
    assert len(report["session_ids"]) == 1
    assert report["quarantine_ids"] == []
    assert len(report["file_hash"]) == 64

    listed = (await client.get("/api/v1/sessions")).json()
    assert [item["id"] for item in listed["items"]] == report["session_ids"]


async def test_uploading_the_same_file_twice_is_a_duplicate_not_an_error(
    data_root: Path, client: AsyncClient
) -> None:
    content = golden("outdoor_ride.fit").read_bytes()
    first = await upload(client, "ride.fit", content)

    second = await upload(client, "ride.fit", content)

    assert second["outcome"] == "duplicate_file"
    # The client is told which sessions it already exists as, so the page can
    # link to the ride rather than just saying "no".
    assert second["session_ids"] == first["session_ids"]


async def test_uploading_a_corrupt_file_answers_with_the_quarantine_record(
    data_root: Path, client: AsyncClient
) -> None:
    report = await upload(client, "broken.fit", b"not a fit file" * 20)

    assert report["outcome"] == "quarantined"
    assert len(report["quarantine_ids"]) == 1

    queue = (await client.get(QUARANTINE)).json()
    assert queue["total"] == 1
    [record] = queue["items"]
    assert record["reason"] == "unreadable_file"
    assert record["status"] == "pending"
    assert record["original_filename"] == "broken.fit"
    assert record["suspected_session_id"] is None
    # A server filesystem path is not part of the contract.
    assert "quarantined_path" not in record


async def test_an_empty_upload_is_refused(data_root: Path, client: AsyncClient) -> None:
    response = await client.post(
        UPLOAD, files={"file": ("empty.fit", b"", "application/octet-stream")}
    )

    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


async def test_a_filename_cannot_escape_the_inbox(
    data_root: Path, client: AsyncClient
) -> None:
    # The name is written to disk, so it is rebuilt from a safe alphabet
    # rather than trusted. The file lands in the inbox and nowhere else.
    await upload(client, "../../etc/ride.fit", golden("brick.fit").read_bytes())

    assert not (data_root.parent / "etc").exists()
    assert not list((data_root / "inbox").iterdir()), "it was ingested and moved"


async def test_the_quarantine_queue_and_the_log_need_a_session(
    data_root: Path, anon_client: AsyncClient
) -> None:
    for path in (QUARANTINE, EVENTS):
        assert (await anon_client.get(path)).status_code == 401
    upload_response = await anon_client.post(
        UPLOAD, files={"file": ("ride.fit", b"x", "application/octet-stream")}
    )
    assert upload_response.status_code == 401


async def test_the_ingest_log_records_every_file_newest_first(
    data_root: Path, client: AsyncClient
) -> None:
    content = golden("outdoor_ride.fit").read_bytes()
    await upload(client, "ride.fit", content)
    await upload(client, "ride.fit", content)

    log = (await client.get(EVENTS, params={"limit": 10})).json()

    assert log["total"] == 2
    assert [event["outcome"] for event in log["items"]] == [
        "duplicate_file",
        "ingested",
    ]
    assert log["items"][1]["session_id"] is not None


async def test_the_list_endpoints_are_bounded(
    data_root: Path, client: AsyncClient
) -> None:
    for path in (QUARANTINE, EVENTS):
        assert (await client.get(path, params={"limit": 500})).status_code == 422
        assert (await client.get(path, params={"offset": -1})).status_code == 422


async def test_confirming_a_duplicate_closes_it_and_discards_the_copy(
    data_root: Path, client: AsyncClient
) -> None:
    await upload(client, "ride.gpx", gpx_document().encode())
    twin = await upload(client, "ride.tcx", tcx_document().encode())
    [record_id] = twin["quarantine_ids"]

    response = await client.post(f"{QUARANTINE}/{record_id}/confirm")

    assert response.status_code == 200, response.text
    record = response.json()
    assert record["status"] == "confirmed_discarded"
    assert record["resolved_at"] is not None
    assert not list((data_root / "quarantine").iterdir()), "the copy is gone"
    assert list((data_root / "originals").glob("**/*.gpx")), "the twin's original stays"
    assert (await client.get("/api/v1/sessions")).json()["total"] == 1


async def test_confirming_twice_is_a_conflict(
    data_root: Path, client: AsyncClient
) -> None:
    await upload(client, "ride.gpx", gpx_document().encode())
    twin = await upload(client, "ride.tcx", tcx_document().encode())
    [record_id] = twin["quarantine_ids"]
    await client.post(f"{QUARANTINE}/{record_id}/confirm")

    response = await client.post(f"{QUARANTINE}/{record_id}/confirm")

    assert response.status_code == 409
    assert "already resolved" in response.json()["detail"]


async def test_rejecting_a_duplicate_ingests_it_as_its_own_session(
    data_root: Path, client: AsyncClient
) -> None:
    await upload(client, "ride.gpx", gpx_document().encode())
    twin = await upload(client, "ride.tcx", tcx_document().encode())
    [record_id] = twin["quarantine_ids"]

    response = await client.post(f"{QUARANTINE}/{record_id}/reject")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record"]["status"] == "rejected_ingested"
    assert body["report"]["outcome"] == "ingested"
    assert len(body["report"]["session_ids"]) == 1
    sessions = (await client.get("/api/v1/sessions")).json()
    assert sessions["total"] == 2
    assert body["report"]["session_ids"][0] in [
        item["id"] for item in sessions["items"]
    ]
    assert list((data_root / "originals").glob("**/*.tcx")), "it became an original"


async def test_rejecting_a_corrupt_file_is_a_conflict(
    data_root: Path, client: AsyncClient
) -> None:
    # Disagreeing with the parser does not make the bytes readable; there is
    # nothing in the file that is safe to ingest.
    report = await upload(client, "broken.fit", b"not a fit file" * 20)
    [record_id] = report["quarantine_ids"]

    response = await client.post(f"{QUARANTINE}/{record_id}/reject")

    assert response.status_code == 409
    assert "suspected duplicate" in response.json()["detail"]
    assert (await client.get("/api/v1/sessions")).json()["total"] == 0


async def test_a_decision_on_an_unknown_record_is_a_404(
    data_root: Path, client: AsyncClient
) -> None:
    missing = "0199a1b2-0000-7000-8000-000000000000"

    for action in ("confirm", "reject"):
        response = await client.post(f"{QUARANTINE}/{missing}/{action}")
        assert response.status_code == 404, action


async def test_the_queue_leads_with_what_is_still_waiting(
    data_root: Path, client: AsyncClient
) -> None:
    await upload(client, "broken.fit", b"not a fit file" * 20)
    resolved = (await client.get(QUARANTINE)).json()["items"][0]["id"]
    await client.post(f"{QUARANTINE}/{resolved}/confirm")
    await upload(client, "short.gpx", gpx_document(seconds=range(0, 60, 5)).encode())

    queue = (await client.get(QUARANTINE)).json()

    assert queue["total"] == 2
    assert queue["items"][0]["status"] == "pending"
    assert queue["items"][0]["reason"] == "too_short"
