"""The backfill: `just rebuild-streams` gives every older ride its power curve.

A migration cannot read parquet, so `session_mmp` starts empty for every
session already in the store. The operator's path to fill it is the rebuild
script, which re-derives each stream and recomputes each session's metrics —
and the recompute is where the curve is written. The state "recorded before
the migration" is produced here exactly: a real ingest, then its curve row
removed, which is what the table looks like the moment `0022` has run.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.actor import Actor
from app.ingest.pipeline import IngestPipeline
from app.persistence.activity import RecordingRow
from app.persistence.power_curve import SessionMmpRow
from scripts import rebuild_streams
from tests.unit.golden_fit import golden


async def ingest(data_root: Path, db_session: AsyncSession, name: str) -> None:
    """Put one golden file through the real pipeline."""
    path = data_root / "inbox" / name
    path.write_bytes(golden(name).read_bytes())
    await IngestPipeline.from_session(db_session).ingest_file(
        path, actor=Actor.athlete()
    )


async def curve_rows(db_session: AsyncSession) -> list[SessionMmpRow]:
    """Every stored curve, read fresh."""
    db_session.expire_all()
    return list((await db_session.execute(sa.select(SessionMmpRow))).scalars())


def snapshot(rows: list[SessionMmpRow]) -> list[tuple[object, ...]]:
    """Everything about the stored curves that a rerun could change."""
    return [
        (
            row.id,
            row.session_id,
            row.grid_version,
            row.durations_s,
            row.watts_dw,
            row.start_indices,
            row.clean,
            row.computed_at,
        )
        for row in rows
    ]


async def predate_the_migration(db_session: AsyncSession) -> None:
    """Leave the store as `0022` finds it: sessions, and no curves."""
    await db_session.execute(sa.delete(SessionMmpRow))
    await db_session.commit()


async def test_the_rebuild_gives_an_older_ride_its_curve_and_a_rerun_changes_nothing(
    data_root: Path,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest(data_root, db_session, "outdoor_ride.fit")
    [ingested] = await curve_rows(db_session)
    await predate_the_migration(db_session)
    assert await curve_rows(db_session) == []

    exit_code = await rebuild_streams.run(recording_id=None, recompute=True)

    assert exit_code == 0
    [rebuilt] = await curve_rows(db_session)
    assert rebuilt.session_id == ingested.session_id
    # The same stream, so the same curve the ingest wrote.
    assert rebuilt.grid_version == ingested.grid_version
    assert rebuilt.durations_s == ingested.durations_s
    assert rebuilt.watts_dw == ingested.watts_dw
    assert rebuilt.start_indices == ingested.start_indices
    assert rebuilt.clean == ingested.clean
    before = snapshot([rebuilt])

    assert await rebuild_streams.run(recording_id=None, recompute=True) == 0

    assert snapshot(await curve_rows(db_session)) == before


async def test_streams_only_leaves_the_curve_to_the_recompute(
    data_root: Path,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest(data_root, db_session, "outdoor_ride.fit")
    await predate_the_migration(db_session)

    assert await rebuild_streams.run(recording_id=None, recompute=False) == 0

    assert await curve_rows(db_session) == []


async def test_a_session_whose_original_is_gone_is_reported_not_skipped_silently(
    data_root: Path,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    await ingest(data_root, db_session, "outdoor_ride.fit")
    await predate_the_migration(db_session)
    recording = (await db_session.execute(sa.select(RecordingRow))).scalar_one()
    Path(recording.original_path).unlink()

    exit_code = await rebuild_streams.run(recording_id=None, recompute=True)

    output = capsys.readouterr().out
    # Non-zero, a line naming the recording and why, and the count in the
    # summary — and still no curve, because nothing was rebuilt to give one.
    assert exit_code == 1
    assert f"original_missing  {recording.id}" in output
    assert "the original file is not at" in output
    assert "1 skipped" in output
    assert await curve_rows(db_session) == []
