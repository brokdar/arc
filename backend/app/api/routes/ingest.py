"""HTTP endpoints for the watched folder: upload, quarantine, ingest log.

Thin over `app.ingest.service`, which owns the file work and the commits. The
only thing decided here is that an upload is **synchronous**: the athlete is
looking at the result, and the alternative — 202 and a page that says nothing
for up to half a minute — buys nothing the pipeline's speed does not already
give.

`POST /ingest/upload` therefore answers 200 with the outcome, including for a
file it refused: a quarantined file is a *result*, not an error. The 4xx
statuses here are for requests that are wrong (an empty upload, a decision on
a record that is already resolved), never for a bad recording.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.ingest import (
    IngestEventRead,
    IngestEventsPage,
    IngestReportRead,
    QuarantinePage,
    QuarantineRecordRead,
    QuarantineRejectRead,
)
from app.core.exceptions import ErrorDetail, ValidationError, ValidationErrorDetail
from app.ingest.pipeline import IngestReport
from app.ingest.service import MAX_UPLOAD_BYTES, IngestService
from app.persistence.db import SessionDep

router = APIRouter(prefix="/ingest", tags=["ingest"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "No such quarantine record"}
}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": "The record is already resolved, or holds nothing safe to ingest",
    }
}
INVALID: Responses = {
    422: {"model": ValidationErrorDetail, "description": "The upload is unusable"}
}


def get_service(session: SessionDep) -> IngestService:
    """Bind the service to a request-scoped session."""
    return IngestService.from_session(session)


ServiceDep = Annotated[IngestService, Depends(get_service)]

#: The multipart part carrying the device file.
UploadDep = Annotated[UploadFile, File(description="A FIT, GPX or TCX file.")]

#: How much a multipart envelope may add to the file it carries: a boundary,
#: a couple of headers and the filename. Kilobytes, allowed generously — the
#: point of the header check is to refuse gigabytes before they are spooled,
#: not to police the last kilobyte, which `MAX_UPLOAD_BYTES` does exactly.
MULTIPART_OVERHEAD_BYTES = 8 * 1024


def _refuse_oversized(size: int, limit: int) -> None:
    """Refuse an upload above the limit, in the athlete's units."""
    if size > limit:
        raise ValidationError(
            f"The uploaded file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )


def to_report(report: IngestReport) -> IngestReportRead:
    """Project the pipeline's report onto its response shape."""
    return IngestReportRead(
        filename=report.filename,
        file_hash=report.file_hash,
        outcome=report.outcome,
        detail=report.detail,
        session_ids=list(report.session_ids),
        quarantine_ids=list(report.quarantine_ids),
    )


@router.post("/upload", responses=INVALID)
async def upload_activity_file(
    request: Request, service: ServiceDep, actor: ActorDep, file: UploadDep
) -> IngestReportRead:
    """Upload one activity file and ingest it now.

    The same pipeline the watched folder runs, so the outcomes are the same:
    a new session, a file already known by its hash, or a quarantine record
    with the reason it was refused. An upload above the size limit is refused
    with a 422.
    """
    # The bound is applied twice, and the first one is the one that matters.
    # Multipart carries no declared part size: Starlette counts the bytes as it
    # spools them, so `file.size` is only true once the whole body has been
    # written to the container's disk — a 20 GB POST would fill the filesystem
    # in order to be refused. The request's own `Content-Length` is known
    # before a byte is read, and refusing on it never touches `file` at all.
    # (Caddy holds the same line one hop earlier; this must stand without it,
    # because the API is also reachable directly.)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit():
        _refuse_oversized(int(declared), MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES)
    # And then the file itself, for a chunked request that declared nothing.
    if file.size is not None:
        _refuse_oversized(file.size, MAX_UPLOAD_BYTES)
    return to_report(
        await service.upload(
            filename=file.filename or "", content=await file.read(), actor=actor
        )
    )


@router.get("/quarantine")
async def list_quarantine(service: ServiceDep, page: PageParamsDep) -> QuarantinePage:
    """List quarantined files, still-pending ones first.

    Pending first because the page is a queue: what is waiting on the athlete
    outranks what they have already dealt with, whenever it arrived.
    """
    records, total = await service.quarantine_records(
        offset=page.offset, limit=page.limit
    )
    return QuarantinePage(
        items=[QuarantineRecordRead.model_validate(record) for record in records],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("/quarantine/{record_id}/confirm", responses=NOT_FOUND | CONFLICT)
async def confirm_quarantine(
    service: ServiceDep, actor: ActorDep, record_id: uuid.UUID
) -> QuarantineRecordRead:
    """Accept the verdict: discard the quarantined copy of this file.

    Only the copy in ``quarantine/``. If the same file also produced a session
    — a multisport file with one good sport and one refused — its original
    stays where it is, because nothing in this system deletes an original.
    """
    return QuarantineRecordRead.model_validate(
        await service.confirm(record_id, actor=actor)
    )


@router.post("/quarantine/{record_id}/reject", responses=NOT_FOUND | CONFLICT)
async def reject_quarantine(
    service: ServiceDep, actor: ActorDep, record_id: uuid.UUID
) -> QuarantineRejectRead:
    """Overrule the verdict: ingest this file anyway.

    Two verdicts can be overruled — `suspected_duplicate` ("this is a
    different session") and `implausible_channel` ("one channel is broken, the
    ride is not"; the cleaner nulls what it cannot believe, so nothing
    out-of-range reaches analysis). A corrupt or too-short file offers confirm
    (discard) and a re-drop after fixing it — disagreeing with the parser does
    not make the bytes readable, so rejecting one is a 409 rather than an
    ingest that would fail identically.

    The answer is 200 even when the re-ingest did not produce a session: the
    decision has been recorded either way, and the report says what came of it.
    """
    record, report = await service.reject(record_id, actor=actor)
    return QuarantineRejectRead(
        record=QuarantineRecordRead.model_validate(record), report=to_report(report)
    )


@router.get("/events")
async def list_ingest_events(
    service: ServiceDep, page: PageParamsDep
) -> IngestEventsPage:
    """List the ingest log, newest first.

    One row per file the pipeline looked at, including the ones it had already
    seen: "nothing happened because I already have it" is the answer to most
    of the questions this log is opened for.
    """
    events, total = await service.events(offset=page.offset, limit=page.limit)
    return IngestEventsPage(
        items=[IngestEventRead.model_validate(event) for event in events],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )
