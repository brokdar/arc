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
    MAX_STRENGTH_HEURISTIC_S,
    ClassificationSource,
    IngestOutcome,
    QuarantineReason,
    QuarantineStatus,
    RecordingKind,
    SessionDiscipline,
)
from app.domain.actor import Actor
from app.domain.streams import ParsedActivity, RawSample, StreamChannel
from app.ingest.parquet import read_streams, stream_path
from app.ingest.pipeline import IngestPaths, IngestPipeline, _prepare
from app.persistence.activity import (
    RecordingRepository,
    RecordingRow,
    SessionRepository,
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
    # A4.4 on the row, not just in memory: what a reader derives by
    # subtracting is exactly the row ranges the same row reports.
    assert recording.recording_stops == [[601, 1200]]
    paused = recording.elapsed_time_s - recording.recording_time_s
    assert paused == sum(end - start for start, end in recording.recording_stops)
    # One second shorter than the raw gap the golden file was built with: the
    # samples either side of a stop were recorded, so their rows are not paused.
    assert paused == OUTDOOR_STOP_S - 1
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
    # And it says so: nothing was created, so nothing was "ingested".
    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    assert "already ingested" in (report.detail or "")
    assert "session(s) ingested" not in (report.detail or "")


async def test_a_crash_before_the_commit_leaves_the_file_for_the_next_sweep(
    data_root: Path,
    pipeline: IngestPipeline,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The file is copied to `originals/` before its rows are committed. A
    # process that dies in that window must leave the inbox copy behind, or the
    # bytes are in a tree nothing has a row pointing at — invisible for ever.
    class Killed(BaseException):
        """A process death: past every `except Exception` in the pipeline."""

    async def die(*_args: object, **_kwargs: object) -> None:
        raise Killed

    monkeypatch.setattr("app.ingest.pipeline.commit", die)
    dropped = drop_in(data_root, "ride.fit", golden("outdoor_ride.fit"))

    with pytest.raises(Killed):
        await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert dropped.exists(), "the inbox copy outlives an uncommitted run"
    filed = list((data_root / "originals").glob("**/*.fit"))
    assert filed, "the copy is made first; it is the commit that did not happen"

    # A restart: the transaction is gone (nothing was committed), the inbox is
    # not, and the next sweep converges on the file already at the destination.
    monkeypatch.undo()
    await db_session.rollback()

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.INGESTED
    assert len(await rows(db_session, SessionRow)) == 1
    assert not dropped.exists(), "and the inbox is cleaned, after the commit"
    assert list((data_root / "originals").glob("**/*.fit")) == filed


async def test_a_duplicate_whose_original_is_missing_restores_it(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # The dedup check answers from a row. If the file that row names is gone,
    # discarding the new arrival would delete the last copy of the bytes.
    await pipeline.ingest_file(
        drop_in(data_root, "ride.fit", golden("outdoor_ride.fit")), actor=ATHLETE
    )
    [recording] = await rows(db_session, RecordingRow)
    original = Path(recording.original_path)
    original.unlink()
    again = drop_in(data_root, "ride-again.fit", golden("outdoor_ride.fit"))

    report = await pipeline.ingest_file(again, actor=ATHLETE)

    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    assert original.is_file(), "the recorded copy is restored from the arrival"
    assert original.read_bytes() == golden("outdoor_ride.fit").read_bytes()
    assert not again.exists()
    assert len(await rows(db_session, SessionRow)) == 1


async def test_a_duplicate_whose_quarantined_copy_is_missing_restores_it(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    text = gpx_document(seconds=range(0, 60, 5))
    await pipeline.ingest_file(
        drop_in(data_root, "short.gpx", text=text), actor=ATHLETE
    )
    [record] = await rows(db_session, QuarantineRecordRow)
    kept = Path(record.quarantined_path)
    kept.unlink()
    again = drop_in(data_root, "short-again.gpx", text=text)

    report = await pipeline.ingest_file(again, actor=ATHLETE)

    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    assert kept.is_file(), "the pending record has its file back"
    assert kept.read_text() == text
    assert len(await rows(db_session, QuarantineRecordRow)) == 1


async def test_a_symlinked_drop_becomes_real_bytes_and_the_link_is_removed(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # A link would otherwise be filed as an "original" that points somewhere
    # else — and, because resolving it leaves the inbox, never be cleaned up.
    target = data_root.parent / "athlete-archive" / "ride.fit"
    target.parent.mkdir(parents=True)
    target.write_bytes(golden("outdoor_ride.fit").read_bytes())
    link = data_root / "inbox" / "ride.fit"
    link.symlink_to(target)

    report = await pipeline.ingest_file(link, actor=ATHLETE)

    assert report.outcome is IngestOutcome.INGESTED
    assert not link.is_symlink(), "the link left the inbox"
    assert not list((data_root / "inbox").iterdir())
    [recording] = await rows(db_session, RecordingRow)
    original = Path(recording.original_path)
    assert original.is_file()
    assert not original.is_symlink()
    assert original.read_bytes() == target.read_bytes()
    assert target.is_file(), "the athlete's own copy is untouched"


async def test_losing_the_dedup_race_is_a_duplicate_not_a_quarantine_record(
    data_root: Path,
    pipeline: IngestPipeline,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two ingests of one file, interleaved: the loser's dedup reads happened
    # before the winner committed, so the unique constraint is what catches it.
    text = gpx_document()
    first = await pipeline.ingest_file(
        drop_in(data_root, "ride.gpx", text=text), actor=ATHLETE
    )
    by_hash = RecordingRepository.by_hash
    blinded = {"calls": 0}

    async def blind_once(self: RecordingRepository, file_hash: str) -> object:
        blinded["calls"] += 1
        # Blind only the pre-check; the recovery read sees the winner, which
        # is the state of the database by the time the constraint fires.
        return [] if blinded["calls"] == 1 else await by_hash(self, file_hash)

    async def no_dedup(self: object, *_args: object) -> None:
        return None

    async def no_overlap(self: object, *_args: object) -> list[object]:
        return []

    monkeypatch.setattr(RecordingRepository, "by_hash", blind_once)
    monkeypatch.setattr(RecordingRepository, "by_dedup_key", no_dedup)
    monkeypatch.setattr(SessionRepository, "overlapping", no_overlap)
    again = drop_in(data_root, "ride-again.gpx", text=text)

    report = await pipeline.ingest_file(again, actor=ATHLETE)

    assert report.outcome is IngestOutcome.DUPLICATE_FILE
    assert report.session_ids == first.session_ids
    assert not await rows(db_session, QuarantineRecordRow), "no phantom queue entry"
    assert len(await rows(db_session, SessionRow)) == 1
    assert not again.exists()


async def test_a_failed_stream_write_leaves_nothing_at_the_final_path(
    data_root: Path,
    pipeline: IngestPipeline,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A truncated parquet file at the path a recording names is not a shorter
    # ride, it is a permanent read error — so the write is staged and renamed.
    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("app.ingest.parquet.pq.write_table", explode)

    report = await pipeline.ingest_file(
        drop_in(data_root, "ride.fit", golden("outdoor_ride.fit")), actor=ATHLETE
    )

    assert report.outcome is IngestOutcome.ERROR
    assert list((data_root / "streams").iterdir()) == [], (
        "neither a half-written frame nor the staging file it was written to"
    )


async def test_a_file_whose_extension_is_a_payload_is_filed_under_a_usable_name(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # `<64 hex>.<202 chars>` is longer than any filesystem accepts a name to
    # be, and the ENAMETOOLONG it raises used to escape the sweep entirely.
    dropped = drop_in(data_root, f"ride.{'f' * 202}", text="not a ride at all")

    report = await pipeline.ingest_file(dropped, actor=ATHLETE)

    assert report.outcome is IngestOutcome.QUARANTINED
    [record] = await rows(db_session, QuarantineRecordRow)
    kept = Path(record.quarantined_path)
    assert kept.is_file()
    assert kept.suffix == ".bin"
    assert len(kept.name) <= 255
    assert record.reason is QuarantineReason.UNREADABLE_FILE


async def test_a_ride_overlapping_a_gym_session_is_not_a_suspected_duplicate(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # The athlete lifted and then rode, and the two recordings overlap. A ride
    # cannot be a second copy of a strength session, whatever the clock says.
    await pipeline.ingest_file(
        drop_in(data_root, "gym.gpx", text=gpx_document(sport="strength_training")),
        actor=ATHLETE,
    )
    ride = drop_in(data_root, "ride.tcx", text=tcx_document())

    report = await pipeline.ingest_file(ride, actor=ATHLETE)

    assert report.outcome is IngestOutcome.INGESTED
    assert not await rows(db_session, QuarantineRecordRow)
    assert {row.discipline for row in await rows(db_session, SessionRow)} == {
        SessionDiscipline.STRENGTH,
        SessionDiscipline.CYCLING,
    }


def test_the_discipline_is_classified_from_recording_time_not_elapsed() -> None:
    # An hour in the gym with a forty-minute break between blocks: 100 minutes
    # on the clock, which is longer than any strength session the heuristic
    # believes in — and 60 minutes of recording, which is exactly one.
    start = dt.datetime(2026, 6, 1, 17, 0, tzinfo=dt.UTC)
    seconds = list(range(0, 1801, 5)) + list(range(4200, 6001, 5))
    activity = ParsedActivity(
        file_sport_index=0,
        sport=None,
        start_time=start,
        local_offset=None,
        samples=[
            RawSample(t=start + dt.timedelta(seconds=s), values={StreamChannel.HR: 128})
            for s in seconds
        ],
    )

    prepared = _prepare(activity)

    assert prepared.resampled.elapsed_time_s > MAX_STRENGTH_HEURISTIC_S
    assert prepared.resampled.recording_time_s < MAX_STRENGTH_HEURISTIC_S
    assert prepared.discipline is SessionDiscipline.STRENGTH
    assert prepared.classification is ClassificationSource.HEURISTIC


async def test_waiving_the_implausible_channel_ingests_it_cleaned_to_null(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # What B-4's reject of an `implausible_channel` verdict does: the ride is
    # ingested, and the channel nobody can believe is null in the fixed column
    # rather than an average of nonsense.
    dropped = drop_in(data_root, "absurd.gpx", text=gpx_document(power=9000))

    report = await pipeline.ingest_file(dropped, actor=ATHLETE, waive_implausible=True)

    assert report.outcome is IngestOutcome.INGESTED
    assert not await rows(db_session, QuarantineRecordRow)
    [recording] = await rows(db_session, RecordingRow)
    stored = read_streams(stream_path(data_root / "streams", recording.id))
    assert set(stored.raw[StreamChannel.POWER]) == {9000.0}, "the raw column is kept"
    assert all(value is None for value in stored.fixed[StreamChannel.POWER])
    dropped_rows = [
        row
        for row in await rows(db_session, StreamAnomalyRow)
        if row.kind.value == "dropped"
    ]
    assert dropped_rows, "and every substitution is recorded"
    assert {row.channel for row in dropped_rows} == {StreamChannel.POWER}


async def test_waiving_the_implausible_channel_waives_nothing_else(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    dropped = drop_in(
        data_root, "short.gpx", text=gpx_document(seconds=range(0, 60, 5))
    )

    report = await pipeline.ingest_file(dropped, actor=ATHLETE, waive_implausible=True)

    assert report.outcome is IngestOutcome.QUARANTINED
    [record] = await rows(db_session, QuarantineRecordRow)
    assert record.reason is QuarantineReason.TOO_SHORT


def test_the_data_tree_puts_originals_under_year_and_month(data_root: Path) -> None:
    paths = IngestPaths.from_settings()
    file_hash = "a" * 64

    filed = paths.original_for(file_hash, "fit", dt.datetime(2026, 5, 4, tzinfo=dt.UTC))

    assert filed == data_root / "originals" / "2026" / "05" / f"{file_hash}.fit"
    assert paths.is_original(filed)
    assert not paths.is_original(paths.quarantine_for(file_hash, "fit"))
    assert paths.streams == data_root / "streams"
    assert stream_path(paths.streams, uuid.uuid7()).suffix == ".parquet"
