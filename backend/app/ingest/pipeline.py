"""The per-file ingest pipeline: hash, dedup, parse, validate, file, store.

One function matters — :meth:`IngestPipeline.ingest_file` — and the order of
its steps is the work order's (B-2), because each one exists to stop the next
from doing damage:

1. **sha256.** The file's identity. Everything after it is keyed on this.
2. **Dedup by hash**, against ingested recordings *and* unresolved quarantine
   records. Re-seeing a file is a ``duplicate_file`` log line, never an error
   and never a second session: this is what makes the watched folder safe to
   point at a directory the athlete's device keeps re-syncing.
3. **Parse** into one activity per sport (A4.5).
4. Per activity, **validate** (`app.domain.streams.validate`). A file that is
   systemically broken is quarantined with its reason, not repaired.
5. Per activity, **overlap dedup**: a time range covering more than
   ``INGEST__OVERLAP_THRESHOLD`` of an existing session is the same ride
   arriving from a second source, and the athlete rules on it.
6. **File the original** under ``data/originals/YYYY/MM/<hash>.<ext>`` — once
   per file, however many activities it holds, never modified afterwards and
   **never deleted**. It is what a re-ingest would read, so it outranks every
   derived artefact in this system.
7. **Session + recording rows**, then `resample` + `clean`, then the parquet
   frame and one row per repair.

**Quarantine is the catch-all.** Any failure this module did not anticipate
still ends with the file moved somewhere the athlete can find it and a record
saying what went wrong, because the one unrecoverable outcome is a file that
was deleted by something that then crashed.

**File I/O here is synchronous, on the event loop.** Hashing a megabyte,
moving it and writing a parquet frame is tens of milliseconds; this is a
single-user application with one athlete's rides arriving a handful at a time,
and an async filesystem layer would buy latency nobody is waiting on at the
cost of a second way for every path in this module to fail. It is a decision,
not an oversight — revisit it if bulk backfill ever runs here (D5's reasoning,
one layer down).
"""

import datetime as dt
import hashlib
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.activity import (
    IngestOutcome,
    QuarantineReason,
    QuarantineStatus,
    RecordingKind,
    classify_discipline,
    session_date,
    timezone_label,
)
from app.domain.actor import Actor
from app.domain.streams import (
    Anomaly,
    ParsedActivity,
    ResampleResult,
    StreamChannel,
    channels_present,
    clean,
    resample,
    validate,
)
from app.ingest.parquet import stream_path, write_streams
from app.ingest.parsers import UnreadableFileError, extension_of, parse
from app.persistence.activity import (
    RecordingRepository,
    RecordingRow,
    SessionRepository,
    SessionRow,
    StreamAnomalyRow,
)
from app.persistence.audit import AuditRepository
from app.persistence.db import commit
from app.persistence.ingest_log import (
    MAX_DETAIL_LENGTH,
    MAX_FILENAME_LENGTH,
    IngestEventRepository,
    QuarantineRecordRow,
    QuarantineRepository,
)

logger = get_logger(__name__)

#: `entity_type` written on this module's audit rows.
SESSION_ENTITY = "session"
QUARANTINE_ENTITY = "quarantine_record"

#: Bytes read per hashing round.
HASH_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What the pipeline did with one file.

    Args:
        filename: The name the file arrived under.
        file_hash: Its sha256, or ``None`` when it could not even be read.
        outcome: ``ingested`` when at least one session was created,
            ``duplicate_file`` when the hash was already known,
            ``quarantined`` when every activity was refused, ``error`` when the
            pipeline itself failed (the file is still quarantined).
        detail: One sentence for the log and for the upload response.
        session_ids: Sessions created — or, for a duplicate, the sessions the
            file was already ingested as.
        quarantine_ids: Quarantine records created.
    """

    filename: str
    file_hash: str | None
    outcome: IngestOutcome
    detail: str | None = None
    session_ids: tuple[uuid.UUID, ...] = ()
    quarantine_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestPaths:
    """Where the runtime data tree puts each kind of file."""

    inbox: Path
    originals: Path
    quarantine: Path
    streams: Path

    @classmethod
    def from_settings(cls) -> Self:
        """Read the tree's root from configuration (``DATA__ROOT``)."""
        root = get_settings().data.root
        return cls(
            inbox=root / "inbox",
            originals=root / "originals",
            quarantine=root / "quarantine",
            streams=root / "streams",
        )

    def original_for(self, file_hash: str, ext: str, when: dt.datetime) -> Path:
        """``originals/YYYY/MM/<hash>.<ext>`` for a file starting at ``when``."""
        return self.originals / f"{when:%Y}" / f"{when:%m}" / f"{file_hash}.{ext}"

    def quarantine_for(self, file_hash: str, ext: str) -> Path:
        """Where a refused file is kept until the athlete rules on it."""
        return self.quarantine / f"{file_hash}.{ext}"

    def is_original(self, path: Path) -> bool:
        """Whether ``path`` is inside the immutable originals tree."""
        return self.originals.resolve() in path.resolve().parents


@dataclass(slots=True)
class _Plan:
    """One activity's verdict, decided before anything is written."""

    activity: ParsedActivity
    reason: QuarantineReason | None = None
    detail: str | None = None
    suspected_session_id: uuid.UUID | None = None
    existing_session_id: uuid.UUID | None = None

    @property
    def ingestible(self) -> bool:
        """Whether this activity is to become a session."""
        return self.reason is None and self.existing_session_id is None


@dataclass(slots=True)
class _Placement:
    """Where the file ended up, and what the rows should say about it."""

    path: Path
    sessions: list[uuid.UUID] = field(default_factory=list)
    quarantines: list[uuid.UUID] = field(default_factory=list)


@dataclass(slots=True)
class _Located:
    """Where the file is *now*, shared with the catch-all.

    The pipeline moves the file part-way through, so "the path we were handed"
    stops being true at that point. The rescue path needs the current one — it
    has a file to keep — and this is how it learns it without the happy path
    having to return something on the way out.
    """

    path: Path


class IngestPipeline:
    """Turns one device file into sessions, or into a quarantine record."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        sessions: SessionRepository,
        recordings: RecordingRepository,
        quarantine: QuarantineRepository,
        events: IngestEventRepository,
        audit: AuditRepository,
        paths: IngestPaths,
        overlap_threshold: float,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._recordings = recordings
        self._quarantine = quarantine
        self._events = events
        self._audit = audit
        self._paths = paths
        self._overlap_threshold = overlap_threshold

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the pipeline and its repositories to one database session."""
        return cls(
            session,
            sessions=SessionRepository(session),
            recordings=RecordingRepository(session),
            quarantine=QuarantineRepository(session),
            events=IngestEventRepository(session),
            audit=AuditRepository(session),
            paths=IngestPaths.from_settings(),
            overlap_threshold=get_settings().ingest.overlap_threshold,
        )

    @property
    def paths(self) -> IngestPaths:
        """The runtime data tree this pipeline writes into."""
        return self._paths

    async def ingest_file(
        self,
        path: Path,
        *,
        actor: Actor,
        filename: str | None = None,
        reingest: bool = False,
    ) -> IngestReport:
        """Ingest one file, committing whatever it managed to do.

        Args:
            path: The file to read. It is *moved* — into ``originals/`` when
                something was ingested, into ``quarantine/`` when nothing was
                — unless it is already an original, which is never moved.
            actor: Who is credited on the audit rows: the athlete for an
                upload, `Actor.system` for the watched folder.
            filename: The name to log, when ``path`` is not it (an upload is
                written into the inbox under a sanitised name).
            reingest: Set for B-4's *reject*, where the athlete has ruled that
                a quarantined file is **not** a duplicate. It waives both
                duplicate checks the decision overrules — the file-level hash
                one and the overlap one — and nothing else. Per-activity
                dedup on ``(hash, sport_index)`` still applies, so a reject
                cannot produce a second copy of an activity that is already a
                session.

        Returns:
            What happened. The pipeline does not raise for a bad file: a file
            it cannot use is a quarantine record, which is a result.
        """
        name = (filename or path.name)[:MAX_FILENAME_LENGTH]
        located = _Located(path)
        try:
            return await self._ingest(
                path, name=name, actor=actor, reingest=reingest, located=located
            )
        except Exception as exc:  # noqa: BLE001 — quarantine is the catch-all
            logger.exception("ingest_failed", filename=name, path=str(located.path))
            return await self._rescue(located.path, name=name, actor=actor, error=exc)

    # --- the pipeline proper -------------------------------------------------

    async def _ingest(
        self, path: Path, *, name: str, actor: Actor, reingest: bool, located: _Located
    ) -> IngestReport:
        """Steps 1-7 of the module docstring, in one transaction."""
        file_hash = _sha256(path)
        extension = extension_of(path)

        if not reingest:
            duplicate = await self._known_file(path, name=name, file_hash=file_hash)
            if duplicate is not None:
                return duplicate

        try:
            activities = parse(path)
        except UnreadableFileError as exc:
            return await self._refuse_whole_file(
                path,
                name=name,
                actor=actor,
                file_hash=file_hash,
                extension=extension,
                reason=QuarantineReason.UNREADABLE_FILE,
                detail=str(exc),
                located=located,
            )

        plans = [
            await self._classify(activity, file_hash, overlap=not reingest)
            for activity in activities
        ]
        placement = _Placement(
            path=self._place(
                path,
                destination=self._destination(plans, file_hash, extension),
            )
        )
        located.path = placement.path

        for plan in plans:
            if plan.existing_session_id is not None:
                placement.sessions.append(plan.existing_session_id)
            elif plan.reason is not None:
                placement.quarantines.append(
                    await self._quarantine_activity(
                        plan,
                        name=name,
                        file_hash=file_hash,
                        path=placement.path,
                        actor=actor,
                    )
                )
            else:
                placement.sessions.append(
                    await self._ingest_activity(
                        plan.activity,
                        file_hash=file_hash,
                        extension=extension,
                        original=placement.path,
                        actor=actor,
                    )
                )

        report = _report(name, file_hash, placement)
        await self._events.record(
            filename=name,
            file_hash=file_hash,
            outcome=report.outcome,
            detail=report.detail,
            session_id=report.session_ids[0] if report.session_ids else None,
        )
        await commit(self._session)
        return report

    async def _known_file(
        self, path: Path, *, name: str, file_hash: str
    ) -> IngestReport | None:
        """Answer the file-level dedup check, or ``None`` to carry on.

        Both halves of it: a hash already ingested, and a hash sitting
        unresolved in quarantine. Neither may be parsed again — the first
        would duplicate a session, the second would duplicate the queue entry
        the athlete has not yet dealt with.
        """
        known = await self._recordings.by_hash(file_hash)
        pending = await self._quarantine.pending_for_hash(file_hash)
        if not known and pending is None:
            return None

        detail = (
            f"already ingested as {len(known)} recording(s) of this file"
            if known
            else "already waiting in quarantine for a decision"
        )
        self._discard_inbox_copy(path)
        sessions = tuple(dict.fromkeys(row.session_id for row in known))
        await self._events.record(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.DUPLICATE_FILE,
            detail=detail,
            session_id=sessions[0] if sessions else None,
        )
        await commit(self._session)
        return IngestReport(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.DUPLICATE_FILE,
            detail=detail,
            session_ids=sessions,
            quarantine_ids=(pending.id,) if pending is not None else (),
        )

    async def _classify(
        self, activity: ParsedActivity, file_hash: str, *, overlap: bool
    ) -> _Plan:
        """Decide one activity's fate without writing anything.

        Two passes are needed because where the *file* goes depends on whether
        **any** activity in it is ingestible, and the rows that name that path
        cannot be written before it is known.
        """
        already = await self._recordings.by_dedup_key(
            file_hash, activity.file_sport_index
        )
        if already is not None:
            return _Plan(activity, existing_session_id=already.session_id)

        verdict = validate(activity)
        if verdict is not None:
            return _Plan(activity, reason=verdict.reason, detail=verdict.detail)

        if overlap:
            twin = await self._overlapping_session(activity)
            if twin is not None:
                twin_session, fraction = twin
                return _Plan(
                    activity,
                    reason=QuarantineReason.SUSPECTED_DUPLICATE,
                    detail=(
                        f"{fraction:.0%} of this activity's time range overlaps the "
                        f"session already recorded on {twin_session.local_date}; "
                        "confirm to discard it, or reject to keep both"
                    ),
                    suspected_session_id=twin_session.id,
                )
        return _Plan(activity)

    async def _overlapping_session(
        self, activity: ParsedActivity
    ) -> tuple[SessionRow, float] | None:
        """The existing session this activity is probably a second copy of.

        The measure is the shared seconds over the **longer** of the two
        ranges. Two exports of one ride differ by a few seconds at each end
        and score near 1; a short recording that merely happens to sit inside
        a long ride scores near 0, which is right — it is not a copy of it.
        """
        start = activity.start_time
        end = activity.samples[-1].t
        best: tuple[SessionRow, float] | None = None
        for candidate in await self._sessions.overlapping(start, end):
            shared = (
                min(end, candidate.end_time) - max(start, candidate.start_time)
            ).total_seconds()
            longest = max(
                (end - start).total_seconds(),
                (candidate.end_time - candidate.start_time).total_seconds(),
            )
            fraction = shared / longest if longest > 0 else 0.0
            if fraction > self._overlap_threshold and (
                best is None or fraction > best[1]
            ):
                best = (candidate, fraction)
        return best

    def _destination(
        self, plans: Sequence[_Plan], file_hash: str, extension: str
    ) -> Path:
        """Where the file belongs once every activity has a verdict.

        One file, one destination, whatever its activity count: the original
        is the bytes, and the bytes are one thing. A file with an ingestible
        activity becomes an original even if another of its activities is
        quarantined — the quarantine record then points at the original, and
        `confirm` refuses to delete anything from that tree.
        """
        ingestible = [plan for plan in plans if plan.ingestible]
        if ingestible:
            return self._paths.original_for(
                file_hash, extension, ingestible[0].activity.start_time
            )
        return self._paths.quarantine_for(file_hash, extension)

    def _place(self, path: Path, *, destination: Path) -> Path:
        """Move the file to its destination, and never lose it doing so.

        A destination that already exists is left alone: identical hash means
        identical bytes, so the file already there *is* this file — the state
        a crash between the move and the commit leaves behind, and re-running
        must converge on it rather than fail.

        A file that is **already an original stays where it is**, whatever the
        computed destination says. The reject path re-reads an original, and a
        second sport within it can start in a different month from the first;
        moving the file would rewrite a path other rows already record, and
        ``originals/`` is the one tree nothing rewrites.
        """
        if self._paths.is_original(path):
            return path
        if path.resolve() == destination.resolve():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._discard_inbox_copy(path)
            return destination
        shutil.move(str(path), str(destination))
        return destination

    def _discard_inbox_copy(self, path: Path) -> None:
        """Remove a redundant copy — but only from the inbox.

        The inbox is a drop point and its files are transient. Everything else
        is somebody's record: **nothing under ``originals/`` is ever deleted**,
        and a quarantined file is the athlete's to rule on.
        """
        if not path.exists() or self._paths.is_original(path):
            return
        if self._paths.inbox.resolve() not in path.resolve().parents:
            return
        path.unlink()

    # --- writing -------------------------------------------------------------

    async def _ingest_activity(
        self,
        activity: ParsedActivity,
        *,
        file_hash: str,
        extension: str,
        original: Path,
        actor: Actor,
    ) -> uuid.UUID:
        """Create the session, the recording, the parquet frame and the repairs."""
        resampled = resample(activity.samples)
        cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)
        channels = channels_present(activity.samples)
        discipline, classification = classify_discipline(
            sport=activity.sport,
            has_power=StreamChannel.POWER in channels,
            has_speed=StreamChannel.SPEED in channels,
            has_gps=StreamChannel.LAT in channels,
            duration_s=resampled.elapsed_time_s,
        )
        tz = timezone_label(activity.local_offset)
        session_row = await self._sessions.add(
            SessionRow(
                start_time=activity.start_time,
                end_time=activity.start_time
                + dt.timedelta(seconds=resampled.elapsed_time_s),
                timezone=tz,
                local_date=session_date(activity.start_time, tz),
                discipline=discipline,
                classification_source=classification,
                recording_kind=RecordingKind.DEVICE,
            )
        )
        recording = await self._recordings.add(
            _recording_row(
                session_id=session_row.id,
                activity=activity,
                resampled=resampled,
                file_hash=file_hash,
                extension=extension,
                original=original,
                channels=channels,
            )
        )
        write_streams(
            stream_path(self._paths.streams, recording.id),
            frame=resampled.frame,
            cleaned=cleaned,
            sources=_source_labels(activity),
        )
        await self._recordings.add_anomalies(
            [_anomaly_row(recording.id, anomaly) for anomaly in cleaned.anomalies]
        )
        await self._audit.record(
            actor=actor,
            action="session.ingested",
            entity_type=SESSION_ENTITY,
            entity_id=session_row.id,
            payload={
                "file_hash": file_hash,
                "file_sport_index": activity.file_sport_index,
                "discipline": discipline.value,
                "classification_source": classification.value,
                "local_date": session_row.local_date.isoformat(),
                "recording_time_s": resampled.recording_time_s,
                "original_path": str(original),
            },
        )
        return session_row.id

    async def _quarantine_activity(
        self,
        plan: _Plan,
        *,
        name: str,
        file_hash: str,
        path: Path,
        actor: Actor,
    ) -> uuid.UUID:
        """Record one refused activity and where its file was kept."""
        return await self._record_quarantine(
            name=name,
            file_hash=file_hash,
            file_sport_index=plan.activity.file_sport_index,
            reason=plan.reason or QuarantineReason.UNREADABLE_FILE,
            detail=plan.detail,
            path=path,
            suspected_session_id=plan.suspected_session_id,
            actor=actor,
        )

    async def _refuse_whole_file(
        self,
        path: Path,
        *,
        name: str,
        actor: Actor,
        file_hash: str,
        extension: str,
        reason: QuarantineReason,
        detail: str,
        located: _Located,
    ) -> IngestReport:
        """Quarantine a file nothing could be parsed out of."""
        kept = self._place(
            path, destination=self._paths.quarantine_for(file_hash, extension)
        )
        located.path = kept
        record_id = await self._record_quarantine(
            name=name,
            file_hash=file_hash,
            file_sport_index=None,
            reason=reason,
            detail=detail,
            path=kept,
            suspected_session_id=None,
            actor=actor,
        )
        await self._events.record(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.QUARANTINED,
            detail=detail[:MAX_DETAIL_LENGTH],
        )
        await commit(self._session)
        return IngestReport(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.QUARANTINED,
            detail=detail,
            quarantine_ids=(record_id,),
        )

    async def _record_quarantine(
        self,
        *,
        name: str,
        file_hash: str,
        file_sport_index: int | None,
        reason: QuarantineReason,
        detail: str | None,
        path: Path,
        suspected_session_id: uuid.UUID | None,
        actor: Actor,
    ) -> uuid.UUID:
        """Append one quarantine record and audit it."""
        row = await self._quarantine.add(
            QuarantineRecordRow(
                original_filename=name,
                file_hash=file_hash,
                file_sport_index=file_sport_index,
                reason=reason,
                detail=(detail or "")[:MAX_DETAIL_LENGTH] or None,
                quarantined_path=str(path),
                status=QuarantineStatus.PENDING,
                suspected_session_id=suspected_session_id,
            )
        )
        await self._audit.record(
            actor=actor,
            action="ingest.quarantined",
            entity_type=QUARANTINE_ENTITY,
            entity_id=row.id,
            payload={
                "filename": name,
                "file_hash": file_hash,
                "file_sport_index": file_sport_index,
                "reason": reason.value,
                "quarantined_path": str(path),
                "suspected_session_id": (
                    str(suspected_session_id) if suspected_session_id else None
                ),
            },
        )
        return row.id

    async def _rescue(
        self, path: Path, *, name: str, actor: Actor, error: Exception
    ) -> IngestReport:
        """Last resort: keep the file, say what broke, do not raise.

        The transaction is rolled back first — a failure part-way through
        leaves the session unusable — and the record is written on a fresh
        one. If even this fails the exception propagates: at that point the
        database is unreachable, and inventing a success would be worse than
        letting the scheduler log it.
        """
        await self._session.rollback()
        detail = f"the pipeline failed while ingesting this file: {error}"
        file_hash: str | None = None
        quarantined: tuple[uuid.UUID, ...] = ()
        try:
            file_hash = _sha256(path)
        except OSError:
            file_hash = None
        if file_hash is not None:
            kept = self._place(
                path,
                destination=self._paths.quarantine_for(file_hash, extension_of(path)),
            )
            quarantined = (
                await self._record_quarantine(
                    name=name,
                    file_hash=file_hash,
                    file_sport_index=None,
                    reason=QuarantineReason.UNREADABLE_FILE,
                    detail=detail,
                    path=kept,
                    suspected_session_id=None,
                    actor=actor,
                ),
            )
        await self._events.record(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.ERROR,
            detail=detail[:MAX_DETAIL_LENGTH],
        )
        await commit(self._session)
        return IngestReport(
            filename=name,
            file_hash=file_hash,
            outcome=IngestOutcome.ERROR,
            detail=detail,
            quarantine_ids=quarantined,
        )


# --- row builders -------------------------------------------------------------


def _recording_row(
    *,
    session_id: uuid.UUID,
    activity: ParsedActivity,
    resampled: ResampleResult,
    file_hash: str,
    extension: str,
    original: Path,
    channels: frozenset[StreamChannel],
) -> RecordingRow:
    """The recording row for one ingested activity, A4.3 and A4.4 included."""
    return RecordingRow(
        session_id=session_id,
        file_hash=file_hash,
        file_sport_index=activity.file_sport_index,
        original_path=str(original),
        original_ext=extension,
        sport=activity.sport,
        elapsed_time_s=resampled.elapsed_time_s,
        recording_time_s=resampled.recording_time_s,
        recording_stops=[[start, end] for start, end in resampled.recording_stops],
        median_time_delta_s=resampled.median_time_delta_s,
        moving_time_s=resampled.moving_time_s,
        power_source_candidates=list(activity.power_source_candidates),
        power_source=activity.power_source,
        power_source_rule=activity.power_source_rule,
        hr_source_candidates=list(activity.hr_source_candidates),
        hr_source=activity.hr_source,
        hr_source_rule=activity.hr_source_rule,
        channels=sorted(channel.value for channel in channels),
    )


def _anomaly_row(recording_id: uuid.UUID, anomaly: Anomaly) -> StreamAnomalyRow:
    """One repaired (or explicitly unrepaired) region, as a row."""
    return StreamAnomalyRow(
        recording_id=recording_id,
        channel=anomaly.channel,
        start_index=anomaly.start_index,
        end_index=anomaly.end_index,
        kind=anomaly.kind,
        substituted_value=anomaly.substituted_value,
    )


def _source_labels(activity: ParsedActivity) -> dict[StreamChannel, str]:
    """The per-channel source labels written into the parquet metadata."""
    return {
        channel: source
        for channel, source in (
            (StreamChannel.POWER, activity.power_source),
            (StreamChannel.HR, activity.hr_source),
        )
        if source is not None
    }


def _report(name: str, file_hash: str, placement: _Placement) -> IngestReport:
    """Summarise what a file produced.

    A file with one ingested activity and one quarantined one reports
    ``ingested``: something reached the calendar, and the quarantine record is
    on the report as well as in the queue.
    """
    ingested = bool(placement.sessions)
    return IngestReport(
        filename=name,
        file_hash=file_hash,
        outcome=IngestOutcome.INGESTED if ingested else IngestOutcome.QUARANTINED,
        detail=(
            f"{len(placement.sessions)} session(s) ingested, "
            f"{len(placement.quarantines)} quarantined"
        ),
        session_ids=tuple(placement.sessions),
        quarantine_ids=tuple(placement.quarantines),
    )


def _sha256(path: Path) -> str:
    """The file's sha256, hex — its identity for every dedup check."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
