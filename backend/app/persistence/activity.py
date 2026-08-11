"""Completed sessions, the device recordings behind them, and their repairs.

Four tables. The split between the first two is build-plan WP-4.2 and it is
the one shape decision worth restating: a **session** is the real-world event
(a ride happened, on this day, in this timezone) and a **recording** is one
device's account of it. The MVP writes exactly one recording per session, but
the schema permits N because the case that breaks a one-to-one column — a
head unit stopped at the garage door and restarted, two files for one ride —
is WP-6's, and re-shaping the ingest pipeline once files exist is the expensive
kind of change (A4.5).

Do not confuse `SessionRow` with `PlannedSessionRow` in
`app.persistence.planned_sessions`: this one is what happened, that one is
what was asked for, and what joins them is `session_matches`
(`app.persistence.matching`) — a link table, never a column on either side.
See `app.domain.activity` for the vocabulary both use.

`stream_anomalies` is A4.2's audit trail one level below the audit log: a
repaired sample is a derived value, so every substituted region says what was
done to it. The samples themselves are not here — they go to
``data/streams/<recording_id>.parquet`` on the 1 Hz grid (A4.1), because a
four-hour ride is 14 400 rows per channel and a row per sample per channel is
a table nothing would ever query by row.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.activity import (
    ClassificationSource,
    RecordingKind,
    SessionContext,
    SessionDiscipline,
    SessionMatchStatus,
)
from app.domain.anchors import Provenance
from app.domain.streams import AnomalyKind, StreamChannel
from app.persistence.db import Base, flush
from app.persistence.exercises import MAX_SLUG_LENGTH
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest free-text note on a session or a logged set.
MAX_NOTES_LENGTH = 4_000

#: Longest a stored timezone string may be — IANA names top out well below
#: this, and the fixed-offset form is nine characters.
MAX_TIMEZONE_LENGTH = 64

#: sha256, hex.
FILE_HASH_LENGTH = 64

#: Longest stored filesystem path.
MAX_PATH_LENGTH = 1_024

#: Longest a channel-source label or the rule that chose it may be (A4.3).
MAX_SOURCE_LENGTH = 200


class SessionRow(Base):
    """One completed session: what the athlete actually did."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: When it started, aware UTC. Indexed because the WP-6 candidate window
    #: and the overlap-duplicate check (B-2) both query on it.
    start_time: Mapped[dt.datetime] = mapped_column(UtcDateTime, index=True)
    end_time: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    #: The athlete-local timezone at the start, best-effort from the file:
    #: an IANA name when the source gives one, else ``UTC+02:00``, else
    #: ``UTC``. Athlete-overridable, and the override re-derives `local_date`.
    timezone: Mapped[str] = mapped_column(String(MAX_TIMEZONE_LENGTH), default="UTC")
    #: The day the session belongs to — `app.domain.activity.session_date` of
    #: `start_time` in `timezone`, so a midnight-crosser belongs to the day it
    #: began. Stored rather than derived on read: it is what the session list
    #: pages by and what WP-6 matches on.
    local_date: Mapped[dt.date] = mapped_column(Date, index=True)
    discipline: Mapped[SessionDiscipline] = mapped_column(
        enum_column(SessionDiscipline), index=True
    )
    #: Whether `discipline` came from the file's own sport field or a guess.
    classification_source: Mapped[ClassificationSource] = mapped_column(
        enum_column(ClassificationSource)
    )
    #: Set when the athlete corrected the discipline, so a correction is never
    #: silently re-guessed by a later re-classification.
    discipline_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Where this session stands relative to the plan. WP-6 owns the
    #: lifecycle: `app.services.matching` is the only writer, and the link
    #: itself lives in `session_matches`, not here — this column is the
    #: denormalised answer the session list renders a badge from.
    status: Mapped[SessionMatchStatus] = mapped_column(
        enum_column(SessionMatchStatus),
        default=SessionMatchStatus.UNMATCHED,
        index=True,
    )
    recording_kind: Mapped[RecordingKind] = mapped_column(
        enum_column(RecordingKind), index=True
    )
    #: Session RPE, 0-10, for manual strength entry (B-6). Null for a device
    #: session unless the athlete adds one.
    rpe: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES_LENGTH))

    #: Reserved (R3), no behavior: bodyweight **at the time**, pinned like an
    #: anchor, because every w/kg, VO2max and power-profile number the MMP adds
    #: depends on the weight of the day rather than today's. Nothing writes
    #: these yet.
    weight_kg: Mapped[float | None] = mapped_column(Float)
    weight_provenance: Mapped[Provenance | None] = mapped_column(
        enum_column(Provenance)
    )
    #: Reserved (R5), no behavior: WP-6 switches its scoring rubric on this.
    #: Only `training` is ever produced in the MVP.
    session_context: Mapped[SessionContext] = mapped_column(
        enum_column(SessionContext), default=SessionContext.TRAINING
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )

    recordings: Mapped[list[RecordingRow]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
    )
    logged_sets: Mapped[list[LoggedSetRow]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
        order_by="LoggedSetRow.set_index",
    )

    @property
    def duration_s(self) -> float:
        """Wall-clock length of the session.

        Not the load duration: that is the recording's `recording_time_s`,
        which has the stops taken out of it (A4.4, A5.1).
        """
        return (self.end_time - self.start_time).total_seconds()


class RecordingRow(Base):
    """One device file's account of one session (one sport within one file)."""

    __tablename__ = "recordings"
    __table_args__ = (
        # The dedup key (A4.5). The hash alone is not enough: a multisport
        # file has one hash and several activities, and ingesting the second
        # one must not look like re-ingesting the first.
        UniqueConstraint("file_hash", "file_sport_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint below leads on `file_hash`, and
    #: this one is covered by the relationship's own index.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    #: sha256 of the original file, hex.
    file_hash: Mapped[str] = mapped_column(String(FILE_HASH_LENGTH))
    #: Ordinal of this sport within the file, from 0 (A4.5).
    file_sport_index: Mapped[int] = mapped_column(Integer, default=0)
    #: Where the immutable original was filed
    #: (``data/originals/YYYY/MM/<hash>.<ext>``). Never deleted, never
    #: rewritten — it is what a re-ingest would read.
    original_path: Mapped[str] = mapped_column(String(MAX_PATH_LENGTH))
    original_ext: Mapped[str] = mapped_column(String(16))
    #: The raw sport string the file carried, kept so the discipline
    #: classification can be re-read without re-parsing the original.
    sport: Mapped[str | None] = mapped_column(String(80))

    #: A4.4's four numbers, all impossible to reconstruct from the resampled
    #: frame afterwards.
    elapsed_time_s: Mapped[float] = mapped_column(Float)
    #: Elapsed minus every gap over 30 s. **The duration term in load** (A5.1).
    recording_time_s: Mapped[float] = mapped_column(Float)
    #: The `[start_index, end_index)` row ranges subtracted above.
    recording_stops: Mapped[list[list[int]]] = mapped_column(JSONColumn, default=list)
    median_time_delta_s: Mapped[float] = mapped_column(Float)
    #: Display only — never a load input.
    moving_time_s: Mapped[float] = mapped_column(Float)

    #: A4.3: which meter produced the numbers, and why that one. A file with a
    #: crank meter and a smart trainer carries two power traces that can differ
    #: by 15 %, and choosing silently makes the number unexplainable.
    power_source_candidates: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    power_source: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    power_source_rule: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    hr_source_candidates: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    hr_source: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    hr_source_rule: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))

    #: `StreamChannel` values actually present in the parquet frame.
    channels: Mapped[list[str]] = mapped_column(JSONColumn, default=list)

    #: Reserved (R4), no behavior: the vendor's own id and which integration
    #: it came from, for the MMP's adapters and for the "a richer file
    #: supersedes a lower-fidelity import of the same ride" merge case.
    external_id: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(60))

    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )

    # No relationship to the anomaly rows: a recording's repairs are counted
    # far more often than they are listed, and an eager load would drag every
    # anomaly of every recording behind an ordinary session read. The foreign
    # key's ON DELETE CASCADE is what removes them.


class StreamAnomalyRow(Base):
    """One repaired region of one channel of one recording (A4.2)."""

    __tablename__ = "stream_anomalies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[StreamChannel] = mapped_column(enum_column(StreamChannel))
    #: `[start_index, end_index)` on the 1 Hz grid — the same addressing every
    #: other range in this system uses (A4.1).
    start_index: Mapped[int] = mapped_column(Integer)
    end_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[AnomalyKind] = mapped_column(enum_column(AnomalyKind))
    #: The value substituted, where one value was; null for an interpolated or
    #: dropped region, which has no single number to name.
    substituted_value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class LoggedSetRow(Base):
    """One set the athlete logged by hand (manual strength entry, B-6)."""

    __tablename__ = "logged_sets"
    __table_args__ = (UniqueConstraint("session_id", "set_index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: The catalogue movement, when it is one. Nulled rather than cascaded if
    #: that entry is ever removed — `exercise_name` is what keeps the row
    #: readable, which is the same reason it exists for free-text entries.
    exercise_id: Mapped[str | None] = mapped_column(
        String(MAX_SLUG_LENGTH),
        ForeignKey("exercises.id", ondelete="SET NULL"),
        index=True,
    )
    #: What the athlete called the movement. Always written; for a catalogue
    #: exercise it is the catalogue name at the time of logging.
    exercise_name: Mapped[str] = mapped_column(String(160))
    #: 0-based position within the session.
    set_index: Mapped[int] = mapped_column(Integer)
    reps: Mapped[int] = mapped_column(Integer)
    load_kg: Mapped[float | None] = mapped_column(Float)
    #: Reps in reserve, as reported after the set.
    rir: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


def session_duration_s(row: SessionRow) -> float:
    """How long a completed session lasted, in the sense every reader means.

    **Recording time** for a device session — elapsed with the pauses taken out
    (A4.4) — because that is what training load is computed over (A5.1) and
    what the athlete would call the ride. Summed across recordings, so a merged
    session (WP-6.5) reports both files. Wall-clock for a typed-in one, which
    has no recording and therefore no pauses to subtract.

    One function because four callers need the same answer — the session list,
    the session detail, the week rail and WP-6's duration term — and a matcher
    that compared elapsed time against a prescription while the page showed
    recording time would disagree with the screen the athlete is looking at.

    Here rather than in a service because `app.services.matching` needs it and
    `app.services.activity` needs *that*: one of the two had to stop being the
    home of a function that only reads a row.
    """
    if not row.recordings:
        return row.duration_s
    return sum(recording.recording_time_s for recording in row.recordings)


class SessionRepository:
    """SQLAlchemy repository for completed sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: uuid.UUID) -> SessionRow | None:
        """Return one completed session with its recordings, or None."""
        return await self._session.get(SessionRow, session_id)

    async def list(
        self,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        discipline: SessionDiscipline | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionRow], int]:
        """Return a page of sessions, newest first, plus the total.

        Newest first because this is a log of what has happened, unlike the
        planned-session list, which is a calendar and reads forwards.
        ``start`` and ``end`` bound `local_date` inclusively.
        """
        criteria: list[Any] = []
        if start is not None:
            criteria.append(SessionRow.local_date >= start)
        if end is not None:
            criteria.append(SessionRow.local_date <= end)
        if discipline is not None:
            criteria.append(SessionRow.discipline == discipline)
        total = await self._session.scalar(
            select(func.count()).select_from(SessionRow).where(*criteria)
        )
        result = await self._session.execute(
            select(SessionRow)
            .where(*criteria)
            .order_by(SessionRow.local_date.desc(), SessionRow.start_time.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars()), total or 0

    async def by_ids(
        self, session_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, SessionRow]:
        """Several sessions by id, in one query.

        For the pages that join *onto* sessions rather than list them — WP-6's
        proposal inbox is a page of links, and each row names the session it
        is about.
        """
        if not session_ids:
            return {}
        result = await self._session.execute(
            select(SessionRow).where(SessionRow.id.in_(session_ids))
        )
        return {row.id: row for row in result.scalars()}

    async def overlapping(
        self, start: dt.datetime, end: dt.datetime
    ) -> Sequence[SessionRow]:
        """Sessions whose time range intersects ``[start, end]``.

        The candidate set for B-2's overlap duplicate check; the >70 % rule
        itself is the pipeline's, not the database's.
        """
        result = await self._session.execute(
            select(SessionRow)
            .where(SessionRow.start_time <= end, SessionRow.end_time >= start)
            .order_by(SessionRow.start_time.asc())
        )
        return list(result.scalars())

    async def add(self, row: SessionRow) -> SessionRow:
        """Persist a session (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row


class RecordingRepository:
    """SQLAlchemy repository for recordings and their anomalies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, recording_id: uuid.UUID) -> RecordingRow | None:
        """Return one recording, or None."""
        return await self._session.get(RecordingRow, recording_id)

    async def by_dedup_key(
        self, file_hash: str, file_sport_index: int
    ) -> RecordingRow | None:
        """Return the recording already ingested for this file+sport, if any.

        The idempotency check the pipeline runs before it parses anything:
        re-seeing a known key is a `duplicate_file` event, not an error.
        """
        result = await self._session.execute(
            select(RecordingRow).where(
                RecordingRow.file_hash == file_hash,
                RecordingRow.file_sport_index == file_sport_index,
            )
        )
        return result.scalar_one_or_none()

    async def by_hash(self, file_hash: str) -> Sequence[RecordingRow]:
        """Every recording already ingested from one file, in sport order.

        The file-level half of the pipeline's dedup check: a hash that is
        known at all must not be parsed again, whatever its sport count.
        Ordered so the log line naming the twin is deterministic.
        """
        result = await self._session.execute(
            select(RecordingRow)
            .where(RecordingRow.file_hash == file_hash)
            .order_by(RecordingRow.file_sport_index.asc())
        )
        return list(result.scalars())

    async def all(self) -> Sequence[RecordingRow]:
        """Every recording there is, oldest first.

        For the maintenance paths that walk the whole store — rebuilding the
        stream files from the originals, above all — where "every recording"
        is the unit of work and there is no session to start from.
        """
        result = await self._session.execute(
            select(RecordingRow).order_by(RecordingRow.created_at.asc())
        )
        return list(result.scalars())

    async def add(self, row: RecordingRow) -> RecordingRow:
        """Persist a recording and refresh generated fields.

        Raises:
            ConflictError: When the dedup key is already taken.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row

    async def add_anomalies(self, rows: Sequence[StreamAnomalyRow]) -> None:
        """Append anomaly rows for a recording."""
        self._session.add_all(rows)
        await flush(self._session)

    async def replace_anomalies(
        self, recording_id: uuid.UUID, rows: Sequence[StreamAnomalyRow]
    ) -> None:
        """Swap one recording's anomalies for a freshly derived set.

        Delete-then-insert rather than a merge, because anomalies are not
        records of *events* — they are a derived description of one stream
        file, and a rebuilt file's repairs are the whole truth about it.
        Keeping the old rows beside the new ones would leave the chart marking
        regions of a column that no longer exists.
        """
        await self._session.execute(
            delete(StreamAnomalyRow).where(
                StreamAnomalyRow.recording_id == recording_id
            )
        )
        self._session.add_all(rows)
        await flush(self._session)

    async def anomalies(self, recording_id: uuid.UUID) -> Sequence[StreamAnomalyRow]:
        """Every repaired region of one recording, in row order.

        Includes the `resampled_only` certificates: a reader that wants only
        the repairs filters them out, and a reader that wants to know the
        cleaner ran at all needs them. The chart marks the repairs (A4.2's
        done-when), so it filters.
        """
        result = await self._session.execute(
            select(StreamAnomalyRow)
            .where(StreamAnomalyRow.recording_id == recording_id)
            .order_by(
                StreamAnomalyRow.start_index.asc(), StreamAnomalyRow.end_index.asc()
            )
        )
        return list(result.scalars())

    async def anomaly_count(self, recording_id: uuid.UUID) -> int:
        """How many anomaly rows this recording has, of every kind.

        Includes the `resampled_only` certificates — one per channel that
        needed nothing — so this is the size of the table, not the number of
        repairs. :meth:`repair_counts` is what a reader wants.
        """
        total = await self._session.scalar(
            select(func.count())
            .select_from(StreamAnomalyRow)
            .where(StreamAnomalyRow.recording_id == recording_id)
        )
        return total or 0

    async def repair_counts(
        self, recording_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Repairs per recording, in one query, `resampled_only` excluded.

        A channel that needed no cleaning stores a `resampled_only` row so
        that "nothing was repaired" can be told from "the cleaner never ran"
        (`app.domain.streams.AnomalyKind`). It is not a repair, and counting
        it would tell an athlete their clean ride had eight anomalies.
        """
        if not recording_ids:
            return {}
        result = await self._session.execute(
            select(StreamAnomalyRow.recording_id, func.count())
            .where(
                StreamAnomalyRow.recording_id.in_(recording_ids),
                StreamAnomalyRow.kind != AnomalyKind.RESAMPLED_ONLY,
            )
            .group_by(StreamAnomalyRow.recording_id)
        )
        counted = {recording_id: total for recording_id, total in result.all()}
        return {
            recording_id: counted.get(recording_id, 0) for recording_id in recording_ids
        }
