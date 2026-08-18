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
5. Per activity, **prepare**: `resample` + `clean` + the discipline
   classification. Pure, and done before anything is written, because the
   overlap check below needs the discipline and the classification needs the
   *recording* time the resample reports.
6. Per activity, **overlap dedup**: a time range covering more than
   ``INGEST__OVERLAP_THRESHOLD`` of an existing session **of the same
   discipline** is the same ride arriving from a second source, and the athlete
   rules on it.
7. **File the original** under ``data/originals/YYYY/MM/<hash>.<ext>`` — once
   per file, however many activities it holds, never modified afterwards and
   **never deleted**. It is what a re-ingest would read, so it outranks every
   derived artefact in this system.
8. **Session + recording rows**, then the parquet frame and one row per repair.
9. After the commit, and only then: **metrics**, and then **matching** (WP-6),
   which reads them. Both are per session inside their own ``try``, because the
   file and the rows are already durable and neither a metric failure nor a
   matching failure may un-ingest a ride.

**Quarantine is the catch-all.** Any failure this module did not anticipate
still ends with the file kept somewhere the athlete can find it and a record
saying what went wrong, because the one unrecoverable outcome is a file that
was deleted by something that then crashed.

**Nothing is moved before its rows are committed.** The file is *copied* to
its destination, the transaction commits, and only then is the inbox copy
removed (:meth:`IngestPipeline._place`). A crash anywhere in that window
leaves the arrival in the inbox for the next sweep, which converges on the
already-placed destination rather than starting again — the alternative,
moving first, loses the file entirely to a process that dies before COMMIT.

**The CPU-heavy work runs off the event loop.** Hashing, parsing, resampling,
cleaning and the parquet write go through `asyncio.to_thread`; every database
call stays on the loop, where the session lives. A season's backfill is 150
files at up to a second each, and a synchronous pass over them starves
`/health` (5 s timeout) long enough for the container to be declared unhealthy
and take Caddy and the frontend down with it. This is still one in-process
pipeline — no queue, no worker — it simply yields.
"""

import asyncio
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
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.domain.activity import (
    ClassificationSource,
    IngestOutcome,
    IngestSource,
    QuarantineReason,
    QuarantineStatus,
    RecordingKind,
    SessionDiscipline,
    classify_discipline,
    session_date,
    timezone_label,
)
from app.domain.actor import Actor
from app.domain.streams import (
    Anomaly,
    CleanResult,
    ParsedActivity,
    ResampleResult,
    StreamChannel,
    channels_present,
    clean,
    resample,
    validate,
)
from app.ingest.analysis import SessionAnalyser
from app.ingest.parquet import STREAMS_DIRNAME, stream_path, write_streams
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
from app.services.matching import MatchingService
from app.services.proposals import resolve_proposals_for_session

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
            ``duplicate_file`` when the hash — or every activity in it — was
            already known, ``quarantined`` when every activity was refused,
            ``error`` when the pipeline itself failed. The file survives an
            ``error`` either way: quarantined with the failure on it, or left
            where it was when the caller had already ruled on it.
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


def _located_in(path: Path) -> Sequence[Path]:
    """The resolved directories ``path`` sits under, without following it.

    Only the parent is resolved. Resolving the whole path would follow a
    symbolic link to wherever it points, so a link dropped in the inbox would
    read as living outside the inbox — and the guards that keep this module's
    deletes inside it would silently stop applying to exactly the entry that
    needs them.
    """
    return (path.parent.resolve() / path.name).parents


@dataclass(frozen=True, slots=True)
class FileOrigin:
    """Where a file came from, when it did not simply appear on this machine.

    Threaded through to the recording's ``source`` / ``external_id`` columns,
    which is the only place the answer survives: the *file* on disk is
    identical however it arrived, so a ride pulled from Dropbox and the same
    ride dropped in `data/inbox/` are byte-for-byte the same original. Without
    this, "is my Dropbox feed actually delivering?" has no answer that is not a
    guess from timestamps.

    ``None`` at the call site — the upload endpoint, the local sweep — is not
    a missing value. It is the statement that the file arrived here directly;
    see `app.domain.activity.IngestSource`.
    """

    source: IngestSource
    #: The provider's own id for the file, stable across a rename and a move.
    external_id: str | None = None


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
            streams=root / STREAMS_DIRNAME,
        )

    def original_for(self, file_hash: str, ext: str, when: dt.datetime) -> Path:
        """``originals/YYYY/MM/<hash>.<ext>`` for a file starting at ``when``."""
        return self.originals / f"{when:%Y}" / f"{when:%m}" / f"{file_hash}.{ext}"

    def quarantine_for(self, file_hash: str, ext: str) -> Path:
        """Where a refused file is kept until the athlete rules on it."""
        return self.quarantine / f"{file_hash}.{ext}"

    def is_original(self, path: Path) -> bool:
        """Whether ``path`` is inside the immutable originals tree."""
        return self.originals.resolve() in _located_in(path)

    def is_inbox(self, path: Path) -> bool:
        """Whether ``path`` is a drop in the watched folder."""
        return self.inbox.resolve() in _located_in(path)


@dataclass(frozen=True, slots=True)
class PreparedActivity:
    """Everything about one activity that no database or disk was needed for.

    Computed in one pass off the event loop (:func:`prepare`) and then used
    twice: the discipline decides which existing sessions the overlap check may
    compare against, and the frames become the rows and the parquet file. Doing
    it once is also why a plan carries it — resampling a four-hour ride twice
    is a second of CPU spent to recompute a value that cannot have changed.
    """

    resampled: ResampleResult
    cleaned: CleanResult
    channels: frozenset[StreamChannel]
    discipline: SessionDiscipline
    classification: ClassificationSource


@dataclass(slots=True)
class _Plan:
    """One activity's verdict, decided before anything is written."""

    activity: ParsedActivity
    reason: QuarantineReason | None = None
    detail: str | None = None
    suspected_session_id: uuid.UUID | None = None
    existing_session_id: uuid.UUID | None = None
    prepared: PreparedActivity | None = None

    @property
    def ingestible(self) -> bool:
        """Whether this activity is to become a session."""
        return self.reason is None and self.existing_session_id is None


@dataclass(slots=True)
class _Placement:
    """Where the file ended up, and what the rows should say about it.

    ``sessions`` is every session the file resolved to, in file order — the
    ones it created *and* the ones its activities were already ingested as.
    ``created`` is only the former, because "2 session(s) ingested" is a lie
    when both of them existed before this run (F11).
    """

    path: Path
    sessions: list[uuid.UUID] = field(default_factory=list)
    created: list[uuid.UUID] = field(default_factory=list)
    quarantines: list[uuid.UUID] = field(default_factory=list)


@dataclass(slots=True)
class _Located:
    """Where the file is *now*, shared with the catch-all.

    The pipeline files the bytes part-way through, so "the path we were
    handed" stops being the interesting one at that point. The rescue path
    needs the current one — it has a file to keep — and this is how it learns
    it without the happy path having to return something on the way out.
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
        origin: FileOrigin | None = None,
        reingest: bool = False,
        waive_implausible: bool = False,
        quarantine_on_failure: bool = True,
    ) -> IngestReport:
        """Ingest one file, committing whatever it managed to do.

        Args:
            path: The file to read. It is *copied* to its destination —
                ``originals/`` when something was ingested, ``quarantine/``
                when nothing was — and the inbox copy is removed once those
                rows are committed. A file that is already an original stays
                where it is, and a file outside the inbox is never deleted.
            actor: Who is credited on the audit rows: the athlete for an
                upload, `Actor.system` for the watched folder.
            filename: The name to log, when ``path`` is not it (an upload is
                written into the inbox under a sanitised name).
            origin: The connector this file came in over, when one did. Left
                unset the recording says nothing about its transport, which is
                what a local drop or an upload means.
            reingest: Set for B-4's *reject* of a suspected duplicate, where
                the athlete has ruled that the file is **not** one. It waives
                both duplicate checks the decision overrules — the file-level
                hash one and the overlap one — and nothing else. Per-activity
                dedup on ``(hash, sport_index)`` still applies, so a reject
                cannot produce a second copy of an activity that is already a
                session.
            waive_implausible: Set for B-4's *reject* of an
                ``implausible_channel`` verdict. It waives that one check and
                no other: the cleaner nulls every value it cannot trust, so
                what is ingested carries no out-of-range reading (D-F13).
            quarantine_on_failure: Whether an unanticipated failure should
                leave a **new pending** quarantine record. The athlete-driven
                reject path sets it false: it has just resolved the record for
                this file, and a second pending one for the same bytes is a
                phantom queue entry rather than a decision anybody can take.

        Returns:
            What happened. The pipeline does not raise for a bad file: a file
            it cannot use is a quarantine record, which is a result.
        """
        name = (filename or path.name)[:MAX_FILENAME_LENGTH]
        source = await asyncio.to_thread(self._materialise, path)
        if source is None:
            return await self._dead_link(name)
        located = _Located(source)
        try:
            report = await self._ingest(
                source,
                name=name,
                actor=actor,
                origin=origin,
                reingest=reingest,
                waive_implausible=waive_implausible,
                located=located,
            )
        except Exception as exc:  # noqa: BLE001 — quarantine is the catch-all
            logger.exception("ingest_failed", filename=name, path=str(located.path))
            report = await self._rescue(
                located.path,
                name=name,
                actor=actor,
                error=exc,
                quarantine=quarantine_on_failure,
            )
        # Both branches above have committed. Until one of them did, the inbox
        # copy was the only guarantee that a crash could not lose the file.
        await asyncio.to_thread(self._discard_inbox_copy, source)
        return report

    # --- the pipeline proper -------------------------------------------------

    async def _ingest(
        self,
        path: Path,
        *,
        name: str,
        actor: Actor,
        origin: FileOrigin | None,
        reingest: bool,
        waive_implausible: bool,
        located: _Located,
    ) -> IngestReport:
        """Steps 1-8 of the module docstring, in one transaction."""
        file_hash = await asyncio.to_thread(_sha256, path)
        extension = extension_of(path)

        if not reingest:
            duplicate = await self._known_file(path, name=name, file_hash=file_hash)
            if duplicate is not None:
                return duplicate

        try:
            activities = await asyncio.to_thread(parse, path)
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
            await self._classify(
                activity,
                file_hash,
                overlap=not reingest,
                waive_implausible=waive_implausible,
            )
            for activity in activities
        ]
        placement = _Placement(
            path=await asyncio.to_thread(
                self._place,
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
                try:
                    created = await self._ingest_activity(
                        plan,
                        file_hash=file_hash,
                        extension=extension,
                        original=placement.path,
                        origin=origin,
                        actor=actor,
                    )
                except ConflictError:
                    # The dedup key is taken: another writer ingested this file
                    # between our read and our insert. The session has been
                    # rolled back, so there is nothing of this run left to
                    # report but the fact that the file is already here.
                    return await self._lost_the_race(name=name, file_hash=file_hash)
                placement.sessions.append(created)
                placement.created.append(created)

        report = _report(name, file_hash, placement)
        await self._events.record(
            filename=name,
            file_hash=file_hash,
            outcome=report.outcome,
            detail=report.detail,
            session_id=report.session_ids[0] if report.session_ids else None,
        )
        await commit(self._session)
        await self._compute_metrics(placement.created, actor=actor)
        # Matching before the proposal sweep: a link is one of the two things
        # that resolve a pending proposal, and it does not exist until
        # the matcher has run.
        await self._match(placement.created, actor=actor)
        await self._resolve_proposals(placement.created, actor=actor)
        return report

    async def _resolve_proposals(
        self, session_ids: Sequence[uuid.UUID], *, actor: Actor
    ) -> None:
        """Close pending plan-change proposals each new session made moot (WP-8.2).

        The athlete rode. A proposal still asking what to do with that day in
        that discipline is not a question any more, and leaving it in the inbox
        would let an accept rewrite the plan the ride is about to be scored
        against. Per session inside its own ``try``, for the reason the metric
        and matching passes are: the session, the recording and the file are
        durable already, and a failure here leaves a stale inbox row that the
        hourly expiry sweep clears anyway.
        """
        for session_id in session_ids:
            row = await self._sessions.get(session_id)
            if row is None:  # pragma: no cover — committed moments ago
                continue
            try:
                await resolve_proposals_for_session(
                    self._session,
                    actor=actor,
                    local_date=row.local_date,
                    discipline=row.discipline,
                )
            except Exception:  # noqa: BLE001 — see the docstring
                logger.exception(
                    "proposal_resolution_failed", session_id=str(session_id)
                )
                await self._session.rollback()

    async def _compute_metrics(
        self, session_ids: Sequence[uuid.UUID], *, actor: Actor
    ) -> None:
        """Compute the metric artefact of each session this run created.

        **After** the commit, and per session inside its own ``try``. Both
        deliberately: the session, the recording, the parquet file and the
        anomaly rows are already durable, so a metric failure leaves an
        ingested ride with no numbers rather than un-ingesting the file — and
        the file is the irreplaceable half (invariant 8). The artefact is
        recoverable by hand from `POST /sessions/{id}/metrics/recompute`; the
        original, once refused, is a re-import the athlete has to notice.
        """
        for session_id in session_ids:
            try:
                await SessionAnalyser.from_session(self._session).compute(
                    session_id, actor=actor
                )
            except Exception:  # noqa: BLE001 — see the docstring
                logger.exception("metrics_failed", session_id=str(session_id))
                await self._session.rollback()

    async def _match(self, session_ids: Sequence[uuid.UUID], *, actor: Actor) -> None:
        """Look for the planned session each new session answers to (WP-6).

        **After the metrics**, because the intensity and structure terms read
        the metric artefact — matching a session whose numbers do not exist yet
        would score every ride on duration alone. Per session inside its own
        ``try``, for the same reason the metric pass is: the session, the
        recording and the file are durable already, and a matching failure
        leaves an ingested ride nobody has proposed a link for, which
        `POST /sessions/{id}/rematch` recovers by hand.
        """
        for session_id in session_ids:
            try:
                await MatchingService.from_session(self._session).match_session(
                    session_id, actor=actor
                )
            except Exception:  # noqa: BLE001 — see the docstring
                logger.exception("matching_failed", session_id=str(session_id))
                await self._session.rollback()

    async def _known_file(
        self, path: Path, *, name: str, file_hash: str
    ) -> IngestReport | None:
        """Answer the file-level dedup check, or ``None`` to carry on.

        Both halves of it: a hash already ingested, and a hash sitting
        unresolved in quarantine. Neither may be parsed again — the first
        would duplicate a session, the second would duplicate the queue entry
        the athlete has not yet dealt with.

        **A row is not a file.** Before the new arrival is treated as
        redundant, the copy the row names is checked for existence: if it is
        gone — deleted by hand, lost by a restore — the arrival *becomes* it
        and the log says so. Discarding the last copy of the bytes on the
        strength of a row pointing at nothing is the one outcome this module
        exists to prevent.
        """
        known = await self._recordings.by_hash(file_hash)
        pending = await self._quarantine.pending_for_hash(file_hash)
        if not known and pending is None:
            return None

        canonical = _canonical_copy(known, pending)
        restored = await asyncio.to_thread(self._restore_canonical, path, canonical)
        detail = (
            f"already ingested as {len(known)} recording(s) of this file"
            if known
            else "already waiting in quarantine for a decision"
        )
        if restored:
            detail = (
                f"{detail}; the recorded copy was missing and this file restored it"
            )
            logger.info(
                "canonical_copy_restored", file_hash=file_hash, path=str(canonical)
            )
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
        self,
        activity: ParsedActivity,
        file_hash: str,
        *,
        overlap: bool,
        waive_implausible: bool = False,
    ) -> _Plan:
        """Decide one activity's fate without writing anything.

        Two passes are needed because where the *file* goes depends on whether
        **any** activity in it is ingestible, and the rows that name that path
        cannot be written before it is known.

        The order is the domain's: `validate` refuses a file before `resample`
        is asked to make sense of it. Only then is the activity prepared — and
        it has to be prepared *here*, because the overlap check below compares
        disciplines and the discipline is not known until the resample has
        reported how much of the file was actually recorded.
        """
        already = await self._recordings.by_dedup_key(
            file_hash, activity.file_sport_index
        )
        if already is not None:
            return _Plan(activity, existing_session_id=already.session_id)

        verdict = validate(activity)
        if verdict is not None and not (
            waive_implausible and verdict.reason is QuarantineReason.IMPLAUSIBLE_CHANNEL
        ):
            return _Plan(activity, reason=verdict.reason, detail=verdict.detail)

        prepared = await asyncio.to_thread(prepare, activity)
        if overlap:
            twin = await self._overlapping_session(
                activity, discipline=prepared.discipline
            )
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
                    prepared=prepared,
                )
        return _Plan(activity, prepared=prepared)

    async def _overlapping_session(
        self, activity: ParsedActivity, *, discipline: SessionDiscipline
    ) -> tuple[SessionRow, float] | None:
        """The existing session this activity is probably a second copy of.

        The measure is the shared seconds over the **longer** of the two
        ranges. Two exports of one ride differ by a few seconds at each end
        and score near 1; a short recording that merely happens to sit inside
        a long ride scores near 0, which is right — it is not a copy of it.

        Only sessions of the **same discipline** are candidates. A ride and a
        gym session share a wall clock all the time — the athlete lifts, then
        rides — and "70 % of this ride overlaps your strength session" names a
        session the file cannot be a second copy of, which is a decision the
        athlete is being asked to take for no reason.
        """
        start = activity.start_time
        end = activity.samples[-1].t
        best: tuple[SessionRow, float] | None = None
        for candidate in await self._sessions.overlapping(start, end):
            if candidate.discipline is not discipline:
                continue
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
        """**Copy** the file to its destination, and never lose it doing so.

        Copy rather than move, and the arrival is unlinked only after the
        transaction that names the destination has committed (see the module
        docstring). Until then the inbox copy is what a crash leaves behind,
        and a crashed run is one the next sweep repeats rather than a file
        sitting in ``originals/`` that no row mentions and nothing will ever
        look at again.

        The copy itself lands under a temporary name and is renamed into place,
        so a destination that exists is a *complete* file rather than however
        much of one a killed process managed to write.

        A destination that already exists is left alone: identical hash means
        identical bytes, so the file already there *is* this file — the state a
        crash before the commit leaves behind, and re-running must converge on
        it rather than fail.

        A file that is **already an original stays where it is**, whatever the
        computed destination says. The reject path re-reads an original, and a
        second sport within it can start in a different month from the first;
        moving the file would rewrite a path other rows already record, and
        ``originals/`` is the one tree nothing rewrites.

        ``copy2`` follows a symbolic link and copies the bytes behind it, so
        the destination is always a regular file however the arrival got there
        — ``originals/`` holding a pointer would make a backup of that tree
        useless (invariant 8).
        """
        if self._paths.is_original(path):
            return path
        if path.resolve() == destination.resolve():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            staging = destination.with_name(f".{destination.name}.incoming")
            shutil.copy2(path, staging)
            staging.replace(destination)
        return destination

    def _restore_canonical(self, arrival: Path, canonical: Path | None) -> bool:
        """Put the bytes back where a row says they are, if they are not there.

        Returns whether anything was restored. Same identity, same bytes: the
        arrival is a byte-for-byte copy of what the row describes (it was found
        by hash), so copying it to the recorded path makes the row true again
        rather than inventing a file. The arrival is left alone here — the
        inbox copy is dropped after the commit, like every other placement.
        """
        if canonical is None or arrival.resolve() == canonical.resolve():
            return False
        if canonical.exists():
            return False
        canonical.parent.mkdir(parents=True, exist_ok=True)
        staging = canonical.with_name(f".{canonical.name}.incoming")
        shutil.copy2(arrival, staging)
        staging.replace(canonical)
        return True

    def _materialise(self, path: Path) -> Path | None:
        """Replace a symbolic link dropped in the inbox with the bytes it names.

        The inbox is a transient drop point, so a link in it is replaced by a
        real file of the same name and the pipeline carries on. Everything
        downstream then holds an ordinary file: the link would otherwise be
        re-hashed on every sweep for ever, because the guards that discard an
        inbox copy resolve the path and find themselves outside the inbox.

        Returns the path to read, or ``None`` when the link dangles — its
        target is unreadable, there is nothing to ingest, and the link is
        removed rather than left to fail identically every thirty seconds.
        """
        if not path.is_symlink() or not self._paths.is_inbox(path):
            return path
        staging = path.with_name(f".{path.name}.materialising")
        try:
            shutil.copyfile(path, staging)  # follows the link, streams the bytes
        except OSError:
            logger.warning("inbox_dead_link_removed", path=str(path))
            staging.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            return None
        staging.replace(path)
        logger.info("inbox_link_materialised", path=str(path))
        return path

    def _discard_inbox_copy(self, path: Path) -> None:
        """Remove a redundant copy — but only from the inbox.

        The inbox is a drop point and its files are transient. Everything else
        is somebody's record: **nothing under ``originals/`` is ever deleted**,
        and a quarantined file is the athlete's to rule on.

        The containment test never resolves the file itself, only the directory
        holding it: a symbolic link resolves to wherever it points, which is
        how a link in the inbox used to escape this guard entirely.
        """
        if not self._paths.is_inbox(path) or self._paths.is_original(path):
            return
        path.unlink(missing_ok=True)

    # --- writing -------------------------------------------------------------

    async def _ingest_activity(
        self,
        plan: _Plan,
        *,
        file_hash: str,
        extension: str,
        original: Path,
        origin: FileOrigin | None,
        actor: Actor,
    ) -> uuid.UUID:
        """Create the session, the recording, the parquet frame and the repairs.

        Raises:
            ConflictError: When the dedup key ``(file_hash, file_sport_index)``
                is already taken — the pre-check lost a race, and the caller
                reports the winner rather than a failure.
        """
        activity = plan.activity
        prepared = plan.prepared or await asyncio.to_thread(prepare, activity)
        resampled = prepared.resampled
        cleaned = prepared.cleaned
        channels = prepared.channels
        discipline, classification = prepared.discipline, prepared.classification
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
                origin=origin,
                channels=channels,
            )
        )
        await asyncio.to_thread(
            write_streams,
            stream_path(self._paths.streams, recording.id),
            frame=resampled.frame,
            cleaned=cleaned,
            sources=source_labels(activity),
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
                "source": origin.source.value if origin is not None else None,
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
        kept = await asyncio.to_thread(
            self._place,
            path,
            destination=self._paths.quarantine_for(file_hash, extension),
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

    async def _lost_the_race(self, *, name: str, file_hash: str) -> IngestReport:
        """Report the writer that got there first, on a rolled-back session.

        The unique constraint on ``(file_hash, file_sport_index)`` is the real
        dedup check; the read in :meth:`_classify` is an optimisation that can
        always be overtaken. Losing that race means the file **is** ingested —
        by somebody else — which is a `duplicate_file`, not an error and
        certainly not a quarantine record asking the athlete to rule on a file
        that is already a session.
        """
        known = await self._recordings.by_hash(file_hash)
        sessions = tuple(dict.fromkeys(row.session_id for row in known))
        detail = (
            f"another ingest of this file committed first; it is recorded as "
            f"{len(known)} recording(s)"
        )
        logger.info("ingest_lost_dedup_race", filename=name, file_hash=file_hash)
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
        )

    async def _dead_link(self, name: str) -> IngestReport:
        """Log the link whose target could not be read, and move on."""
        detail = (
            "this inbox entry was a symbolic link whose target could not be "
            "read; the link has been removed and nothing was ingested"
        )
        await self._events.record(
            filename=name,
            file_hash=None,
            outcome=IngestOutcome.ERROR,
            detail=detail,
        )
        await commit(self._session)
        return IngestReport(
            filename=name,
            file_hash=None,
            outcome=IngestOutcome.ERROR,
            detail=detail,
        )

    async def _rescue(
        self,
        path: Path,
        *,
        name: str,
        actor: Actor,
        error: Exception,
        quarantine: bool = True,
    ) -> IngestReport:
        """Last resort: keep the file, say what broke, do not raise.

        The transaction is rolled back first — a failure part-way through
        leaves the session unusable — and the record is written on a fresh
        one. If even this fails the exception propagates: at that point the
        database is unreachable, and inventing a success would be worse than
        letting the scheduler log it.

        With ``quarantine`` false the file is left exactly where it is and only
        the error event is written. That is the reject path: the athlete has
        just resolved this file's record, and a fresh pending one — pointing,
        in the multisport case, into ``originals/`` — would be a queue entry
        for a decision that has already been taken.
        """
        await self._session.rollback()
        detail = f"the pipeline failed while ingesting this file: {error}"
        file_hash: str | None = None
        quarantined: tuple[uuid.UUID, ...] = ()
        try:
            file_hash = await asyncio.to_thread(_sha256, path)
        except OSError:
            file_hash = None
        if file_hash is not None and quarantine:
            kept = await asyncio.to_thread(
                self._place,
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


# --- the pure pass ------------------------------------------------------------


def prepare(activity: ParsedActivity) -> PreparedActivity:
    """Resample, clean and classify one activity. No I/O, no database.

    Run in a worker thread: this is the part of the pipeline that costs real
    CPU (about a second for a four-hour FIT file), and a backfill of a season's
    files would otherwise hold the event loop for minutes.

    The duration handed to `classify_discipline` is the **recording** time, not
    the elapsed: an hour in the gym with a forty-minute break between blocks
    lasts a hundred minutes on the clock, and classifying it by that number
    calls it ``other`` on the grounds that it took too long to be a gym
    session.
    """
    resampled = resample(activity.samples)
    cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)
    channels = channels_present(activity.samples)
    discipline, classification = classify_discipline(
        sport=activity.sport,
        has_power=StreamChannel.POWER in channels,
        has_speed=StreamChannel.SPEED in channels,
        has_gps=StreamChannel.LAT in channels,
        duration_s=resampled.recording_time_s,
    )
    return PreparedActivity(
        resampled=resampled,
        cleaned=cleaned,
        channels=channels,
        discipline=discipline,
        classification=classification,
    )


def _canonical_copy(
    known: Sequence[RecordingRow], pending: QuarantineRecordRow | None
) -> Path | None:
    """Where the database says the bytes of an already-known file are kept."""
    if known:
        return Path(known[0].original_path)
    if pending is not None:
        return Path(pending.quarantined_path)
    return None


# --- row builders -------------------------------------------------------------


def _recording_row(
    *,
    session_id: uuid.UUID,
    activity: ParsedActivity,
    resampled: ResampleResult,
    file_hash: str,
    extension: str,
    original: Path,
    origin: FileOrigin | None,
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
        source=origin.source.value if origin is not None else None,
        external_id=origin.external_id if origin is not None else None,
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


def source_labels(activity: ParsedActivity) -> dict[StreamChannel, str]:
    """The per-channel source labels written into the parquet metadata.

    Power and heart rate name a *device*, because more than one could have
    produced them and A4.3's whole point is that the file does not say which.
    The odometer names a *field*: a file writes at most one, so there is no
    tie-break to record, only which field of which format it came out of.
    """
    return {
        channel: source
        for channel, source in (
            (StreamChannel.POWER, activity.power_source),
            (StreamChannel.HR, activity.hr_source),
            (StreamChannel.DISTANCE, activity.distance_source),
        )
        if source is not None
    }


def _report(name: str, file_hash: str, placement: _Placement) -> IngestReport:
    """Summarise what a file produced, counting only what it actually did.

    A file with one ingested activity and one quarantined one reports
    ``ingested``: something reached the calendar, and the quarantine record is
    on the report as well as in the queue.

    A file whose every activity was **already** a session reports
    ``duplicate_file``. It created nothing, and "2 session(s) ingested" would
    be the ingest log's answer to "did anything happen when I dropped this in
    again?" — the one question that log is opened for.
    """
    created, existing = (
        placement.created,
        len(placement.sessions) - len(placement.created),
    )
    if created:
        outcome = IngestOutcome.INGESTED
    elif placement.quarantines:
        outcome = IngestOutcome.QUARANTINED
    elif placement.sessions:
        outcome = IngestOutcome.DUPLICATE_FILE
    else:
        outcome = IngestOutcome.QUARANTINED
    parts = [f"{len(created)} session(s) ingested"] if created else []
    if existing:
        parts.append(f"{existing} activity(ies) already ingested")
    if placement.quarantines or not parts:
        parts.append(f"{len(placement.quarantines)} quarantined")
    return IngestReport(
        filename=name,
        file_hash=file_hash,
        outcome=outcome,
        detail=", ".join(parts),
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
