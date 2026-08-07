"""Ingest use-cases: upload a file, and rule on what the pipeline refused.

Service-shaped, but in `app.ingest` rather than `app.services`, because every
one of these does file work: an upload writes into the inbox and runs the
pipeline, confirming a quarantine deletes a file, rejecting one puts a file
back through the pipeline. `app.services` sits *below* this layer and may not
import the pipeline, so the use-cases that need it live here (see the layer
contract in `pyproject.toml`). Routes stay thin either way — they call this,
and this commits.

The two quarantine outcomes are asymmetric on purpose (B-4):

* **confirm** — "yes, this is the duplicate you thought": the *quarantined
  copy* is discarded and the record closed. Nothing under ``originals/`` is
  ever touched, so the already-ingested twin keeps its file.
* **reject** — "you were wrong, ingest it anyway": the file goes back through
  the pipeline with the one check the athlete overruled waived, and becomes
  its own session. Two verdicts can be overruled — `suspected_duplicate`
  ("this is a different session") and `implausible_channel` ("that channel is
  broken, the rest of the ride is not"). The rest are 409s: nothing about
  disagreeing with the parser makes unreadable bytes readable, and a file with
  two minutes in it does not gain a third.

**The decision is committed before the pipeline runs.** Rejecting is the
athlete's ruling, and a pipeline that fails afterwards must not take it back:
sharing one transaction meant the rollback inside the pipeline's own catch-all
erased the resolution *and* its audit row while leaving the file where the
pipeline had already put it. So the transition commits here, and the ingest
runs on a session of its own — a failure then costs an error event and
nothing else.
"""

import datetime as dt
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.activity import IngestOutcome, QuarantineReason, QuarantineStatus
from app.domain.actor import Actor
from app.ingest.pipeline import IngestPaths, IngestPipeline, IngestReport
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, session_scope
from app.persistence.ingest_log import (
    MAX_FILENAME_LENGTH,
    IngestEventRepository,
    IngestEventRow,
    QuarantineRecordRow,
    QuarantineRepository,
)

logger = get_logger(__name__)

#: `entity_type` written on this module's audit rows.
ENTITY_TYPE = "quarantine_record"

#: Largest upload accepted, in bytes. A four-hour FIT file is under a
#: megabyte; this is three orders of magnitude of headroom and still a bound,
#: which an endpoint writing to disk needs to have.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

#: Everything not matched here is replaced in an uploaded filename. The name
#: is written to disk, so it is rebuilt from a safe alphabet rather than
#: inspected for the traversal of the week.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Used when the sanitised name is empty (a filename that was all separators).
FALLBACK_FILENAME = "upload"

#: Longest name written into the inbox, ``<uuid7>-<name>`` included. NAME_MAX
#: is 255 bytes on every filesystem this runs on and 143 on eCryptfs; the
#: margin below the smaller of those is deliberate, because the failure this
#: bounds — ``ENAMETOOLONG`` from the write — is a 500 on an upload the athlete
#: did nothing wrong in making.
MAX_STAGED_NAME = 180

#: The quarantined reasons a *reject* can overrule, and what each one waives.
#: Every other reason is a 409 (see :meth:`IngestService.reject`).
REJECTABLE_REASONS = frozenset(
    {QuarantineReason.SUSPECTED_DUPLICATE, QuarantineReason.IMPLAUSIBLE_CHANNEL}
)


def safe_filename(raw: str) -> str:
    """A filename safe to write into the inbox, derived from the client's.

    Directory components are dropped, everything outside
    ``[A-Za-z0-9._-]`` becomes an underscore, and a leading dot is stripped —
    the sweep skips dotfiles, so a name starting with one would land a file in
    the inbox that nothing ever picks up.

    The result is bounded by the *log* column's width, not by the filesystem's:
    what goes on disk is :func:`staged_name`, and the name the athlete gave the
    file is worth keeping in full in the ingest log.
    """
    name = _UNSAFE.sub("_", Path(raw).name).strip("._")[:MAX_FILENAME_LENGTH]
    return name or FALLBACK_FILENAME


def staged_name(name: str) -> str:
    """``<uuid7>-<name>``, short enough for any filesystem to accept.

    Two uploads called `activity.fit` must not overwrite one another, and the
    second one is a duplicate by *hash* or it is not a duplicate at all — hence
    the prefix. A name too long for the prefix to fit beside is truncated from
    the middle, keeping its extension: the extension is what the pipeline
    dispatches a parser on, so dropping it would turn a perfectly good ride
    into an `unreadable_file` on the strength of its name's length.
    """
    prefix = f"{uuid.uuid7()}-"
    room = MAX_STAGED_NAME - len(prefix)
    if len(name) <= room:
        return f"{prefix}{name}"
    suffix = Path(name).suffix[: room // 2]
    return f"{prefix}{name[: room - len(suffix)]}{suffix}"


class IngestService:
    """Upload, quarantine decisions and the ingest log. Raises AppError."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        pipeline: IngestPipeline,
        quarantine: QuarantineRepository,
        events: IngestEventRepository,
        audit: AuditRepository,
        paths: IngestPaths,
    ) -> None:
        self._session = session
        self._pipeline = pipeline
        self._quarantine = quarantine
        self._events = events
        self._audit = audit
        self._paths = paths

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service, its pipeline and its repositories to one session."""
        pipeline = IngestPipeline.from_session(session)
        return cls(
            session,
            pipeline=pipeline,
            quarantine=QuarantineRepository(session),
            events=IngestEventRepository(session),
            audit=AuditRepository(session),
            paths=pipeline.paths,
        )

    # --- reads ---------------------------------------------------------------

    async def quarantine_records(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[QuarantineRecordRow], int]:
        """A page of quarantine records, pending first."""
        return await self._quarantine.list(offset=offset, limit=limit)

    async def events(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[IngestEventRow], int]:
        """A page of the ingest log, newest first."""
        return await self._events.list(offset=offset, limit=limit)

    async def get_record(self, record_id: uuid.UUID) -> QuarantineRecordRow:
        """One quarantine record.

        Raises:
            NotFoundError: When no record has that id.
        """
        row = await self._quarantine.get(record_id)
        if row is None:
            raise NotFoundError(f"Quarantine record {record_id} not found")
        return row

    # --- writes --------------------------------------------------------------

    async def upload(
        self, *, filename: str, content: bytes, actor: Actor
    ) -> IngestReport:
        """Write an uploaded file into the inbox and ingest it now.

        Synchronous rather than "dropped in for the sweep to find" because the
        athlete is standing in front of the result: an upload that answered
        202 would leave the page with nothing to say for up to half a minute,
        and the pipeline is fast enough that there is nothing to gain.

        Raises:
            ValidationError: When the upload is empty or above
                :data:`MAX_UPLOAD_BYTES`.
        """
        if not content:
            raise ValidationError("The uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValidationError(
                f"The uploaded file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
            )
        name = safe_filename(filename)
        # A unique, bounded name on disk; the athlete's own name in the log.
        self._paths.inbox.mkdir(parents=True, exist_ok=True)
        staged = self._paths.inbox / staged_name(name)
        staged.write_bytes(content)
        logger.info("upload_received", filename=name, bytes=len(content))
        return await self._pipeline.ingest_file(staged, actor=actor, filename=name)

    async def confirm(
        self, record_id: uuid.UUID, *, actor: Actor
    ) -> QuarantineRecordRow:
        """Accept the pipeline's verdict and discard the quarantined copy.

        The file is unlinked only when it is genuinely in ``quarantine/``. A
        record whose file was filed as an *original* — the multisport case,
        where another activity in the same file was ingested — closes without
        deleting anything, because that file is the twin's original and this
        system never deletes one.

        Raises:
            NotFoundError: When no record has that id.
            ConflictError: When the record has already been resolved.
        """
        row = await self._require_pending(record_id)
        discarded = self._discard(Path(row.quarantined_path))
        row.status = QuarantineStatus.CONFIRMED_DISCARDED
        row.resolved_at = dt.datetime.now(dt.UTC)
        await self._quarantine.add(row)
        await self._audit.record(
            actor=actor,
            action="ingest.quarantine_confirmed",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "file_hash": row.file_hash,
                "reason": row.reason.value,
                "quarantined_path": row.quarantined_path,
                "file_deleted": discarded,
            },
        )
        await commit(self._session)
        return row

    async def reject(
        self, record_id: uuid.UUID, *, actor: Actor
    ) -> tuple[QuarantineRecordRow, IngestReport]:
        """Overrule the pipeline's verdict and ingest the file anyway.

        Two verdicts can be overruled, and each waives exactly the check it
        disagrees with:

        * `suspected_duplicate` — "this is a different session": the
          file-level hash check and the overlap check are waived.
        * `implausible_channel` — "one channel is broken, the ride is not":
          that check alone is waived. What is ingested carries no out-of-range
          reading regardless, because the cleaner nulls every value it cannot
          believe and records each one as a repair.

        The transition is committed **before** the pipeline runs, on this
        service's own session, and the ingest runs on a fresh one: a pipeline
        failure then leaves the athlete's decision standing and the file where
        it is, instead of rolling both back and leaving the file moved.

        Raises:
            NotFoundError: When no record has that id.
            ConflictError: When the record is already resolved, when its reason
                is not one of the two above (there is nothing in it that is
                safe to ingest), or when the file it points at is gone.
        """
        row = await self._require_pending(record_id)
        if row.reason not in REJECTABLE_REASONS:
            raise ConflictError(
                f"This file was quarantined as {row.reason.value!r}, which is not "
                "a verdict that can be overruled: there is nothing in it that is "
                "safe to ingest. Confirm to discard it, fix the file, and drop it "
                "in again."
            )
        path = self._surviving_file(row)
        reason, filename = row.reason, row.original_filename

        row.status = QuarantineStatus.REJECTED_INGESTED
        row.resolved_at = dt.datetime.now(dt.UTC)
        await self._quarantine.add(row)
        await self._audit.record(
            actor=actor,
            action="ingest.quarantine_rejected",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "file_hash": row.file_hash,
                "reason": reason.value,
                "suspected_session_id": (
                    str(row.suspected_session_id) if row.suspected_session_id else None
                ),
                "quarantined_path": row.quarantined_path,
            },
        )
        # Committed here, so that nothing the pipeline does can undo it — and
        # so the pipeline's own "is this hash waiting in quarantine?" check
        # does not refuse the very file this decision is about.
        await commit(self._session)

        async with session_scope() as pipeline_session:
            report = await IngestPipeline.from_session(pipeline_session).ingest_file(
                path,
                actor=actor,
                filename=filename,
                reingest=reason is QuarantineReason.SUSPECTED_DUPLICATE,
                waive_implausible=reason is QuarantineReason.IMPLAUSIBLE_CHANNEL,
                quarantine_on_failure=False,
            )
        if report.outcome is IngestOutcome.INGESTED:
            # The bytes are an original now; the quarantined copy is the queue
            # entry's, and the queue entry is resolved.
            self._discard(path)
        else:
            logger.info(
                "quarantine_reject_did_not_ingest",
                record_id=str(record_id),
                outcome=report.outcome.value,
            )
        return await self.get_record(record_id), report

    # --- helpers -------------------------------------------------------------

    async def _require_pending(self, record_id: uuid.UUID) -> QuarantineRecordRow:
        """The record, if it is still waiting on a decision.

        Raises:
            NotFoundError: When no record has that id.
            ConflictError: When it has already been resolved. Two clicks on
                the same button must not discard a second file or ingest a
                second copy.
        """
        row = await self.get_record(record_id)
        if row.status is not QuarantineStatus.PENDING:
            raise ConflictError(
                f"Quarantine record {record_id} was already resolved as "
                f"{row.status.value!r}"
            )
        return row

    def _surviving_file(self, record: QuarantineRecordRow) -> Path:
        """The file a record points at, if it is still on disk.

        Raises:
            ConflictError: When it is gone — deleted by hand, or by a confirm
                that raced this reject. There is nothing to ingest, and saying
                so beats a pipeline run that fails on a missing path.
        """
        path = Path(record.quarantined_path)
        if not path.is_file():
            raise ConflictError(
                f"The quarantined file is no longer at {record.quarantined_path}; "
                "nothing can be ingested from this record."
            )
        return path

    def _discard(self, path: Path) -> bool:
        """Delete a quarantined file, and refuse to delete anything else.

        Returns whether a file was removed. The guard is structural rather
        than a convention: this is the only delete in the ingest path, and the
        thing it must never reach is ``data/originals/``.
        """
        if self._paths.quarantine.resolve() not in path.resolve().parents:
            logger.info("quarantine_file_kept", path=str(path))
            return False
        if not path.is_file():
            return False
        path.unlink()
        return True
