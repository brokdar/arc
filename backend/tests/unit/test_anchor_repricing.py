"""Appending an anchor reprices the history it governs (issue #18).

The defect: sessions ingested before an FTP anchor existed kept
``training_load: null`` forever — the versioning doctrine's cascade
(anchors → per-session scores) never fired, and no MCP tool could repair it.

Driven through both adapters — the athlete's HTTP API and the coach's MCP
tool — because both now route the append through
`app.ingest.repricing.append_anchor_and_reprice`, and each reports the
cascade in its own answer. Every fixture is a real golden file run through
the real pipeline, so the numbers that appear after repricing are the numbers
the domain computes.
"""

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.ingest.repricing as repricing
from app.ingest.analysis import SessionAnalyser
from app.ingest.repricing import SCAN_FAILED_NOTE
from app.persistence.anchors import AnchorVersionRow
from tests.unit.golden_fit import golden
from tests.unit.mcp_harness import connected_as, server_for

SESSIONS = "/api/v1/sessions"
ANCHORS = "/api/v1/anchors"
UPLOAD = "/api/v1/ingest/upload"

_KEY = "a1b2c3d4" * 4
COACH = f"coach:write:{_KEY}"

#: The outdoor golden ride's athlete-local day (`tests.unit.golden_fit`,
#: 07:30 UTC at +02:00). Effective dates in these tests sit around it.
RIDE_DAY = dt.date(2026, 5, 4)


async def ingest_ride(client: AsyncClient) -> str:
    """Upload the golden outdoor ride and return its session id."""
    response = await client.post(
        UPLOAD,
        files={
            "file": (
                "ride.fit",
                golden("outdoor_ride.fit").read_bytes(),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 200, response.text
    [session_id] = response.json()["session_ids"]
    return session_id


async def append_ftp(
    client: AsyncClient, *, effective: str, value: float = 250.0
) -> dict[str, Any]:
    """Append an FTP version over HTTP and return the whole response body."""
    response = await client.post(
        ANCHORS,
        json={
            "anchor_type": "ftp",
            "value": value,
            "provenance": "estimated",
            "effective_date": effective,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def metrics_of(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """The current metric artefact of one session, as the detail renders it."""
    response = await client.get(f"{SESSIONS}/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()["metrics"]


async def call_append_anchor(arguments: dict[str, Any]) -> Any:
    """Call the MCP `append_anchor` tool as a write-scoped coach key."""
    async with connected_as(server_for(COACH), COACH) as mcp_client:
        result = await mcp_client.call_tool("append_anchor", arguments)
        return result.data


# --- the cascade, through HTTP ------------------------------------------------


async def test_appending_ftp_reprices_the_sessions_it_governs(
    data_root: Path, client: AsyncClient
) -> None:
    """The issue's own reproduction: ingest first, append after, load appears."""
    session_id = await ingest_ride(client)
    before = await metrics_of(client, session_id)
    assert before["version"] == 1
    assert before["load"]["training_load"] is None
    assert before["pins"] == []

    body = await append_ftp(client, effective="2026-01-01")

    assert body["reprice"] == {
        "examined": 1,
        "repriced": 1,
        "unchanged": 0,
        "failed": 0,
        "note": None,
    }
    after = await metrics_of(client, session_id)
    assert after["version"] == 2
    assert after["recompute_reason"] == "repriced: ftp anchor appended"
    assert after["load"]["training_load"] > 0
    pinned = {pin["anchor_type"]: pin for pin in after["pins"]}
    assert pinned["ftp"]["version_id"] == body["id"]


async def test_a_session_before_the_effective_date_is_untouched(
    data_root: Path, client: AsyncClient
) -> None:
    """A version effective after the ride does not govern the ride's day."""
    session_id = await ingest_ride(client)

    body = await append_ftp(
        client, effective=(RIDE_DAY + dt.timedelta(days=28)).isoformat()
    )

    assert body["reprice"] == {
        "examined": 1,
        "repriced": 0,
        "unchanged": 1,
        "failed": 0,
        "note": None,
    }
    after = await metrics_of(client, session_id)
    assert after["version"] == 1
    assert after["load"]["training_load"] is None


async def test_a_second_identical_append_reports_all_unchanged(
    data_root: Path, client: AsyncClient
) -> None:
    """The cascade converges: an append that changes no number recomputes nothing."""
    session_id = await ingest_ride(client)
    await append_ftp(client, effective="2026-01-01")

    body = await append_ftp(client, effective="2026-01-01")

    assert body["reprice"] == {
        "examined": 1,
        "repriced": 0,
        "unchanged": 1,
        "failed": 0,
        "note": None,
    }
    assert (await metrics_of(client, session_id))["version"] == 2


async def test_a_failed_recompute_is_counted_and_never_unwinds_the_append(
    data_root: Path,
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Append-commits-first: the measurement lands even when repricing breaks."""
    session_id = await ingest_ride(client)

    async def broken_compute(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("stream store on fire")

    monkeypatch.setattr(SessionAnalyser, "compute", broken_compute)
    body = await append_ftp(client, effective="2026-01-01")

    assert body["reprice"] == {
        "examined": 1,
        "repriced": 0,
        "unchanged": 0,
        "failed": 1,
        "note": None,
    }
    # The anchor is committed regardless…
    anchors = list((await db_session.execute(select(AnchorVersionRow))).scalars())
    assert [row.value for row in anchors] == [250.0]
    # …and the session stays individually recomputable once the fault clears.
    monkeypatch.undo()
    response = await client.post(f"{SESSIONS}/{session_id}/metrics/recompute")
    assert response.status_code == 200, response.text
    assert (await metrics_of(client, session_id))["load"]["training_load"] > 0


async def test_a_failed_scan_reports_unknown_not_zero(
    data_root: Path,
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_scan(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("metrics table unreadable")

    monkeypatch.setattr(repricing, "_scan", broken_scan)

    body = await append_ftp(client, effective="2026-01-01")

    assert body["reprice"]["note"] == SCAN_FAILED_NOTE
    assert body["reprice"]["examined"] == 0
    anchors = list((await db_session.execute(select(AnchorVersionRow))).scalars())
    assert [row.value for row in anchors] == [250.0]


# --- the cascade, through MCP -------------------------------------------------


async def test_the_append_anchor_tool_reports_the_cascade(
    data_root: Path, client: AsyncClient, session_factory: Any
) -> None:
    session_id = await ingest_ride(client)

    data = await call_append_anchor(
        {
            "anchor_type": "ftp",
            "value": 250,
            "provenance": "estimated",
            "effective_date": "2026-01-01",
        }
    )

    assert data["dry_run"] is False
    assert data["reprice"] == {
        "examined": 1,
        "repriced": 1,
        "unchanged": 0,
        "failed": 0,
        "note": None,
    }
    after = await metrics_of(client, session_id)
    assert after["version"] == 2
    assert after["load"]["training_load"] > 0


async def test_an_append_dry_run_predicts_the_repricing_and_writes_nothing(
    data_root: Path,
    client: AsyncClient,
    session_factory: Any,
    db_session: AsyncSession,
) -> None:
    session_id = await ingest_ride(client)

    data = await call_append_anchor(
        {
            "anchor_type": "ftp",
            "value": 250,
            "provenance": "estimated",
            "effective_date": "2026-01-01",
            "dry_run": True,
        }
    )

    assert data["dry_run"] is True
    assert data["reprice"] == {"examined": 1, "would_reprice": 1, "unchanged": 0}
    # Nothing was appended and nothing recomputed.
    assert list((await db_session.execute(select(AnchorVersionRow))).scalars()) == []
    assert (await metrics_of(client, session_id))["version"] == 1


async def test_a_dry_run_after_a_backward_clock_step_predicts_with_the_correction(
    data_root: Path,
    client: AsyncClient,
    session_factory: Any,
    db_session: AsyncSession,
) -> None:
    """The dry-run draft wins the same `created_at` tie-break a real append wins.

    The prediction folds the draft into the stored history and asks the same
    `anchor_effective_on` the write-side scan asks, so the draft's stamp
    fights the same ``(effective_date, created_at)`` tie-break. Stamped raw
    off a stepped-back wall clock, a same-effective-date correction loses to
    the version it corrects and the dry run predicts `would_reprice: 0` —
    governed by the old value — while the real append, clamped, reprices.
    The stamp must be chosen by one piece of code for both paths.

    Same move-the-row mechanism as the clock-step tests in
    `test_anchors_api.py`: a history whose newest stamp is ahead of now is
    exactly the state a backwards step leaves the next append (or dry run) in.
    """
    session_id = await ingest_ride(client)
    body = await append_ftp(client, effective="2026-01-01", value=250)
    assert (await metrics_of(client, session_id))["version"] == 2
    row = await db_session.get(AnchorVersionRow, uuid.UUID(body["id"]))
    assert row is not None
    row.created_at = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)
    await db_session.commit()

    data = await call_append_anchor(
        {
            "anchor_type": "ftp",
            "value": 300,
            "provenance": "estimated",
            "effective_date": "2026-01-01",
            "dry_run": True,
        }
    )

    # The correction governs the prediction, exactly as it would the write.
    assert data["dry_run"] is True
    assert data["reprice"] == {"examined": 1, "would_reprice": 1, "unchanged": 0}
