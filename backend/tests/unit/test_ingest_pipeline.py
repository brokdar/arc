"""The per-file pipeline: what it writes, what it refuses, what it never loses.

These go through `IngestPipeline` rather than HTTP because the file tree is
the thing under test — where the original ended up, what the parquet frame
holds, which record points at which path. The HTTP surface over the same
use-cases is in `test_ingest_api`.
"""

import datetime as dt
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.activity import (
    ClassificationSource,
    IngestOutcome,
    QuarantineReason,
    QuarantineStatus,
    RecordingKind,
    SessionDiscipline,
)
from app.domain.actor import Actor
from app.domain.streams import StreamChannel
from app.ingest.parquet import read_streams, stream_path
from app.ingest.pipeline import IngestPaths, IngestPipeline
from app.persistence.activity import (
    RecordingRow,
    SessionRow,
    StreamAnomalyRow,
)
from app.persistence.audit import AuditLogEntry
from app.persistence.ingest_log import IngestEventRow, QuarantineRecordRow
from tests.unit.activity_files import gpx_document, tcx_document
from tests.unit.golden_fit import OUTDOOR_STOP_S, golden

ATHLETE = Actor.athlete()


@pytest.fixture
def pipeline(data_root: Path, db_session: AsyncSession) -> IngestPipeline:
    """A pipeline writing into the throwaway data tree."""
    return IngestPipeline.from_session(db_session)


def drop_in(
    data_root: Path, name: str, source: Path | None = None, *, text: str = ""
) -> Path:
    """Put a file in the inbox, from a golden binary or from text."""
    path = data_root / "inbox" / name
    if source is not None:
        path.write_bytes(source.read_bytes())
    else:
        path.write_text(text)
    return path


async def rows[T](session: AsyncSession, model: type[T]) -> list[T]:
    """Every row of one table, for asserting on what a run left behind."""
    result = await session.execute(sa.select(model))
    return list(result.scalars())


async def test_a_ride_becomes_a_session_a_recording_and_a_parquet_frame(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    dropped = drop_in(data_root, "ride.fit", golden("outdoor_ride.fit"))

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.INGESTED
    assert len(report.session_ids) == 1
    assert not report.quarantine_ids
    assert not dropped.exists(), "the inbox copy was moved, not copied"

    [session] = await rows(db_session, SessionRow)
    assert session.recording_kind is RecordingKind.DEVICE
    assert session.discipline is SessionDiscipline.CYCLING
    assert session.classification_source is ClassificationSource.SPORT_FIELD
    assert session.timezone == "UTC+02:00"
    assert session.local_date == dt.date(2026, 5, 4)
    assert session.status.value == "unmatched"

    [recording] = await rows(db_session, RecordingRow)
    assert recording.file_hash == report.file_hash
    assert recording.file_sport_index == 0
    assert recording.original_ext == "fit"
    # A4.4 on the row, not just in memory.
    assert recording.elapsed_time_s - recording.recording_time_s == OUTDOOR_STOP_S
    assert recording.recording_stops == [[601, 1200]]
    assert recording.power_source == "srm/7 #1"
    assert recording.power_source_rule == "only candidate"
    assert StreamChannel.POWER.value in recording.channels

    # The original is filed by month, and the frame is beside it.
    original = Path(recording.original_path)
    assert original.exists()
    assert original.parent == data_root / "originals" / "2026" / "05"
    assert original.name == f"{report.file_hash}.fit"
    stored = read_streams(stream_path(data_root / "streams", recording.id))
    assert stored.row_count == 2401
    assert stored.sources[StreamChannel.POWER] == "srm/7 #1"


async def test_the_ingest_is_logged_and_audited(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    report = await pipeline.ingest_file(
        drop_in(data_root, "ride.fit", golden("outdoor_ride.fit")), actor=ATHLETE
    )

    [event] = await rows(db_session, IngestEventRow)
    assert event.outcome is IngestOutcome.INGESTED
    assert event.filename == "ride.fit"
    assert event.session_id == report.session_ids[0]
    audit = await rows(db_session, AuditLogEntry)
    assert [entry.action for entry in audit] == ["session.ingested"]
    assert audit[0].actor == "athlete"


async def test_the_repairs_are_rows_and_the_untouched_channels_are_certified(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # A4.2: every substituted region is a row, and a channel that needed
    # nothing says so rather than being silent.
    await pipeline.ingest_file(
        drop_in(data_root, "ride.fit", golden("outdoor_ride.fit")), actor=ATHLETE
    )

    anomalies = await rows(db_session, StreamAnomalyRow)
    repairs = [row for row in anomalies if row.kind.value != "resampled_only"]

    assert [(row.channel, row.kind.value) for row in repairs] == [
        (StreamChannel.POWER, "spike_clipped")
    ]
    assert {row.channel for row in anomalies if row.kind.value == "resampled_only"}


async def test_re_seeing_a_file_is_a_duplicate_not_a_second_session(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    first = await pipeline.ingest_file(
        drop_in(data_root, "ride.fit", golden("outdoor_ride.fit")), actor=ATHLETE
    )
    again = drop_in(data_root, "ride-copy.fit", golden("outdoor_ride.fit"))

    second = await pipeline.ingest_file(again, actor=ATHLETE)

    assert second.outcome is IngestOutcome.DUPLICATE_FILE
    assert second.session_ids == first.session_ids
    assert len(await rows(db_session, SessionRow)) == 1
    assert not again.exists(), "the redundant inbox copy is dropped"
    assert Path((await rows(db_session, RecordingRow))[0].original_path).exists(), (
        "the original of the twin is untouched"
    )


async def test_a_multisport_file_becomes_one_session_per_sport_sharing_an_original(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # A4.5, end to end: two sessions, two recordings, one file, one hash, and
    # the dedup key told apart by the sport index.
    report = await pipeline.ingest_file(
        drop_in(data_root, "brick.fit", golden("brick.fit")), actor=ATHLETE
    )

    assert len(report.session_ids) == 2
    recordings = sorted(
        await rows(db_session, RecordingRow), key=lambda row: row.file_sport_index
    )
    assert [row.file_sport_index for row in recordings] == [0, 1]
    assert len({row.file_hash for row in recordings}) == 1
    assert len({row.original_path for row in recordings}) == 1
    assert [row.sport for row in recordings] == ["cycling", "training"]
    disciplines = {row.discipline for row in await rows(db_session, SessionRow)}
    assert disciplines == {SessionDiscipline.CYCLING, SessionDiscipline.STRENGTH}


async def test_a_corrupt_file_is_quarantined_with_the_reason(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    dropped = data_root / "inbox" / "broken.fit"
    dropped.write_bytes(b"not a fit file" * 20)

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    assert not report.session_ids
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.UNREADABLE_FILE
    assert record.status is QuarantineStatus.PENDING
    assert record.file_sport_index is None
    kept = Path(record.quarantined_path)
    assert kept.exists(), "quarantine keeps the file — nothing is lost"
    assert kept.parent == data_root / "quarantine"
    assert not list((data_root / "originals").glob("**/*.fit"))


async def test_a_channel_of_nonsense_is_quarantined_rather_than_repaired(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # A spike is a repair; a whole channel outside the plausible range is a
    # broken file, and the athlete is told instead of shown a cleaned average.
    dropped = drop_in(
        data_root, "absurd.gpx", text=gpx_document(heart_rate=900, power=9000)
    )

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.IMPLAUSIBLE_CHANNEL
    assert record.file_sport_index == 0
    assert "%" in (record.detail or ""), "the record says how bad it was"


async def test_a_recording_shorter_than_two_minutes_is_quarantined(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    dropped = drop_in(
        data_root, "short.gpx", text=gpx_document(seconds=range(0, 60, 5))
    )

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.TOO_SHORT


async def test_a_second_export_of_the_same_ride_is_a_suspected_duplicate(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # Different bytes, so the hash check cannot see it: the same ride exported
    # by two platforms. The overlap check is what catches it.
    await pipeline.ingest_file(
        drop_in(data_root, "ride.gpx", text=gpx_document()), actor=ATHLETE
    )
    twin = drop_in(data_root, "ride.tcx", text=tcx_document())

    report = await pipeline.ingest_file(twin, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    assert len(await rows(db_session, SessionRow)) == 1
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.SUSPECTED_DUPLICATE
    [session] = await rows(db_session, SessionRow)
    assert record.suspected_session_id == session.id
    assert "overlaps" in (record.detail or "")


async def test_a_different_ride_on_the_same_day_is_not_a_duplicate(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    await pipeline.ingest_file(
        drop_in(data_root, "morning.gpx", text=gpx_document()), actor=ATHLETE
    )
    afternoon = drop_in(
        data_root,
        "afternoon.gpx",
        text=gpx_document(start=dt.datetime(2026, 6, 1, 16, 0, tzinfo=dt.UTC)),
    )

    report = await pipeline.ingest_file(afternoon, actor=ATHLETE)

    assert report.outcome is IngestOutcome.INGESTED
    assert len(await rows(db_session, SessionRow)) == 2


async def test_a_file_waiting_in_quarantine_is_not_quarantined_twice(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    text = gpx_document(seconds=range(0, 60, 5))
    await pipeline.ingest_file(
        drop_in(data_root, "short.gpx", text=text), actor=ATHLETE
    )
    again = drop_in(data_root, "short-again.gpx", text=text)

    report = await pipeline.ingest_file(again, actor=ATHLETE)

    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    assert len(await rows(db_session, QuarantineRecordRow)) == 1
    assert len(report.quarantine_ids) == 1


async def test_a_failure_mid_pipeline_still_keeps_the_file(
    data_root: Path,
    pipeline: IngestPipeline,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The catch-all. Whatever breaks, the file ends up somewhere the athlete
    # can find it and a record says what happened.
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the disk caught fire")

    monkeypatch.setattr("app.ingest.pipeline.write_streams", explode)
    dropped = drop_in(data_root, "ride.fit", golden("outdoor_ride.fit"))

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.ERROR
    assert "the disk caught fire" in (report.detail or "")
    assert not await rows(db_session, SessionRow), "the transaction rolled back"
    [record] = await rows(db_session, QuarantineRecordRow)
    assert Path(record.quarantined_path).exists()
    [event] = await rows(db_session, IngestEventRow)
    assert event.outcome is IngestOutcome.ERROR


async def test_an_unreadable_extension_is_quarantined_not_ignored(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    dropped = drop_in(data_root, "notes.txt", text="today I rode my bike")

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.UNREADABLE_FILE
    assert "not a file type" in (record.detail or "")


async def test_the_original_of_an_ingested_file_is_never_moved_again(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    await pipeline.ingest_file(
        drop_in(data_root, "brick.fit", golden("brick.fit")), actor=ATHLETE
    )
    [recording, _] = await rows(db_session, RecordingRow)
    original = Path(recording.original_path)

    # Re-reading the original as a reject would: the file stays put and no
    # activity is ingested twice.
    report = await pipeline.ingest_file(original, actor=ATHLETE, reingest=True)

    assert original.exists()
    assert len(await rows(db_session, SessionRow)) == 2
    assert len(report.session_ids) == 2


def test_the_data_tree_puts_originals_under_year_and_month(data_root: Path) -> None:
    paths = IngestPaths.from_settings()
    file_hash = "a" * 64

    filed = paths.original_for(file_hash, "fit", dt.datetime(2026, 5, 4, tzinfo=dt.UTC))

    assert filed == data_root / "originals" / "2026" / "05" / f"{file_hash}.fit"
    assert paths.is_original(filed)
    assert not paths.is_original(paths.quarantine_for(file_hash, "fit"))
    assert paths.streams == data_root / "streams"
    assert stream_path(paths.streams, uuid.uuid7()).suffix == ".parquet"
