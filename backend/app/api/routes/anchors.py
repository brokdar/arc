"""HTTP endpoints for anchors: read the history, append to it.

The append-only rule (build-plan invariant 3) is stated three times over,
each time in the place that can actually enforce it: the repository has no
update or delete method, the service has no use-case for one, and this module
answers PUT, PATCH and DELETE on an anchor version with **405 and a sentence
saying why**.

Those three handlers have to exist. FastAPI answers an undefined method+path
combination with **404**, which reads as "wrong id" — the one message
guaranteed to send a client looking in the wrong place.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic.json_schema import SkipJsonSchema

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.anchors import (
    AnchorVersionAppended,
    AnchorVersionCreate,
    AnchorVersionRead,
    AnchorVersionsPage,
    RepriceReportRead,
)
from app.core.exceptions import (
    ErrorDetail,
    MethodNotAllowedError,
    ValidationErrorDetail,
)
from app.domain.anchors import AnchorSource, AnchorType
from app.ingest.repricing import append_anchor_and_reprice
from app.persistence.db import SessionDep
from app.services.anchors import AnchorService

router = APIRouter(prefix="/anchors", tags=["anchors"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "Anchor version not found"}
}
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "Version violates a schema or domain rule",
    }
}
APPEND_ONLY: Responses = {
    405: {"model": ErrorDetail, "description": "Anchor history is append-only"}
}

#: The explanation every 405 handler returns. One sentence, because it is the
#: only thing a client hitting it will read.
APPEND_ONLY_DETAIL = (
    "Anchor history is append-only: versions are never edited or deleted. "
    "POST /api/v1/anchors to append a correction — the old version stays in "
    "the history, and anything derived from it stays reproducible."
)

#: RFC 9110 requires a 405 to say what the resource *does* accept.
READ_ONLY_ALLOW = {"Allow": "GET"}


def get_service(session: SessionDep) -> AnchorService:
    """Bind the service to a request-scoped session."""
    return AnchorService.from_session(session)


ServiceDep = Annotated[AnchorService, Depends(get_service)]

# `SkipJsonSchema[None]`: the parameter is optional by *omission*. Without it
# the contract advertises `null` as a legal value, but a query string carries
# `anchor_type=null` as the four-letter string, which the enum rejects with a
# 422 — a schema/validation mismatch Schemathesis flags (found in CI).
AnchorTypeFilter = Annotated[
    AnchorType | SkipJsonSchema[None],
    Query(description="Restrict to one anchor type; omit for all of them."),
]


@router.get("")
async def list_anchor_versions(
    service: ServiceDep, page: PageParamsDep, anchor_type: AnchorTypeFilter = None
) -> AnchorVersionsPage:
    """List anchor versions, newest first, optionally for one anchor type."""
    versions, total = await service.list(
        anchor_type=anchor_type, offset=page.offset, limit=page.limit
    )
    return AnchorVersionsPage(
        items=[AnchorVersionRead.model_validate(version) for version in versions],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=BAD_BODY | INVALID)
async def append_anchor_version(
    session: SessionDep, actor: ActorDep, payload: AnchorVersionCreate
) -> AnchorVersionAppended:
    """Append a new version to an anchor's history.

    Appending also **reprices the recorded history the version governs**
    (`app.ingest.repricing`): sessions whose current metrics were computed
    against a different measurement of this anchor for their date get a new
    metric version, and `reprice` reports the counts. The append itself is
    committed first — a recompute failure never unwinds the measurement.
    """
    version, report = await append_anchor_and_reprice(
        session,
        actor=actor,
        anchor_type=payload.anchor_type,
        value=payload.value,
        provenance=payload.provenance,
        # The athlete is the only writer with an HTTP session; the agent
        # writes through MCP (WP-8), which supplies `agent` here.
        source=AnchorSource.ATHLETE,
        effective_date=payload.effective_date,
        unit=payload.unit,
        protocol=payload.protocol,
        ci_low=payload.ci_low,
        ci_high=payload.ci_high,
    )
    return AnchorVersionAppended(
        **AnchorVersionRead.model_validate(version).model_dump(),
        reprice=RepriceReportRead(
            examined=report.examined,
            repriced=report.repriced,
            unchanged=report.unchanged,
            failed=report.failed,
            note=report.note,
        ),
    )


@router.get("/current", responses=NOT_FOUND)
async def get_current_anchor_version(
    service: ServiceDep,
    anchor_type: Annotated[AnchorType, Query(description="Which anchor to resolve.")],
) -> AnchorVersionRead:
    """Get the version of one anchor currently in force.

    "In force" is a domain rule, not `ORDER BY created_at DESC`: a version
    effective from a future date does not count yet, and a back-dated
    correction does.
    """
    return AnchorVersionRead.model_validate(await service.current(anchor_type))


@router.get("/{anchor_version_id}", responses=NOT_FOUND)
async def get_anchor_version(
    service: ServiceDep, anchor_version_id: uuid.UUID
) -> AnchorVersionRead:
    """Get one anchor version by id."""
    return AnchorVersionRead.model_validate(await service.get(anchor_version_id))


# Three handlers, not one function with three decorators: the OpenAPI
# operation id is derived from the endpoint's name (`app.main`), and three
# routes sharing one name would collide in the generated frontend client.
#
# The path parameter is a plain `str`, not a `uuid.UUID`: these handlers do
# not look at it, and validating it would answer `PUT /anchors/current` with a
# 422 about UUID syntax instead of the 405 that is actually true.


@router.put(
    "/{anchor_version_id}",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    responses=APPEND_ONLY,
)
async def replace_anchor_version(anchor_version_id: str) -> None:
    """Refuse to replace an anchor version (405).

    Raises:
        MethodNotAllowedError: Always. See :data:`APPEND_ONLY_DETAIL`.
    """
    raise MethodNotAllowedError(APPEND_ONLY_DETAIL, READ_ONLY_ALLOW)


@router.patch(
    "/{anchor_version_id}",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    responses=APPEND_ONLY,
)
async def update_anchor_version(anchor_version_id: str) -> None:
    """Refuse to edit an anchor version (405).

    Raises:
        MethodNotAllowedError: Always. See :data:`APPEND_ONLY_DETAIL`.
    """
    raise MethodNotAllowedError(APPEND_ONLY_DETAIL, READ_ONLY_ALLOW)


@router.delete(
    "/{anchor_version_id}",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    responses=APPEND_ONLY,
)
async def delete_anchor_version(anchor_version_id: str) -> None:
    """Refuse to delete an anchor version (405).

    Raises:
        MethodNotAllowedError: Always. See :data:`APPEND_ONLY_DETAIL`.
    """
    raise MethodNotAllowedError(APPEND_ONLY_DETAIL, READ_ONLY_ALLOW)
