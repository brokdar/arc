"""The WP-4 tables round-trip, and the two constraints that carry meaning.

The dedup key and the delete rules are the only behavior these tables have
before Phase B wires a pipeline to them, and both are the kind of thing that
is enforced by the database or not at all: a second copy of a ride and an
orphaned anomaly row are exactly what a repository test cannot notice.
"""

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.domain.activity import (
    ClassificationSource,
    IngestOutcome,
    QuarantineReason,
    QuarantineStatus,
    RecordingKind,
    SessionContext,
    SessionDiscipline,
    SessionMatchStatus,
)
from app.domain.anchors import Provenance
from app.domain.streams import AnomalyKind, StreamChannel
from app.persistence.activity import (
    LoggedSetRow,
    RecordingRepository,
    RecordingRow,
    SessionRepository,
    SessionRow,
    StreamAnomalyRow,
)
from app.persistence.db import flush
from app.persistence.ingest_log import (
    IngestEventRepository,
    IngestEventRow,
    QuarantineRecordRow,
    QuarantineRepository,
)

START = dt.datetime(2026, 5, 4, 7, 30, tzinfo=dt.UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def a_session(**overrides: object) -> SessionRow:
    """A device ride, with every required column filled in."""
    values: dict[str, object] = {
        "start_time": START,
        "end_time": START + dt.timedelta(hours=2),
        "timezone": "UTC+02:00",
        "local_date": dt.date(2026, 5, 4),
        "discipline": SessionDiscipline.CYCLING,
        "classification_source": ClassificationSource.SPORT_FIELD,
        "recording_kind": RecordingKind.DEVICE,
    }
    values.update(overrides)
    return SessionRow(**values)


def a_recording(session_id: uuid.UUID, **overrides: object) -> RecordingRow:
    """A recording of that ride, with A4.3/A4.4's metadata filled in."""
    values: dict[str, object] = {
        "session_id": session_id,
        "file_hash": HASH_A,
        "file_sport_index": 0,
        "original_path": "data/originals/2026/05/" + HASH_A + ".fit",
        "original_ext": "fit",
        "sport": "cycling",
        "elapsed_time_s": 7200.0,
        "recording_time_s": 6600.0,
        "recording_stops": [[300, 900]],
        "median_time_delta_s": 1.0,
        "moving_time_s": 6400.0,
        "power_source_candidates": ["crank", "trainer"],
        "power_source": "crank",
        "power_source_rule": "device_info priority",
        "hr_source_candidates": ["strap"],
        "hr_source": "strap",
        "hr_source_rule": "only candidate",
        "channels": [StreamChannel.POWER.value, StreamChannel.HR.value],
    }
    values.update(overrides)
    return RecordingRow(**values)


async def test_a_session_and_its_recording_round_trip(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    recordings = RecordingRepository(db_session)

    session = await sessions.add(a_session())
    recording_id = (await recordings.add(a_recording(session.id))).id
    session_id = session.id
    db_session.expire_all()

    stored = await sessions.get(session_id)
    assert stored is not None
    assert stored.start_time == START, "aware UTC on SQLite as well as Postgres"
    assert stored.local_date == dt.date(2026, 5, 4)
    assert stored.discipline is SessionDiscipline.CYCLING
    assert stored.duration_s == 7200.0
    # Defaults that make the reserved columns readable without a migration.
    assert stored.status is SessionMatchStatus.UNMATCHED
    assert stored.session_context is SessionContext.TRAINING
    assert stored.discipline_overridden is False
    assert stored.weight_kg is None
    assert stored.weight_provenance is None

    [stored_recording] = stored.recordings
    assert stored_recording.id == recording_id
    assert stored_recording.recording_stops == [[300, 900]]
    assert stored_recording.power_source_candidates == ["crank", "trainer"]
    assert stored_recording.external_id is None
    assert stored_recording.source is None


async def test_the_reserved_weight_columns_accept_an_anchor_provenance(
    db_session: AsyncSession,
) -> None:
    # R3 stores the weight of the day with the provenance of an anchor, so the
    # column has to speak the same vocabulary anchors do.
    sessions = SessionRepository(db_session)

    stored = await sessions.add(
        a_session(weight_kg=72.5, weight_provenance=Provenance.ATHLETE_REPORTED)
    )

    assert stored.weight_provenance is Provenance.ATHLETE_REPORTED


async def test_the_dedup_key_is_the_hash_and_the_sport_index(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    recordings = RecordingRepository(db_session)
    first = await sessions.add(a_session())
    second = await sessions.add(a_session(start_time=START + dt.timedelta(days=1)))
    await recordings.add(a_recording(first.id))

    # A second sport within the same file is a different recording (A4.5).
    other_sport = await recordings.add(a_recording(second.id, file_sport_index=1))
    assert other_sport.file_sport_index == 1

    with pytest.raises(ConflictError):
        await recordings.add(a_recording(second.id))


async def test_a_known_dedup_key_is_found_before_anything_is_parsed(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    recordings = RecordingRepository(db_session)
    session = await sessions.add(a_session())
    await recordings.add(a_recording(session.id))

    assert await recordings.by_dedup_key(HASH_A, 0) is not None
    assert await recordings.by_dedup_key(HASH_A, 1) is None
    assert await recordings.by_dedup_key(HASH_B, 0) is None


async def test_anomalies_are_counted_and_die_with_their_recording(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    recordings = RecordingRepository(db_session)
    session = await sessions.add(a_session())
    recording = await recordings.add(a_recording(session.id))

    await recordings.add_anomalies(
        [
            StreamAnomalyRow(
                recording_id=recording.id,
                channel=StreamChannel.POWER,
                start_index=30,
                end_index=32,
                kind=AnomalyKind.SPIKE_CLIPPED,
                substituted_value=201.0,
            ),
            StreamAnomalyRow(
                recording_id=recording.id,
                channel=StreamChannel.ELEVATION,
                start_index=100,
                end_index=140,
                kind=AnomalyKind.GAP_INTERPOLATED,
            ),
        ]
    )
    assert await recordings.anomaly_count(recording.id) == 2

    # The cascade is the database's, so it is proved by a statement that goes
    # around the ORM's unit of work entirely.
    await db_session.execute(sa.delete(SessionRow).where(SessionRow.id == session.id))
    remaining = await db_session.scalar(
        sa.select(sa.func.count()).select_from(StreamAnomalyRow)
    )
    assert remaining == 0
    assert (
        await db_session.scalar(sa.select(sa.func.count()).select_from(RecordingRow))
        == 0
    )


async def test_logged_sets_belong_to_their_session_and_are_ordered(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    session = await sessions.add(
        a_session(
            discipline=SessionDiscipline.STRENGTH,
            classification_source=ClassificationSource.HEURISTIC,
            recording_kind=RecordingKind.MANUAL,
            rpe=7.0,
        )
    )
    db_session.add_all(
        [
            LoggedSetRow(
                session_id=session.id,
                exercise_id=None,
                exercise_name="Trap bar deadlift",
                set_index=index,
                reps=5,
                load_kg=100.0 + index * 5,
                rir=2,
            )
            for index in (2, 0, 1)
        ]
    )
    await db_session.flush()
    session_id = session.id
    db_session.expire_all()

    stored = await sessions.get(session_id)
    assert stored is not None
    assert [row.set_index for row in stored.logged_sets] == [0, 1, 2]
    assert stored.rpe == 7.0

    # Two sets claiming the same position in one session is a form submitted
    # twice, not two sets; the constraint is what says so.
    db_session.add(
        LoggedSetRow(
            session_id=session_id,
            exercise_name="Trap bar deadlift",
            set_index=0,
            reps=5,
        )
    )
    with pytest.raises(ConflictError):
        await flush(db_session)


async def test_sessions_list_newest_first_and_can_be_bounded_by_date(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    for day in (1, 3, 5):
        await sessions.add(
            a_session(
                start_time=START + dt.timedelta(days=day),
                local_date=dt.date(2026, 5, 4 + day),
            )
        )

    rows, total = await sessions.list()
    assert total == 3
    assert [row.local_date.day for row in rows] == [9, 7, 5]

    bounded, total = await sessions.list(
        start=dt.date(2026, 5, 6), end=dt.date(2026, 5, 8)
    )
    assert total == 1
    assert [row.local_date.day for row in bounded] == [7]

    none_of_them, total = await sessions.list(discipline=SessionDiscipline.STRENGTH)
    assert (list(none_of_them), total) == ([], 0)


async def test_overlapping_finds_the_candidates_for_the_duplicate_check(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    existing = await sessions.add(a_session())
    await sessions.add(
        a_session(
            start_time=START + dt.timedelta(days=1),
            end_time=START + dt.timedelta(days=1, hours=2),
            local_date=dt.date(2026, 5, 5),
        )
    )

    overlapping = await sessions.overlapping(
        START + dt.timedelta(hours=1), START + dt.timedelta(hours=3)
    )

    assert [row.id for row in overlapping] == [existing.id]


async def test_quarantine_lists_pending_first_and_survives_its_suspect(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    quarantine = QuarantineRepository(db_session)
    suspect = await sessions.add(a_session())

    await quarantine.add(
        QuarantineRecordRow(
            original_filename="2026-05-01-ride.fit",
            file_hash=HASH_B,
            reason=QuarantineReason.UNREADABLE_FILE,
            detail="the FIT header did not decode",
            quarantined_path="data/quarantine/" + HASH_B + ".fit",
            status=QuarantineStatus.CONFIRMED_DISCARDED,
            resolved_at=START,
        )
    )
    pending = await quarantine.add(
        QuarantineRecordRow(
            original_filename="2026-05-04-ride.fit",
            file_hash=HASH_A,
            file_sport_index=0,
            reason=QuarantineReason.SUSPECTED_DUPLICATE,
            detail="overlaps an existing session by 94 %",
            quarantined_path="data/quarantine/" + HASH_A + ".fit",
            suspected_session_id=suspect.id,
        )
    )

    rows, total = await quarantine.list()
    assert total == 2
    assert rows[0].id == pending.id, "the queue leads with what is still waiting"
    assert rows[0].status is QuarantineStatus.PENDING
    assert await quarantine.pending_for_hash(HASH_A) is not None
    assert await quarantine.pending_for_hash(HASH_B) is None

    pending_id = pending.id
    await db_session.execute(sa.delete(SessionRow).where(SessionRow.id == suspect.id))
    db_session.expire_all()
    stored = await quarantine.get(pending_id)
    assert stored is not None
    assert stored.suspected_session_id is None, "the record still explains itself"


async def test_the_ingest_log_records_every_outcome_newest_first(
    db_session: AsyncSession,
) -> None:
    sessions = SessionRepository(db_session)
    events = IngestEventRepository(db_session)
    session = await sessions.add(a_session())

    await events.record(
        filename="ride.fit",
        file_hash=HASH_A,
        outcome=IngestOutcome.INGESTED,
        session_id=session.id,
    )
    await events.record(
        filename="ride.fit",
        file_hash=HASH_A,
        outcome=IngestOutcome.DUPLICATE_FILE,
        detail="already ingested as recording 0 of this file",
    )

    rows, total = await events.list()
    assert total == 2
    assert rows[0].outcome is IngestOutcome.DUPLICATE_FILE
    assert rows[0].at.tzinfo is not None

    await db_session.execute(sa.delete(SessionRow).where(SessionRow.id == session.id))
    db_session.expire_all()
    rows, total = await events.list()
    assert total == 2, "the log outlives what it describes"
    assert all(row.session_id is None for row in rows)


async def test_an_event_can_precede_hashing(db_session: AsyncSession) -> None:
    events = IngestEventRepository(db_session)

    row = await events.record(
        filename="corrupt.fit",
        file_hash=None,
        outcome=IngestOutcome.ERROR,
        detail="the file could not be read",
    )

    assert isinstance(row, IngestEventRow)
    assert row.file_hash is None
