"""Regenerating a stored stream from the original file it came from.

The invariant this module exists to keep true: **the originals under
``data/originals/`` are sufficient to rebuild every derived artefact.** Nothing
else in the system is a source. A parquet file is a cache of what parsing and
cleaning one original produced, the recording row is a cache of the durations
and sources that same parse found, and both can be thrown away and made again.

Which matters the moment the parse itself improves. Recomputing metrics reads
the *stored parquet*, so a session ingested before a channel existed can never
gain it by recomputation however many times it is run — the column is not in
the file. D200 is exactly that case: eleven sessions were ingested before arc
read the device's odometer, and their distances are integrations of speed until
their streams are made again from the FIT files that have carried the odometer
all along.

So :class:`StreamRebuilder` re-parses an original, re-derives everything that
parse produces, and writes it over the stream file and the recording row — the
same functions the pipeline itself uses (`app.ingest.pipeline.prepare`,
`app.ingest.pipeline.source_labels`), never a second implementation that could
drift. It does **not** re-classify the session, re-run matching, or touch the
metric artefact: a rebuild is not an ingest, and the versioned chain of metrics
is appended to by `app.ingest.analysis.SessionAnalyser` afterwards, with a
reason, like any other recomputation.

The original file is opened read-only and is never moved, renamed or deleted.

Driven by ``just rebuild-streams`` (``scripts/rebuild_streams.py``); there is
deliberately no HTTP endpoint. It is an operator's maintenance action on the
whole store, it takes as long as re-parsing every file, and nothing in the
product needs to trigger it.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.streams import ParsedActivity, ResampleResult, StreamChannel
from app.ingest.parquet import stream_path, write_streams
from app.ingest.parsers import parse
from app.ingest.pipeline import IngestPaths, prepare, source_labels
from app.persistence.activity import (
    RecordingRepository,
    RecordingRow,
    StreamAnomalyRow,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit

logger = get_logger(__name__)

#: `entity_type` written on this module's audit rows.
ENTITY_TYPE = "recording"

#: The action recorded when a stream file is made again from its original.
REBUILT = "recording.streams_rebuilt"


class RebuildStatus(StrEnum):
    """What became of one recording's rebuild attempt.

    Only :attr:`REBUILT` changed anything. The other three are reported rather
    than raised because a maintenance pass over a whole store must not stop at
    the first file somebody moved by hand — the run's value is in the ones it
    can do, and the ones it cannot are a list the operator reads at the end.
    """

    REBUILT = "rebuilt"
    #: The path the recording row names is not on disk any more.
    ORIGINAL_MISSING = "original_missing"
    #: The file is there but no parser could make an activity out of it.
    UNREADABLE = "unreadable"
    #: The file parsed, but not into the sport this recording is (A4.5's
    #: ``file_sport_index``). A multisport file that lost a sport is not a
    #: file to rebuild half of.
    SPORT_MISSING = "sport_missing"


@dataclass(frozen=True, slots=True)
class RebuildOutcome:
    """One recording's result, for the operator's summary.

    Args:
        recording_id: Which recording.
        session_id: The session it belongs to — what the caller recomputes.
        status: What happened.
        detail: The same in words, with the numbers or the failure in it.
        channels: The channels the rebuilt frame carries, sorted. Empty for
            every status but :attr:`RebuildStatus.REBUILT`.
    """

    recording_id: uuid.UUID
    session_id: uuid.UUID
    status: RebuildStatus
    detail: str
    channels: tuple[str, ...] = ()

    @property
    def rebuilt(self) -> bool:
        """Whether this recording's stream file was actually written."""
        return self.status is RebuildStatus.REBUILT


class StreamRebuilder:
    """Rewrites stream files and recording rows from the original files."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        recordings: RecordingRepository,
        audit: AuditRepository,
        paths: IngestPaths,
    ) -> None:
        self._session = session
        self._recordings = recordings
        self._audit = audit
        self._paths = paths

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the rebuilder and its repositories to one database session."""
        return cls(
            session,
            recordings=RecordingRepository(session),
            audit=AuditRepository(session),
            paths=IngestPaths.from_settings(),
        )

    async def rebuild_all(self, *, actor: Actor) -> list[RebuildOutcome]:
        """Rebuild every recording's stream, oldest first.

        Each recording is committed on its own, so a run interrupted halfway
        leaves the recordings it reached rebuilt and the rest untouched —
        which is a state the next run simply finishes, rather than one that
        has to be reasoned about.
        """
        return [
            await self.rebuild(recording.id, actor=actor)
            for recording in await self._recordings.all()
        ]

    async def rebuild(self, recording_id: uuid.UUID, *, actor: Actor) -> RebuildOutcome:
        """Rebuild one recording's stream file and derived row.

        Raises:
            NotFoundError: When no recording has that id. Unlike the four
                :class:`RebuildStatus` outcomes, this one is the caller's
                mistake rather than the data's.
        """
        recording = await self._recordings.get(recording_id)
        if recording is None:
            raise NotFoundError(f"Recording {recording_id} not found")

        original = Path(recording.original_path)
        loaded = await asyncio.to_thread(_load, original, recording.file_sport_index)
        if isinstance(loaded, tuple):
            status, detail = loaded
            return _outcome(recording, status, detail)

        prepared = await asyncio.to_thread(prepare, loaded)
        activity = loaded
        await asyncio.to_thread(
            write_streams,
            stream_path(self._paths.streams, recording.id),
            frame=prepared.resampled.frame,
            cleaned=prepared.cleaned,
            sources=source_labels(activity),
        )
        before = tuple(recording.channels)
        _refresh(recording, activity, prepared.channels, prepared.resampled)
        await self._recordings.add(recording)
        await self._recordings.replace_anomalies(
            recording.id,
            [
                StreamAnomalyRow(
                    recording_id=recording.id,
                    channel=anomaly.channel,
                    start_index=anomaly.start_index,
                    end_index=anomaly.end_index,
                    kind=anomaly.kind,
                    substituted_value=anomaly.substituted_value,
                )
                for anomaly in prepared.cleaned.anomalies
            ],
        )
        await self._audit.record(
            actor=actor,
            action=REBUILT,
            entity_type=ENTITY_TYPE,
            entity_id=recording.id,
            payload={
                "session_id": str(recording.session_id),
                "original_path": str(original),
                "rows": prepared.resampled.frame.row_count,
                "channels_before": list(before),
                "channels_after": list(recording.channels),
                "anomalies": len(prepared.cleaned.anomalies),
            },
        )
        await commit(self._session)
        logger.info(
            "streams_rebuilt",
            recording_id=str(recording.id),
            session_id=str(recording.session_id),
            channels=recording.channels,
        )
        return _outcome(
            recording,
            RebuildStatus.REBUILT,
            f"{prepared.resampled.frame.row_count} rows, "
            f"{len(prepared.cleaned.anomalies)} anomaly rows"
            + (
                f", gained {', '.join(sorted(set(recording.channels) - set(before)))}"
                if set(recording.channels) - set(before)
                else ""
            ),
            channels=tuple(recording.channels),
        )


def _load(
    original: Path, file_sport_index: int
) -> ParsedActivity | tuple[RebuildStatus, str]:
    """Re-read one sport out of one original, or say why it cannot be read.

    Every disk touch of a rebuild is in here, so the caller can put the whole
    of it on a worker thread: statting the file, parsing it and picking the
    sport out are one blocking unit, and re-parsing a four-hour FIT file is a
    second of CPU that must not be spent on the event loop.
    """
    if not original.is_file():
        return RebuildStatus.ORIGINAL_MISSING, f"the original file is not at {original}"
    try:
        activities = parse(original)
    except Exception as exc:  # noqa: BLE001 — one bad file must not stop a run
        return RebuildStatus.UNREADABLE, f"the file could not be parsed: {exc}"
    for candidate in activities:
        if candidate.file_sport_index == file_sport_index:
            return candidate
    return (
        RebuildStatus.SPORT_MISSING,
        (
            f"the file parsed into {len(activities)} sport(s), none of them "
            f"index {file_sport_index}"
        ),
    )


def _refresh(
    recording: RecordingRow,
    activity: ParsedActivity,
    channels: frozenset[StreamChannel],
    resampled: ResampleResult,
) -> None:
    """Overwrite every field of the row that the parse derives.

    Everything here is a **function of the original file**, so a rebuild that
    left any of it alone would leave the row describing one parse and the
    parquet another. What is deliberately *not* touched: the session it belongs
    to, its discipline and classification (re-classifying an ingested session
    would move it on the calendar), the file hash and path, and the metric
    artefact — all of which are decisions or records rather than derivations.
    """
    recording.elapsed_time_s = resampled.elapsed_time_s
    recording.recording_time_s = resampled.recording_time_s
    recording.recording_stops = [
        [start, end] for start, end in resampled.recording_stops
    ]
    recording.median_time_delta_s = resampled.median_time_delta_s
    recording.moving_time_s = resampled.moving_time_s
    recording.sport = activity.sport
    recording.power_source_candidates = list(activity.power_source_candidates)
    recording.power_source = activity.power_source
    recording.power_source_rule = activity.power_source_rule
    recording.hr_source_candidates = list(activity.hr_source_candidates)
    recording.hr_source = activity.hr_source
    recording.hr_source_rule = activity.hr_source_rule
    recording.channels = sorted(channel.value for channel in channels)


def _outcome(
    recording: RecordingRow,
    status: RebuildStatus,
    detail: str,
    *,
    channels: tuple[str, ...] = (),
) -> RebuildOutcome:
    """One recording's result, with the ids a caller needs to act on it."""
    if status is not RebuildStatus.REBUILT:
        logger.warning(
            "streams_rebuild_skipped",
            recording_id=str(recording.id),
            status=status.value,
            detail=detail,
        )
    return RebuildOutcome(
        recording_id=recording.id,
        session_id=recording.session_id,
        status=status,
        detail=detail,
        channels=channels,
    )


def session_ids(outcomes: Sequence[RebuildOutcome]) -> list[uuid.UUID]:
    """The sessions whose streams changed, deduplicated, in order.

    A merged session has more than one recording (WP-6.5), and recomputing its
    metrics twice would append two versions to its chain for one rebuild.
    """
    seen: dict[uuid.UUID, None] = {}
    for outcome in outcomes:
        if outcome.rebuilt:
            seen.setdefault(outcome.session_id)
    return list(seen)
