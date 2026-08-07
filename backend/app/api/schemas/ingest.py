"""Request/response schemas for the watched folder and its queue.

Three resources, and between them they answer the only question the inbox
page exists to answer — *why is this ride not in my sessions?*

* the **ingest report** an upload comes back with, saying what happened to the
  file just handed over;
* the **quarantine queue**, one row per file the pipeline refused, carrying the
  machine-readable reason the page branches on;
* the **ingest log**, append-only, one row per file the pipeline looked at,
  including the ones it had already seen.

Filesystem paths are deliberately absent from all three. Where a file was put
is the server's business; what the athlete needs is the filename they
recognise, the reason, and the two buttons.
"""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.api.pagination import Page
from app.domain.activity import IngestOutcome, QuarantineReason, QuarantineStatus


class IngestReportRead(BaseModel):
    """What the pipeline did with one file.

    ``session_ids`` is a list because one file may hold several sports
    (A4.5), and it is populated for a ``duplicate_file`` outcome too — with
    the sessions the file was *already* ingested as, which is what the client
    needs to link to when it says "you already have this ride".
    """

    filename: str
    #: sha256 of the file, hex; null only when it could not be read at all.
    file_hash: str | None
    outcome: IngestOutcome
    #: One sentence, athlete-facing.
    detail: str | None
    #: Sessions created — or, for a duplicate, the ones it already exists as.
    session_ids: list[uuid.UUID]
    #: Quarantine records created, for the athlete to rule on.
    quarantine_ids: list[uuid.UUID]


class QuarantineRecordRead(BaseModel):
    """One file the pipeline refused, and what was decided about it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    file_hash: str
    #: Which sport within the file, when the file parsed far enough to tell.
    file_sport_index: int | None
    #: What the confirm/reject buttons branch on: only a
    #: ``suspected_duplicate`` has anything safe to ingest on reject.
    reason: QuarantineReason
    #: The same fact in the athlete's terms, with the numbers behind it.
    detail: str | None
    status: QuarantineStatus
    #: The session this looks like a second copy of, for the duplicate case.
    suspected_session_id: uuid.UUID | None
    created_at: dt.datetime
    resolved_at: dt.datetime | None


class IngestEventRead(BaseModel):
    """One file the pipeline looked at. Append-only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_hash: str | None
    outcome: IngestOutcome
    detail: str | None
    #: The session that was created, when one was. Nulled if it is later
    #: deleted — the log has to outlive what it describes.
    session_id: uuid.UUID | None
    at: dt.datetime


class QuarantineRejectRead(BaseModel):
    """The outcome of overruling the pipeline on a suspected duplicate.

    Both halves, because both changed: the record is now
    ``rejected_ingested``, and the file went back through the pipeline as its
    own session.
    """

    record: QuarantineRecordRead
    report: IngestReportRead


QuarantinePage = Page[QuarantineRecordRead]
IngestEventsPage = Page[IngestEventRead]
