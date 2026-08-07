"""The ingest and metric tables against a real Postgres — the dialect-specific half.

Three things the unit suite cannot prove on SQLite, because SQLite has
neither:

* ``recording_stops``, the source candidates and the channel list are
  `JSONColumn`, which is **JSONB** here and TEXT there. A column that arrived
  as text would answer every ORM read correctly and fail the first time
  anything asked Postgres a question about its contents, so the assertions
  below go through JSONB operators (``->``, ``@>``, ``jsonb_array_length``)
  rather than through the ORM.
* the dedup key's unique constraint, which the pipeline relies on to settle a
  race it cannot read its way out of;
* WP-5's metric artefact: its payload is JSONB here, and its version chain is
  held together by a unique constraint rather than by hope.
"""

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.main import DATA_SUBDIRECTORIES
from app.persistence.activity import RecordingRow
from app.persistence.metrics import SessionMetricsRow
from tests.unit.activity_files import gpx_document

UPLOAD = "/api/v1/ingest/upload"

#: A ride with a ten-minute recording stop in the middle of it, so the row has
#: a stop range to store rather than an empty list.
RIDE = gpx_document(seconds=[*range(0, 301, 5), *range(900, 1201, 5)])


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway runtime data tree, with `DATA__ROOT` pointed at it."""
    root = tmp_path / "runtime-data"
    for name in DATA_SUBDIRECTORIES:
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("DATA__ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


async def upload_ride(client: AsyncClient) -> dict[str, Any]:
    """Upload the ride and return its ingest report."""
    response = await client.post(
        UPLOAD, files={"file": ("ride.gpx", RIDE.encode(), "application/gpx+xml")}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["outcome"] == "ingested", report
    return report


async def test_the_recording_json_columns_are_queryable_jsonb(
    data_root: Path, client: AsyncClient
) -> None:
    await upload_ride(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT jsonb_array_length(recording_stops) AS stops,
                               (recording_stops -> 0 ->> 0)::int AS first_start,
                               (recording_stops -> 0 ->> 1)::int AS first_end,
                               channels @> '["power"]'::jsonb AS has_power,
                               jsonb_typeof(power_source_candidates) AS candidates,
                               elapsed_time_s - recording_time_s AS paused
                        FROM recordings
                        """
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert row.stops == 1
    assert row.has_power is True
    assert row.candidates == "array"
    # A4.4's invariant, asserted on what Postgres itself can see: the paused
    # total is exactly the row range the same row reports.
    assert row.paused == row.first_end - row.first_start


async def test_the_dedup_key_is_unique_in_the_database(
    data_root: Path, client: AsyncClient
) -> None:
    # The pipeline's dedup read can always lose a race; this constraint is what
    # turns the loser into a `duplicate_file` instead of a second session.
    report = await upload_ride(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        async with AsyncSession(engine) as session:
            existing = (
                await session.execute(
                    sa.select(RecordingRow).where(
                        RecordingRow.file_hash == report["file_hash"]
                    )
                )
            ).scalar_one()
            session.add(
                RecordingRow(
                    session_id=existing.session_id,
                    file_hash=existing.file_hash,
                    file_sport_index=existing.file_sport_index,
                    original_path=existing.original_path,
                    original_ext=existing.original_ext,
                    sport=existing.sport,
                    elapsed_time_s=existing.elapsed_time_s,
                    recording_time_s=existing.recording_time_s,
                    recording_stops=[[1, 2]],
                    median_time_delta_s=existing.median_time_delta_s,
                    moving_time_s=existing.moving_time_s,
                    created_at=dt.datetime.now(dt.UTC),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
    finally:
        await engine.dispose()


async def test_the_metric_payload_is_queryable_jsonb(
    data_root: Path, client: AsyncClient
) -> None:
    """WP-5's artefact, on the dialect it will actually run on.

    The payload is a `JSONColumn` — JSONB here, TEXT on SQLite — and the whole
    reason it is JSON rather than columns is that the metric set grows. A
    column that arrived as text would answer every ORM read correctly and fail
    the first time anything asked Postgres a question about it, which is
    exactly what WP-7's trends will do.
    """
    await upload_ride(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT version,
                               superseded_by IS NULL AS is_tip,
                               jsonb_typeof(payload) AS shape,
                               payload ? 'load' AS has_load,
                               (payload -> 'recording_time_s')::float AS recording_s,
                               jsonb_typeof(payload -> 'intervals') AS intervals
                        FROM session_metrics
                        """
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert row.version == 1
    assert row.is_tip is True
    assert row.shape == "object"
    assert row.has_load is True
    assert row.recording_s > 0
    assert row.intervals == "array"


async def test_the_metric_version_chain_is_unique_per_session(
    data_root: Path, client: AsyncClient
) -> None:
    # The no-overwrite rule is enforced through the version chain, so two rows
    # claiming to be version 1 of one session is a corruption of it.
    report = await upload_ride(client)
    engine = create_async_engine(get_settings().postgres.async_url)
    try:
        async with AsyncSession(engine) as session:
            existing = (
                await session.execute(
                    sa.select(SessionMetricsRow).where(
                        SessionMetricsRow.session_id
                        == uuid.UUID(report["session_ids"][0])
                    )
                )
            ).scalar_one()
            session.add(
                SessionMetricsRow(
                    session_id=existing.session_id,
                    version=existing.version,
                    payload={},
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
    finally:
        await engine.dispose()
