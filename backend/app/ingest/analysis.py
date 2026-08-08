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
from app.domain.streams import StreamChannel
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
class SessionStreams:
    """One session's stored samples, as the chart endpoint serves them.

    Args:
        recording_id: Which recording the samples came from.
        t0: The grid origin, aware UTC. Row ``i`` covers ``[t0 + i, t0 + i+1)``.
        length: Rows in the grid — the same for every channel by construction
            (A4.1), which is what lets a client index them together.
        channels: Channel -> the cleaned (``_fixed``) column. Nulls preserved:
            a recording stop is a break in the trace, not a run of zeros.
        sources: Channel -> the label of the sensor that produced it (A4.3).
        recording_stops: ``[start, end)`` row ranges the recording was paused
            for.
        anomalies: The cleaner's repairs, so the chart can mark them (A4.2).
    """

    recording_id: uuid.UUID
    t0: dt.datetime
    length: int
    channels: Mapping[StreamChannel, tuple[float | None, ...]]
    sources: Mapping[StreamChannel, str]
    recording_stops: tuple[tuple[int, int], ...]
    anomalies: Sequence[StreamAnomalyRow]


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
    """Load one session's stored samples for rendering.

    Raises:
        NotFoundError: When the session has no recording (a manual session
            never had a stream), or when its stream file is missing or
            unreadable. The detail names which, because the empty state the UI
            renders is the detail.
    """
    recording = _sole_recording(session_row)
    if recording is None:
        raise NotFoundError(
            f"Session {session_row.id} has no recorded stream: it was entered "
            "by hand, so there are no per-second samples to chart"
        )
    path = stream_path(streams_root(), recording.id)
    stored = await asyncio.to_thread(_read, path)
    if stored is None:
        raise NotFoundError(
            f"The stream file for recording {recording.id} is missing or "
            "unreadable; the original file is kept and can be re-ingested"
        )
    return SessionStreams(
        recording_id=recording.id,
        t0=stored.t0,
        length=stored.row_count,
        channels=stored.fixed,
        sources=stored.sources,
        recording_stops=tuple(
            (int(start), int(end)) for start, end in recording.recording_stops
        ),
        anomalies=await recordings.anomalies(recording.id),
    )


def _sole_recording(session_row: SessionRow) -> RecordingRow | None:
    """The recording behind a session, or ``None`` for a manual one.

    Exactly one today; WP-6 owns the merge case, and this is the one place
    that will have to answer differently when it arrives.
    """
    return session_row.recordings[0] if session_row.recordings else None


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
        recording = _sole_recording(session_row)
        columns: dict[StreamChannel, tuple[float | None, ...]] = {}
        if recording is not None:
            stored = await asyncio.to_thread(
                _read, stream_path(streams_root(), recording.id)
            )
            if stored is None:
                logger.warning(
                    "metrics_stream_missing",
                    session_id=str(session_id),
                    recording_id=str(recording.id),
                )
            else:
                columns = dict(stored.fixed)

        analysis = analyse_session(
            SessionInputs(
                discipline=session_row.discipline,
                recording_time_s=(
                    recording.recording_time_s if recording is not None else 0.0
                ),
                elapsed_time_s=(
                    recording.elapsed_time_s
                    if recording is not None
                    else session_row.duration_s
                ),
                moving_time_s=(
                    recording.moving_time_s if recording is not None else 0.0
                ),
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
