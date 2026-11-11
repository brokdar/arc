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
    false,
    func,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.activity import (
    ClassificationSource,
    HoldQuality,
    LoggedSetKind,
    PainBand,
    PainKind,
    RecordingKind,
    SessionContext,
    SessionDiscipline,
    SessionLifecycle,
    SessionMatchStatus,
    SetConfidence,
    SetSide,
    SetSource,
)
from app.domain.anchors import Provenance
from app.domain.streams import AnomalyKind, StreamChannel
from app.domain.strength import logged_set_units, logged_sides_worked
from app.persistence.db import Base, flush, refresh
from app.persistence.exercises import MAX_SLUG_LENGTH
from app.persistence.search import contains
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

#: Longest private reason a session may carry for "don't learn from today".
MAX_DONT_LEARN_REASON_LENGTH = 500

#: Longest reference a logged set may carry to the planned line it answered.
MAX_PLANNED_LINE_REF_LENGTH = 64


class SessionRow(Base):
    """One session: what the athlete actually did, or is doing (`lifecycle`)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: When it started, aware UTC. Indexed because the WP-6 candidate window
    #: and the overlap-duplicate check (B-2) both query on it.
    start_time: Mapped[dt.datetime] = mapped_column(UtcDateTime, index=True)
    end_time: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    #: Whether `start_time` is a time somebody reported. False only for a
    #: manual session the athlete dated but never timed ("Monday evening"): its
    #: `start_time` is then the **local midnight** of `local_date`, an anchor
    #: the row needs because the column is not null, and not a clock reading
    #: anything may display or reason about. Server default true because every
    #: row that predates this column was timed.
    start_time_known: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true()
    )
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
    #: Between Start and Finish (`active`) or done (`finished`). Every row
    #: before the gym screen is finished, hence the server default and no
    #: backfill. Read by the card service: no card is shown while any session
    #: is active.
    lifecycle: Mapped[SessionLifecycle] = mapped_column(
        enum_column(SessionLifecycle),
        default=SessionLifecycle.FINISHED,
        server_default=SessionLifecycle.FINISHED.value,
        index=True,
    )
    #: Session RPE, 0-10, for manual strength entry (B-6). Null for a device
    #: session unless the athlete adds one.
    rpe: Mapped[float | None] = mapped_column(Float)
    #: Ambient temperature during the session, °C, athlete-reported (#23) —
    #: the conditions the measurement was taken under, not a device channel.
    #: One number for the whole session: the cheapest queryable slice of
    #: "conditions", ahead of the MMP's observed-conditions model.
    temperature_c: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES_LENGTH))
    #: "Don't learn from today": the session stays in history but out of
    #: estimates, progression and learned numbers — an off day the athlete
    #: chose not to teach arc. Server default false because every earlier row
    #: was a session somebody meant to count.
    dont_learn: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    #: The athlete's optional, private reason for `dont_learn`. Not shared with
    #: the coach (the MCP surface never returns it): health and life context
    #: reaches the agent only when the athlete opts in.
    dont_learn_reason: Mapped[str | None] = mapped_column(
        String(MAX_DONT_LEARN_REASON_LENGTH)
    )
    #: The Finish screen's "How hard was the whole session? 1-10", a whole
    #: number. Distinct from `rpe`, the free 0-10 session RPE a recap or the
    #: coach reports; null when skipped, and never guessed.
    session_rating: Mapped[int | None] = mapped_column(Integer)

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
    #:
    #: Power and heart rate only, deliberately — these are the columns that
    #: record a *choice between devices*, which is the ambiguity A4.3 is about:
    #: a bike with two power meters, and a strap that may or may not be the
    #: watch's own. Hence the trio of columns each: what was on offer, what won,
    #: and the rule that picked it. Cadence, speed, altitude and temperature
    #: come from one sensor per file in practice, so a candidate list and a
    #: rule would always repeat the device — cost without a question it
    #: answers. One of them gains columns when a file is found that disagrees.
    #:
    #: Distance is the exception that proves the shape: it carries a source
    #: label too (``app.ingest.pipeline.source_labels``), but the label names a
    #: *field* rather than a device, so there is no tie-break to store and no
    #: column here — it lives in the parquet metadata alone.
    power_source_candidates: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    power_source: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    power_source_rule: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    hr_source_candidates: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    hr_source: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))
    hr_source_rule: Mapped[str | None] = mapped_column(String(MAX_SOURCE_LENGTH))

    #: `StreamChannel` values actually present in the parquet frame.
    channels: Mapped[list[str]] = mapped_column(JSONColumn, default=list)

    #: Reserved (R4), no behavior: the vendor's own id for the file, and the
    #: **transport** that carried it (`app.domain.activity.IngestSource` —
    #: `dropbox`, or null for a file that arrived on this machine). Both are
    #: for the MMP's adapters and for the "a richer file supersedes a
    #: lower-fidelity import of the same ride" merge case.
    #:
    #: `source` is **not** which integration it came from, and no column here
    #: is: that is configuration on the feed, and `IngestSource`'s docstring
    #: says why a folder cannot name the source and what is lost by not
    #: freezing a guess about it into these rows.
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
    """One set the athlete logged (manual strength entry, B-6; the gym screen).

    **Identity is `client_id`, order is `set_index`.** The phone mints a uuid7
    for every set before it has signal, and the server upserts on it, so a set
    uploaded twice is stored once — hence the unique `(session_id, client_id)`.
    `set_index` stays the display and scoring order (positional scoring pairs
    by it) but is no longer unique: a reorder rewrites it in one transaction,
    and a unique index would be violated half-way through any rewrite that
    swaps two positions. Separate `exercise_order` / `set_number` columns were
    the alternative and were rejected — they would be a second order to keep
    in step with the one scoring already reads. Pinned by
    `test_activity_persistence.py::test_reorder_rewrites_set_index` and, on
    both dialects, by `tests/integration/test_logged_set_order.py`.
    """

    __tablename__ = "logged_sets"
    __table_args__ = (UniqueConstraint("session_id", "client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: The set's own identity, minted on the phone (uuid7) so a retried upload
    #: is recognised. Unique per session only — two sessions may carry the
    #: same value. Minted here for a caller that sends none (the coach, legacy
    #: callers), and backfilled as uuid7 for every row older than the column.
    client_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid7)
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
    #: 0-based position within the session: the display and scoring order,
    #: not an identity (see the class docstring).
    set_index: Mapped[int] = mapped_column(Integer)
    #: Warm-up, working, or one of the working kinds. Warm-ups count nowhere.
    set_kind: Mapped[LoggedSetKind] = mapped_column(
        enum_column(LoggedSetKind),
        default=LoggedSetKind.WORKING,
        server_default=LoggedSetKind.WORKING.value,
    )
    #: Which side the set worked. Every row older than the column is `both` —
    #: including the `per_side` ones, which never said which side they were.
    side: Mapped[SetSide] = mapped_column(
        enum_column(SetSide), default=SetSide.BOTH, server_default=SetSide.BOTH.value
    )
    #: The prescription line this set answered, so scoring can pair by line
    #: rather than by position when a session was reordered or swapped. A
    #: reference, never a copy: the line's targets are not read from it.
    planned_line_ref: Mapped[str | None] = mapped_column(
        String(MAX_PLANNED_LINE_REF_LENGTH)
    )
    #: The catalogue movement this set stood in for, when the athlete swapped.
    replaced_exercise_id: Mapped[str | None] = mapped_column(
        String(MAX_SLUG_LENGTH), ForeignKey("exercises.id", ondelete="SET NULL")
    )
    #: How the set reached arc. Rows older than the column were all typed in
    #: after the fact, hence the server default `recap`.
    source: Mapped[SetSource] = mapped_column(
        enum_column(SetSource),
        default=SetSource.RECAP,
        server_default=SetSource.RECAP.value,
    )
    confidence: Mapped[SetConfidence] = mapped_column(
        enum_column(SetConfidence),
        default=SetConfidence.HIGH,
        server_default=SetConfidence.HIGH.value,
    )
    #: Repetitions performed, or null for a timed hold. Exactly one of `reps`
    #: and `duration_s` is set — the service refuses the write and
    #: `app.domain.metrics.PerformedSet` refuses to be built from a row that
    #: breaks it, because a check constraint here could not be written once for
    #: both dialects without a batch migration on every future change to it.
    reps: Mapped[int | None] = mapped_column(Integer)
    #: Seconds held, for a timed set. Null for a rep-based one.
    duration_s: Mapped[int | None] = mapped_column(Integer)
    #: Whether the row holds both sides of a one-side movement written as one
    #: line ("8 each side"): one set, its reps worked twice. A single side is
    #: `side` instead, and the two together are refused. Carried on the logged
    #: row as well as the prescription because completion divides logged sets
    #: by prescribed sets, and the two have to be the same unit or the ratio is
    #: nonsense (`app.domain.strength.logged_set_units`).
    per_side: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), default=False
    )
    load_kg: Mapped[float | None] = mapped_column(Float)
    #: Reps in reserve, as **reported** after the set — never the target.
    rir: Mapped[int | None] = mapped_column(Integer)
    #: Whether `rir` is a floor rather than a count. The Standard effort row
    #: ends in "4+" (felt easy), which is stored as ``rir = 4, rir_capped =
    #: true``; "not sure" / "log without rating" is ``rir = null``. Chosen over
    #: an open-ended sentinel (``rir = 99``, ``-1``), which every reader doing
    #: arithmetic on `rir` would have to know to skip. True only with
    #: ``rir = 4``.
    rir_capped: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    #: RPE as reported (Full control), 6-10 in half steps. Converts to reps
    #: left as 10 - RPE, and when both are given they must agree.
    rpe: Mapped[float | None] = mapped_column(Float)
    #: The rating of a timed hold — its end buttons. Null for a rep set.
    hold_quality: Mapped[HoldQuality | None] = mapped_column(enum_column(HoldQuality))
    #: Rest taken before this set, seconds, as the phone's timer measured it.
    rest_s: Mapped[int | None] = mapped_column(Integer)
    #: When the set was logged, aware UTC, plus the device's UTC offset at the
    #: time in minutes — stored per set so a later per-athlete timezone can
    #: recover local clock readings without guessing.
    completed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    utc_offset_min: Mapped[int | None] = mapped_column(Integer)
    #: Pain reported on this set: its kind, and the 0-10 band it fell in.
    pain_kind: Mapped[PainKind | None] = mapped_column(enum_column(PainKind))
    pain_band: Mapped[PainBand | None] = mapped_column(enum_column(PainBand))
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


def logged_working_sets(row: LoggedSetRow) -> float:
    """How many sets one logged row counts as: a lone side is ½, else 1.

    The rule itself is `app.domain.strength.logged_set_units` — a left-right
    pair is one set.

    Here rather than in a service for the reason `session_duration_s` gives
    just below: three callers need the same answer — the matcher's evidence,
    the scorer's load expansion and the metric artefact's set count — and a
    session that counted two of them in rounds and one in working sets would
    report a completion ratio nobody could reproduce. The prescription's half
    of this rule is `app.domain.strength.StrengthSet.working_sets`, and the
    two exist to be the same number.
    """
    return logged_set_units(row.side)


def logged_sides(row: LoggedSetRow) -> int:
    """How many sides one logged row's reps and load were worked by.

    `app.domain.strength.logged_sides_worked` over the row: two for a
    ``per_side`` row that holds the pair in one line, one otherwise.
    """
    return logged_sides_worked(per_side=row.per_side, side=row.side)


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
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SessionRow], int]:
        """Return a page of sessions, newest first, plus the total.

        ``query`` is a case-insensitive literal substring of the session's
        **current** `notes` (`app.persistence.search`). Sessions with no note
        never match. Earlier wordings live only in audit rows
        (`previous_notes`) and are deliberately not searched: a hit on text the
        athlete has since replaced would answer a question about a note that
        no longer exists.

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
        if query is not None:
            criteria.append(contains(SessionRow.notes, query))
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

    async def all_oldest_first(self) -> Sequence[SessionRow]:
        """Every session, oldest first.

        Unpaged on purpose, for the one read that needs the whole history: the
        fitness series (`app.services.fitness`) is an average that starts on
        the first session ever recorded, and a page of it would be a different
        average. One athlete's lifetime of sessions is a few thousand rows.
        """
        result = await self._session.execute(
            select(SessionRow).order_by(
                SessionRow.local_date.asc(), SessionRow.start_time.asc()
            )
        )
        return list(result.scalars())

    async def with_logged_sets_oldest_first(self) -> Sequence[SessionRow]:
        """Every session holding at least one logged set, oldest first.

        Unpaged for the reason `all_oldest_first` gives: the exports
        (`app.services.exports`) hand over the whole history in one file, and
        a session with no sets has nothing to put in a set-level export.
        """
        has_sets = select(LoggedSetRow.id).where(
            LoggedSetRow.session_id == SessionRow.id
        )
        result = await self._session.execute(
            select(SessionRow)
            .where(has_sets.exists())
            .order_by(SessionRow.start_time.asc(), SessionRow.id.asc())
        )
        return list(result.scalars())

    async def last_logged_sets(self) -> dict[str, tuple[LoggedSetRow, dt.date]]:
        """The last non-warm-up set of every catalogue exercise ever logged.

        Keyed by exercise slug, with the local date of the session it was
        logged in. "Last" is the latest session by start time (ties by id,
        uuid7 insertion order), then the highest `set_index` in it — never
        `created_at`, which the wall clock can step behind
        (`.claude/rules/clock-monotonicity.md`). Warm-ups are skipped: they
        say nothing about the working weight. Free-text sets have no slug to
        key by. One query over the set history; a single athlete's lifetime
        of sets is tens of thousands of narrow rows at most.
        """
        result = await self._session.execute(
            select(LoggedSetRow, SessionRow.local_date)
            .join(SessionRow, LoggedSetRow.session_id == SessionRow.id)
            .where(
                LoggedSetRow.exercise_id.is_not(None),
                LoggedSetRow.set_kind != LoggedSetKind.WARMUP,
            )
            .order_by(
                SessionRow.start_time.desc(),
                SessionRow.id.desc(),
                LoggedSetRow.set_index.desc(),
                LoggedSetRow.id.desc(),
            )
        )
        latest: dict[str, tuple[LoggedSetRow, dt.date]] = {}
        for row, local_date in result.all():
            if row.exercise_id is not None and row.exercise_id not in latest:
                latest[row.exercise_id] = (row, local_date)
        return latest

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

    async def manual_replay(
        self,
        *,
        start_time: dt.datetime,
        discipline: SessionDiscipline,
        client_ids: Sequence[uuid.UUID],
    ) -> SessionRow | None:
        """The manual session a repeated upload already created, if any.

        A replay is a manual session with the same start and discipline that
        already holds **every** one of ``client_ids``. All of them, not some:
        uniqueness is per session, so another session sharing one id is a
        different session, not this one again.
        """
        wanted = set(client_ids)
        if not wanted:
            return None
        result = await self._session.execute(
            select(LoggedSetRow.session_id, func.count())
            .join(SessionRow, SessionRow.id == LoggedSetRow.session_id)
            .where(
                SessionRow.recording_kind == RecordingKind.MANUAL,
                SessionRow.start_time == start_time,
                SessionRow.discipline == discipline,
                LoggedSetRow.client_id.in_(wanted),
            )
            .group_by(LoggedSetRow.session_id)
        )
        for session_id, held in result.all():
            if held == len(wanted):
                return await self.get(session_id)
        return None

    async def add(self, row: SessionRow) -> SessionRow:
        """Persist a session (new or modified) and refresh generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await refresh(self._session, row)
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
        await refresh(self._session, row)
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
