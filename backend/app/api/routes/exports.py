"""HTTP endpoints for exporting logged training. Thin over `app.services.exports`.

The whole-history exports live under `/exports` — a collection of files, not a
facet of `/sessions` — and the one-workout export is a sub-resource of one
session (`/sessions/{id}/export`), which has one more segment than the id
route and so collides with nothing.

The plain-text and CSV routes declare ``response_class=Response`` and state
their 200 body in ``responses``: a ``response_class`` with a media type of its
own would also be stamped on every *error* response in the contract (a 404
published as ``text/csv``), while the error envelope is JSON.

Every file is sent with ``Content-Disposition: attachment`` so a plain link in
the browser downloads it with a sensible name; the session-page "Copy as
text" action reads the same text endpoint through the typed client.
"""

import datetime as dt
import uuid
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from app.api.schemas.exports import WorkoutsExport
from app.core.exceptions import ErrorDetail
from app.domain.csv_safety import CsvVariant
from app.persistence.db import SessionDep
from app.services.exports import ExportService

router = APIRouter(prefix="/exports", tags=["exports"])
#: The one-workout export, a sub-resource of `/sessions/{id}`.
session_router = APIRouter(prefix="/sessions", tags=["exports"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "No such session"}}

_STRING = {"schema": {"type": "string"}}
CSV_BODY: Responses = {200: {"content": {"text/csv": _STRING}}}
SESSION_BODY: Responses = {
    200: {
        "content": {"text/plain": _STRING, "text/csv": _STRING},
        "description": "The session as text, or as the set-level CSV",
    }
}


class CsvResponse(Response):
    """A CSV body, UTF-8 with no BOM."""

    media_type = "text/csv"


class SessionExportFormat(StrEnum):
    """What one session is exported as."""

    TEXT = "text"
    CSV = "csv"


def get_service(session: SessionDep) -> ExportService:
    """Bind the service to a request-scoped session."""
    return ExportService.from_session(session)


ServiceDep = Annotated[ExportService, Depends(get_service)]
VariantQuery = Annotated[
    CsvVariant,
    Query(
        description=(
            "comma: comma and decimal point; semicolon: semicolon and decimal "
            "comma (German Excel)."
        )
    ),
]


def attachment(filename: str) -> dict[str, str]:
    """The header that makes a browser save the body under ``filename``."""
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/workouts.json", response_model_exclude_none=True)
async def export_workouts(service: ServiceDep, response: Response) -> WorkoutsExport:
    """Every session with logged sets, as openweight `WorkoutLog` documents.

    Wrapped in arc's ``arc-export`` envelope; arc's own fields are the
    ``arc*`` keys openweight leaves free.
    """
    response.headers.update(attachment("arc-workouts.json"))
    document = await service.workouts(exported_at=dt.datetime.now(dt.UTC))
    return WorkoutsExport.model_validate(document)


@router.get("/sets.csv", response_class=Response, responses=CSV_BODY)
async def export_sets(
    service: ServiceDep, variant: VariantQuery = CsvVariant.COMMA
) -> CsvResponse:
    """Every logged set, one row each, with formula-looking text escaped."""
    return CsvResponse(
        await service.sets_csv(variant), headers=attachment("arc-sets.csv")
    )


@router.get("/strong.csv", response_class=Response, responses=CSV_BODY)
async def export_strong(
    service: ServiceDep, variant: VariantQuery = CsvVariant.COMMA
) -> CsvResponse:
    """Every logged set in Strong's column layout (lossy: no side, W/D/F only)."""
    return CsvResponse(
        await service.strong_csv(variant), headers=attachment("arc-strong.csv")
    )


@session_router.get(
    "/{session_id}/export",
    response_class=Response,
    responses=SESSION_BODY | NOT_FOUND,
)
async def export_session(
    service: ServiceDep,
    session_id: uuid.UUID,
    export_format: Annotated[
        SessionExportFormat, Query(alias="format")
    ] = SessionExportFormat.TEXT,
    variant: VariantQuery = CsvVariant.COMMA,
) -> Response:
    """One session as text (to read or paste) or as the set-level CSV.

    The CSV of a session with no sets is its header line alone. ``variant``
    applies to the CSV only.
    """
    if export_format is SessionExportFormat.TEXT:
        text = await service.session_text(session_id)
        return PlainTextResponse(
            text.body, headers=attachment(f"arc-session-{text.local_date}.txt")
        )
    table = await service.session_csv(session_id, variant)
    return CsvResponse(
        table.body, headers=attachment(f"arc-session-{table.local_date}.csv")
    )
