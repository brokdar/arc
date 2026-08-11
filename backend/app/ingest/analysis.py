"""Reading a stored stream back, and turning it into a metric artefact.

The layer boundary this module exists to respect: `app.services` may not read
parquet, and `app.domain` may not know files exist. So the orchestration lives
here, beside `parquet.py`, which already owns `read_streams` — this module
loads the frame, resolves the anchors and the athlete, hands plain tuples to
`app.domain.session_analysis`, and hands the result to
`app.services.metrics` to persist. It decides nothing; every rule it looks
like it is applying is a domain function's.

Two entry points, both used by more than one caller:

* :meth:`SessionAnalyser.compute` — the whole metric set for one session. The
  ingest pipeline calls it after the parquet write, and the recompute endpoint
  calls it again later, possibly against different anchors.
* :func:`load_streams` — the chart payload's source, for the streams endpoint.

**Absence is never a failure.** A ride with no power meter, a session whose
stream file is missing, an athlete with no anchors at all: each produces an
artefact whose affected slots carry the reason, because the domain guarantees
it. What *is* refused is a session with no recording — a manual session has no
stream to read, and asking for one is a 404 with a reason rather than an empty
frame that would render as a flat line.
"""

import asyncio
import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.metrics import PerformedSet
from app.domain.session_analysis import SessionInputs, analyse_session
from app.domain.streams import AnomalyKind, StreamChannel
from app.ingest.parquet import (
    STREAMS_DIRNAME,
    StoredStreams,
    read_streams,
    stream_path,
)
from app.persistence.activity import (
    RecordingRepository,
    RecordingRow,
    SessionRepository,
    SessionRow,
    StreamAnomalyRow,
)
from app.persistence.metrics import SessionMetricsRow
from app.services.metrics import SessionMetricsService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamAnomaly:
    """One repaired region, addressed on the **joined** grid (A4.2).

    A detached copy of `app.persistence.activity.StreamAnomalyRow` rather than
    the row itself, because a merged session's second recording contributes its
    regions shifted by wherever that recording begins in the joined grid — and
    shifting the ORM row would mark it dirty and write the display offset back
    into the database on the next commit.
    """

    channel: StreamChannel
    start_index: int
    end_index: int
    kind: AnomalyKind
    substituted_value: float | None


@dataclass(frozen=True, slots=True)
class SessionStreams:
    """One session's stored samples, as the chart endpoint serves them.

    For a merged session (WP-6.5) this is the **joined** view: the recordings
    laid end to end on one 1 Hz grid anchored at the earliest one's origin,
    with the gap between them left as unrecorded rows and reported as a
    recording stop. Every index here — stops, anomalies — addresses that grid.

    Args:
        recording_id: The first recording the samples came from, in time
            order. Kept alongside `recording_ids` so a single-recording session
            reads exactly as it always has.
        recording_ids: Every recording joined into this view, in time order.
        t0: The grid origin, aware UTC. Row ``i`` covers ``[t0 + i, t0 + i+1)``.
        length: Rows in the grid — the same for every channel by construction
            (A4.1), which is what lets a client index them together.
        channels: Channel -> the cleaned (``_fixed``) column. Nulls preserved:
            a recording stop is a break in the trace, not a run of zeros.
        sources: Channel -> the label of the sensor that produced it (A4.3).
        recording_stops: ``[start, end)`` row ranges the recording was paused
            for, including the gap between two joined recordings.
        anomalies: The cleaner's repairs, so the chart can mark them (A4.2).
    """

    recording_id: uuid.UUID
    recording_ids: tuple[uuid.UUID, ...]
    t0: dt.datetime
    length: int
    channels: Mapping[StreamChannel, tuple[float | None, ...]]
    sources: Mapping[StreamChannel, str]
    recording_stops: tuple[tuple[int, int], ...]
    anomalies: Sequence[StreamAnomaly]


def streams_root() -> Path:
    """Where stream files live — the same directory the pipeline writes to."""
    return get_settings().data.root / STREAMS_DIRNAME


def _read(path: Path) -> StoredStreams | None:
    """Read one stream file, or ``None`` when it is not there.

    A missing file is not an exception here on purpose: the row saying it
    should exist is committed before the artefact is computed, and a metric
    run that raised would leave the athlete with an ingested session and an
    error instead of an ingested session and a reason.
    """
    try:
        return read_streams(path)
    except FileNotFoundError, ValueError:
        return None


async def load_streams(
    session_row: SessionRow, recordings: RecordingRepository
) -> SessionStreams:
    """Load one session's stored samples for rendering, joined if there are several.

    Raises:
        NotFoundError: When the session has no recording (a manual session
            never had a stream), or when every one of its stream files is
            missing or unreadable. The detail names which, because the empty
            state the UI renders is the detail.
    """
    segments = await _segments(session_row)
    joined = _join(segments)
    anomalies: list[StreamAnomaly] = []
    for segment in segments:
        anomalies.extend(
            _shifted(anomaly, segment.offset)
            for anomaly in await recordings.anomalies(segment.recording.id)
        )
    return SessionStreams(
        recording_id=segments[0].recording.id,
        recording_ids=tuple(segment.recording.id for segment in segments),
        t0=segments[0].stored.t0,
        length=joined.length,
        channels=joined.channels,
        sources=joined.sources,
        recording_stops=joined.recording_stops,
        anomalies=sorted(anomalies, key=lambda one: (one.start_index, one.end_index)),
    )


@dataclass(frozen=True, slots=True)
class _Segment:
    """One recording placed on the joined grid."""

    recording: RecordingRow
    stored: StoredStreams
    #: Row this recording's first sample occupies in the joined grid. Always 0
    #: for a session with one recording, which is what keeps that path — every
    #: session the MVP ingests — byte-for-byte what it was before WP-6.
    offset: int


@dataclass(frozen=True, slots=True)
class _Durations:
    """A4.4's three durations for a session, however many recordings it has.

    Args:
        recording_time_s: Elapsed minus every stop — **the duration term in
            training load** (A5.1), summed across recordings.
        elapsed_time_s: The recording's own elapsed span, or the session's
            wall clock once more than one recording spans it.
        moving_time_s: The basis the artefact's averages are taken over
            (D194), summed across recordings; never a load input.
    """

    recording_time_s: float
    elapsed_time_s: float
    moving_time_s: float


@dataclass(frozen=True, slots=True)
class _Joined:
    """Several recordings laid end to end on one 1 Hz grid."""

    length: int
    channels: Mapping[StreamChannel, tuple[float | None, ...]]
    sources: Mapping[StreamChannel, str]
    recording_stops: tuple[tuple[int, int], ...]


async def _segments(session_row: SessionRow) -> list[_Segment]:
    """Read every readable stream behind a session, in time order.

    Ordered by the **stored grid origin** rather than by row order or id: a
    recording row carries no start time of its own, and after a merge the
    absorbed recording may well have been ingested first.

    Raises:
        NotFoundError: When the session has no recording at all, or when none
            of its stream files can be read.
    """
    if not session_row.recordings:
        raise NotFoundError(
            f"Session {session_row.id} has no recorded stream: it was entered "
            "by hand, so there are no per-second samples to chart"
        )
    read: list[tuple[RecordingRow, StoredStreams]] = []
    for recording in session_row.recordings:
        stored = await asyncio.to_thread(
            _read, stream_path(streams_root(), recording.id)
        )
        if stored is not None:
            read.append((recording, stored))
    if not read:
        raise NotFoundError(
            f"The stream file(s) for session {session_row.id} are missing or "
            "unreadable; the original file is kept and can be re-ingested"
        )
    read.sort(key=lambda pair: pair[1].t0)
    origin = read[0][1].t0
    segments: list[_Segment] = []
    cursor = 0
    for recording, stored in read:
        offset = max(cursor, round((stored.t0 - origin).total_seconds()))
        segments.append(_Segment(recording=recording, stored=stored, offset=offset))
        cursor = offset + stored.row_count
    return segments


def _join(segments: Sequence[_Segment]) -> _Joined:
    """Lay the segments on one grid, padding the gaps between them.

    The gap between two recordings is filled with **unrecorded rows** and
    reported as a recording stop, which is what it is: the athlete stopped the
    head unit at the garage door. Nothing about the numbers changes as a
    result — `app.domain.metrics` excludes rows with no reading rather than
    reading them as zero, and the load's duration term is the recordings' own
    recording time summed, never the length of this grid (A5.1).
    """
    length = segments[-1].offset + segments[-1].stored.row_count
    channels: dict[StreamChannel, list[float | None]] = {}
    sources: dict[StreamChannel, str] = {}
    stops: list[tuple[int, int]] = []
    previous_end = 0
    for segment in segments:
        if segment.offset > previous_end:
            stops.append((previous_end, segment.offset))
        previous_end = segment.offset + segment.stored.row_count
        sources |= dict(segment.stored.sources)
        for channel, values in segment.stored.fixed.items():
            empty: list[float | None] = [None] * length
            column = channels.setdefault(channel, empty)
            column[segment.offset : segment.offset + len(values)] = values
        stops.extend(
            (int(start) + segment.offset, int(end) + segment.offset)
            for start, end in segment.recording.recording_stops
        )
    return _Joined(
        length=length,
        channels={channel: tuple(values) for channel, values in channels.items()},
        sources=sources,
        recording_stops=tuple(sorted(stops)),
    )


def _shifted(anomaly: StreamAnomalyRow, offset: int) -> StreamAnomaly:
    """One stored repair, addressed on the joined grid instead of its own."""
    return StreamAnomaly(
        channel=anomaly.channel,
        start_index=anomaly.start_index + offset,
        end_index=anomaly.end_index + offset,
        kind=anomaly.kind,
        substituted_value=anomaly.substituted_value,
    )


class SessionAnalyser:
    """Computes and stores one session's metric artefact."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        sessions: SessionRepository,
        recordings: RecordingRepository,
        metrics: SessionMetricsService,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._recordings = recordings
        self._metrics = metrics

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the analyser and everything it reads to one database session."""
        return cls(
            session,
            sessions=SessionRepository(session),
            recordings=RecordingRepository(session),
            metrics=SessionMetricsService.from_session(session),
        )

    async def compute(
        self, session_id: uuid.UUID, *, actor: Actor, reason: str | None = None
    ) -> SessionMetricsRow:
        """Compute and store a new metric version for one session.

        Reads the session's stored stream when it has one, resolves every
        pinnable anchor that is **in force now** (D115) and records exactly
        which versions those were, then runs the domain over the cleaned
        columns with the recording's ``recording_time_s`` — A5.1's duration
        term, not elapsed and not moving time.

        The anchors and the athlete's sex come from
        `app.services.metrics.SessionMetricsService`, not from a second copy
        of the resolution here: a ride and a typed-in gym session have to
        agree about what was in force at the same instant, and when they did
        not, a recompute of an unchanged session wrote a divergent version.

        A session with no stream (a typed-in gym session) still gets an
        artefact: its logged sets produce the strength block and every
        stream-derived slot carries its reason.

        Raises:
            NotFoundError: When no session has that id.
        """
        session_row = await self._sessions.get(session_id)
        if session_row is None:
            raise NotFoundError(f"Session {session_id} not found")

        anchors = await self._metrics.current_anchors()
        sex = await self._metrics.athlete_sex()
        durations, columns = await self._recorded(session_row)

        analysis = analyse_session(
            SessionInputs(
                discipline=session_row.discipline,
                recording_time_s=durations.recording_time_s,
                elapsed_time_s=durations.elapsed_time_s,
                moving_time_s=durations.moving_time_s,
                columns=columns,
                sex=sex,
                anchors={
                    anchor_type: version
                    for anchor_type, (version, _) in anchors.items()
                },
                sets=[
                    PerformedSet(reps=logged.reps, load_kg=logged.load_kg)
                    for logged in session_row.logged_sets
                ],
            )
        )
        return await self._metrics.record(
            session_id,
            analysis,
            actor=actor,
            pins={
                anchor_type: version_id
                for anchor_type, (_, version_id) in anchors.items()
            },
            reason=reason,
        )

    async def _recorded(
        self, session_row: SessionRow
    ) -> tuple[_Durations, dict[StreamChannel, tuple[float | None, ...]]]:
        """The A4.4 durations and the cleaned columns the metrics run over.

        One recording is the ordinary case and answers with its own three
        numbers unchanged. A **merged** session (WP-6.5) answers with the
        joined grid and with the durations *summed* rather than re-derived
        from it: recording time is elapsed minus every stop (A4.4), and the
        gap between two files is a stop by definition — so summing the two
        recordings' own numbers is the same answer, arrived at without asking
        the join to remember what it padded.

        A session with no recording, or whose stream files are all unreadable,
        answers with no columns and the reasons the domain writes for them.
        """
        if not session_row.recordings:
            return (
                _Durations(
                    recording_time_s=0.0,
                    elapsed_time_s=session_row.duration_s,
                    moving_time_s=0.0,
                ),
                {},
            )
        try:
            segments = await _segments(session_row)
        except NotFoundError:
            logger.warning(
                "metrics_stream_missing",
                session_id=str(session_row.id),
                recordings=[str(recording.id) for recording in session_row.recordings],
            )
            segments = []
        columns = dict(_join(segments).channels) if segments else {}
        recordings = (
            [segment.recording for segment in segments]
            if segments
            else list(session_row.recordings)
        )
        return (
            _Durations(
                recording_time_s=sum(one.recording_time_s for one in recordings),
                # Counted over the session's recordings, not the readable
                # survivors: a merged session with one unreadable stream file
                # still spans the whole ride, and taking the lone survivor's
                # own span would describe half the ride while claiming to
                # describe the session.
                elapsed_time_s=(
                    recordings[0].elapsed_time_s
                    if len(session_row.recordings) == 1
                    else session_row.duration_s
                ),
                moving_time_s=sum(one.moving_time_s for one in recordings),
            ),
            columns,
        )
