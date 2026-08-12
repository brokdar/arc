"""Rebuilding a stored stream from its original, and what that is for.

The invariant under test is the one the whole ``data/originals/`` tree exists
to make true: **an original is enough to rebuild every derived artefact.** The
case that forced it is the odometer — a parser that learns a new channel cannot
reach the sessions already ingested, because recomputing metrics reads the
stored parquet and the column is not in it. So the setup here strips the
odometer out of a freshly written stream, which is exactly what a stream
written before the parser read that channel looks like, and asserts that a
rebuild puts it back and that the distance metric changes its answer as a
result.
"""

import hashlib
import uuid
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.domain.actor import Actor
from app.domain.metrics import Measured, distance_km
from app.domain.streams import StreamChannel
from app.ingest.parquet import read_streams, stream_path
from app.ingest.pipeline import IngestPipeline
from app.ingest.rebuild import (
    REBUILT,
    RebuildOutcome,
    RebuildStatus,
    StreamRebuilder,
    session_ids,
)
from app.persistence.activity import RecordingRow, StreamAnomalyRow
from app.persistence.audit import AuditLogEntry
from scripts import rebuild_streams
from tests.unit.golden_fit import golden

ATHLETE = Actor.athlete()
SYSTEM = Actor.system()


@pytest.fixture
def pipeline(data_root: Path, db_session: AsyncSession) -> IngestPipeline:
    """A pipeline writing into the throwaway data tree."""
    return IngestPipeline.from_session(db_session)


async def ingest_ride(data_root: Path, pipeline: IngestPipeline) -> None:
    """Put the outdoor ride through the real pipeline."""
    path = data_root / "inbox" / "ride.fit"
    path.write_bytes(golden("outdoor_ride.fit").read_bytes())
    await pipeline.ingest_file(path, actor=ATHLETE)


async def only_recording(session: AsyncSession) -> RecordingRow:
    """The one recording the fixture ride created."""
    result = await session.execute(sa.select(RecordingRow))
    [recording] = list(result.scalars())
    return recording


def age_the_stream(path: Path) -> None:
    """Rewrite a stream file as one written before the odometer existed.

    Not a mock of an old file — an actual parquet with the columns removed and
    its metadata otherwise intact, which is byte-for-byte the shape the eleven
    already-ingested sessions have.
    """
    table = pq.read_table(path)
    metadata = table.schema.metadata or {}
    aged = table.drop_columns(
        [
            name
            for name in table.column_names
            if name.startswith(StreamChannel.DISTANCE.value)
        ]
    ).replace_schema_metadata(
        {key: value for key, value in metadata.items() if key != b"source.distance"}
    )
    pq.write_table(aged, path)


async def anomaly_ids(session: AsyncSession) -> set[uuid.UUID]:
    """Every anomaly row's id, for telling a replacement from an append."""
    result = await session.execute(sa.select(StreamAnomalyRow.id))
    return set(result.scalars())


async def test_a_rebuild_restores_a_channel_the_stored_stream_was_written_before(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)
    path = stream_path(data_root / "streams", recording.id)
    age_the_stream(path)
    recording.channels = [
        channel for channel in recording.channels if channel != "distance"
    ]
    await db_session.commit()

    # Before: the column is gone, so the distance is an integration of speed
    # and says so. This is the state recompute alone can never get out of.
    aged = read_streams(path)
    assert StreamChannel.DISTANCE not in aged.fixed
    before = distance_km(aged.fixed[StreamChannel.SPEED], ())
    assert isinstance(before, Measured)
    assert "integrated from the 1 Hz speed channel" in before.explanation.assumptions[0]

    outcome = await StreamRebuilder.from_session(db_session).rebuild(
        recording.id, actor=SYSTEM
    )

    assert outcome.status is RebuildStatus.REBUILT
    assert "gained distance" in outcome.detail
    rebuilt = read_streams(path)
    assert StreamChannel.DISTANCE in rebuilt.fixed
    assert rebuilt.sources[StreamChannel.DISTANCE] == "record.distance"
    after = distance_km(
        rebuilt.fixed[StreamChannel.SPEED], rebuilt.fixed[StreamChannel.DISTANCE]
    )
    assert isinstance(after, Measured)
    assert "odometer" in after.explanation.assumptions[0]
    # And it is a different number, which is the entire point of the exercise.
    assert after.value > before.value * 1.01
    # The row agrees with the file it points at.
    await db_session.refresh(recording)
    assert "distance" in recording.channels
    assert rebuilt.row_count == aged.row_count


async def test_a_rebuild_never_touches_the_original(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # The one file in this system that is not derived from anything. A rebuild
    # reads it and nothing else.
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)
    original = Path(recording.original_path)
    before = hashlib.sha256(original.read_bytes()).hexdigest()

    await StreamRebuilder.from_session(db_session).rebuild(recording.id, actor=SYSTEM)

    assert original.is_file()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert before == recording.file_hash


async def test_a_rebuild_replaces_the_anomalies_rather_than_appending(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # Anomalies describe one stream file, so the rebuilt file's repairs are the
    # whole truth about it. Appending would leave the chart marking regions of
    # a column that no longer exists.
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)
    before = await anomaly_ids(db_session)

    await StreamRebuilder.from_session(db_session).rebuild(recording.id, actor=SYSTEM)

    after = await anomaly_ids(db_session)
    # The same repairs, because the same file was parsed the same way — but
    # freshly written rows, not the old ones with a second set beside them.
    assert len(after) == len(before)
    assert not (after & before)


async def test_a_rebuild_is_audited(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)

    await StreamRebuilder.from_session(db_session).rebuild(recording.id, actor=SYSTEM)

    result = await db_session.execute(
        sa.select(AuditLogEntry).where(AuditLogEntry.action == REBUILT)
    )
    [entry] = list(result.scalars())
    assert entry.entity_id == recording.id
    assert entry.actor == str(SYSTEM)
    assert entry.payload_json["session_id"] == str(recording.session_id)
    assert entry.payload_json["rows"] == 2401


async def test_a_missing_original_is_reported_rather_than_raised(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # A pass over the whole store must not stop at the first file somebody
    # moved by hand: the run's value is in the ones it can do.
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)
    Path(recording.original_path).unlink()

    [outcome] = await StreamRebuilder.from_session(db_session).rebuild_all(actor=SYSTEM)

    assert outcome.status is RebuildStatus.ORIGINAL_MISSING
    assert not outcome.rebuilt
    assert recording.original_path.split("/")[-1] in outcome.detail


async def test_an_unreadable_original_is_reported_rather_than_raised(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    await ingest_ride(data_root, pipeline)
    recording = await only_recording(db_session)
    Path(recording.original_path).write_bytes(b"not a FIT file at all")

    [outcome] = await StreamRebuilder.from_session(db_session).rebuild_all(actor=SYSTEM)

    assert outcome.status is RebuildStatus.UNREADABLE
    assert "could not be parsed" in outcome.detail


async def test_rebuilding_an_unknown_recording_is_a_not_found(
    data_root: Path, db_session: AsyncSession
) -> None:
    with pytest.raises(NotFoundError):
        await StreamRebuilder.from_session(db_session).rebuild(
            uuid.uuid7(), actor=SYSTEM
        )


async def test_a_whole_store_rebuild_does_every_recording_and_commits_each(
    data_root: Path, pipeline: IngestPipeline, db_session: AsyncSession
) -> None:
    # The shape the operator actually runs, and the loop no single-recording
    # test exercises: a pass over the store commits each recording on its own,
    # so a run interrupted at the second one leaves the first rebuilt rather
    # than rolling it back.
    await ingest_ride(data_root, pipeline)
    indoor = data_root / "inbox" / "trainer.fit"
    indoor.write_bytes(golden("indoor_trainer.fit").read_bytes())
    await pipeline.ingest_file(indoor, actor=ATHLETE)
    result = await db_session.execute(sa.select(RecordingRow))
    recordings = list(result.scalars())
    assert len(recordings) == 2
    for recording in recordings:
        age_the_stream(stream_path(data_root / "streams", recording.id))
        recording.channels = [
            channel for channel in recording.channels if channel != "distance"
        ]
    await db_session.commit()

    outcomes = await StreamRebuilder.from_session(db_session).rebuild_all(actor=SYSTEM)

    assert [outcome.status for outcome in outcomes] == [RebuildStatus.REBUILT] * 2
    assert {outcome.recording_id for outcome in outcomes} == {
        recording.id for recording in recordings
    }
    # Two sessions, so two recomputations — not one, and not two of the same.
    assert len(session_ids(outcomes)) == 2
    # Each one's audit row is its own, and each was committed as it was done.
    audited = await db_session.execute(
        sa.select(AuditLogEntry.entity_id).where(AuditLogEntry.action == REBUILT)
    )
    assert set(audited.scalars()) == {recording.id for recording in recordings}
    # Only the ride carried an odometer; the trainer file has none, so exactly
    # one of the two reports gaining the channel back.
    assert sum("gained distance" in outcome.detail for outcome in outcomes) == 1


async def test_the_script_reports_an_unknown_recording_without_a_traceback(
    data_root: Path,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An operator who mistypes a uuid has made a mistake, not found a defect,
    # and a traceback is the wrong way to tell them so.
    exit_code = await rebuild_streams.run(recording_id=uuid.uuid7(), recompute=False)

    captured = capsys.readouterr()
    assert exit_code == rebuild_streams.USAGE_EXIT
    assert captured.err.strip().startswith("error: Recording ")
    assert len(captured.err.strip().splitlines()) == 1
    assert "Traceback" not in captured.err


def test_the_scripts_help_states_the_deploy_then_rebuild_ordering() -> None:
    # The hazard is invisible from the code: a rebuilt parquet carries the new
    # parser's channels, and an older image has no enum member for one it
    # predates, so it reads every rebuilt stream as missing. The operator about
    # to run this is the one who needs the sentence, so it is on --help and not
    # only in a module docstring they did not open.
    help_text = " ".join(rebuild_streams.build_parser().format_help().split())

    assert "Deploy the new image first, then rebuild" in help_text
    assert "never roll the image back after a rebuild" in help_text


def test_the_sessions_to_recompute_are_deduplicated() -> None:
    # A merged session has more than one recording (WP-6.5), and recomputing it
    # once per recording would append two metric versions for one rebuild.
    merged = uuid.uuid7()
    outcomes = [
        RebuildOutcome(uuid.uuid7(), merged, RebuildStatus.REBUILT, ""),
        RebuildOutcome(uuid.uuid7(), merged, RebuildStatus.REBUILT, ""),
        RebuildOutcome(uuid.uuid7(), uuid.uuid7(), RebuildStatus.UNREADABLE, ""),
    ]

    assert session_ids(outcomes) == [merged]
