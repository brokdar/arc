"""HTTP endpoints for the sources arc collects from.

Thin over `app.services.integrations`, which owns the catalogue rules, the
normalised folder refusal and the commits.

Two shapes here are deliberate and worth reading before adding a route.

**The catalogue is `/api/v1/integration-catalogue`, not
`/api/v1/integrations/catalogue`.** A facet of the collection may not live
under `/{id}` (`.claude/rules/api-collection-facets.md`): the second spelling
also matches `/integrations/{integration_id}`, so every method other than the
one the facet declares would fall through to the id route and answer 422 about
path-parameter syntax where 405 is the true answer. Outside the id namespace,
Starlette's own 405 is correct for free.

**`{integration_id}` is typed `str`, not `uuid.UUID`.** The local drop is
synthesized and its id is the literal `local_drop`; typed as a UUID, a `DELETE`
on it would be a 422 about hex digits, when what the athlete asked for is
something that does not exist and cannot be made to. The service answers 404.

**`/integrations/local-drop/settings` is a literal, and so it is a 405.**
Nothing declares `/{integration_id}/settings`, so this two-segment literal
shadows no id route and every method it does not declare gets Starlette's own
405 rather than a 422 about path-parameter syntax — the same outcome
`.claude/rules/api-collection-facets.md` demands, reached by having no
collision instead of by moving the path. The segment is spelled `local-drop`
where the id is `local_drop`: the id is an `IntegrationKind` value and travels
in JSON, the path segment is a URL and travels in a browser bar.

There is deliberately **no `POST /api/v1/feeds` any more**. A feed created
without an integration is a folder arc polls with nothing recording what it
brings in, which is the whole defect this surface exists to close; the folder
operations live under the integration that owns them.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import ActorDep
from app.api.schemas.integrations import (
    CatalogueEntry,
    FolderUpdate,
    IntegrationCatalogue,
    IntegrationCreate,
    IntegrationList,
    IntegrationRead,
    LocalDropSettingsRead,
    LocalDropSettingsUpdate,
    StorageStatusRead,
    TransportOffer,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.domain.integrations import CATALOGUE, SYNTHESIZED_KINDS, ordered_data_kinds
from app.ingest.inbox import set_scan_interval
from app.persistence.db import SessionDep
from app.services.ingest_settings import IngestSettingsService
from app.services.integrations import IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])
catalogue_router = APIRouter(prefix="/integration-catalogue", tags=["integrations"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "No such integration or folder"}
}
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": "Another integration is already collecting that folder",
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": (
            "The integration cannot be added, the transport is not one it "
            "supports, or its storage provider has no account connected"
        ),
    }
}


def get_service(session: SessionDep) -> IntegrationService:
    """Bind the service to a request-scoped session."""
    return IntegrationService.from_session(session)


ServiceDep = Annotated[IntegrationService, Depends(get_service)]


@catalogue_router.get("")
async def get_catalogue(service: ServiceDep) -> IntegrationCatalogue:
    """Every source arc can collect from, and how ready each transport is.

    Exactly what arc ships. There is no "coming soon" entry: an integration the
    athlete can pick and arc cannot deliver is a promise broken at the one
    moment the application asked for trust. That the model can express Garmin,
    Apple Health and the rest is proven by a domain test, not by this list.
    """
    return IntegrationCatalogue(
        items=[
            CatalogueEntry(
                kind=kind,
                display_name=spec.display_name,
                data_kinds=list(ordered_data_kinds(spec.provides)),
                addable=kind not in SYNTHESIZED_KINDS,
                transports=[
                    TransportOffer.model_validate(transport)
                    for transport in spec.transports
                ],
            )
            for kind, spec in CATALOGUE.items()
        ],
        storage=[
            StorageStatusRead.model_validate(status)
            for status in await service.storage_statuses()
        ],
    )


@router.get("")
async def list_integrations(service: ServiceDep) -> IntegrationList:
    """Every source arc collects from, the local drop first.

    The local drop is always in this list and has no row behind it — see
    `IntegrationService.list`.
    """
    return IntegrationList(
        items=[IntegrationRead.model_validate(view) for view in await service.list()]
    )


#: 201 is documented rather than declared as the operation's status code: the
#: handler chooses between 200 and 201 per request, and an undocumented 201
#: would be absent from the contract the frontend client is generated from.
CREATED: Responses = {
    201: {"model": IntegrationRead, "description": "The integration is new"}
}


@router.post("", responses=CREATED | BAD_BODY | NOT_FOUND | CONFLICT | INVALID)
async def add_integration(
    service: ServiceDep,
    actor: ActorDep,
    submitted: IntegrationCreate,
    response: Response,
) -> IntegrationRead:
    """Add a source and the folder it is collected through, in one call.

    **201** when the integration is new, **200** when an existing one grew a
    folder: adding Wahoo a second time with a different directory is one Wahoo
    with two folders, and answering 201 would tell the client a second entry
    appeared in a list that did not change length.
    """
    added = await service.add(
        kind=submitted.kind,
        transport=submitted.transport,
        connection_id=submitted.connection_id,
        remote_path=submitted.remote_path,
        path_display=submitted.path_display,
        actor=actor,
    )
    response.status_code = (
        status.HTTP_201_CREATED if added.created else status.HTTP_200_OK
    )
    return IntegrationRead.model_validate(added.view)


@router.delete(
    "/{integration_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def remove_integration(
    service: ServiceDep, actor: ActorDep, integration_id: str
) -> None:
    """Stop collecting from a source, keeping the account and the rides.

    A 404 for `local_drop`: it is synthesized, always present, and there is
    nothing to delete.
    """
    await service.remove(integration_id, actor=actor)


def get_ingest_settings(session: SessionDep) -> IngestSettingsService:
    """Bind the local drop's settings service to a request-scoped session."""
    return IngestSettingsService.from_session(session)


IngestSettingsDep = Annotated[IngestSettingsService, Depends(get_ingest_settings)]


@router.get("/local-drop/settings")
async def read_local_drop_settings(
    service: IngestSettingsDep,
) -> LocalDropSettingsRead:
    """Where the local drop looks, how often, and what may be changed.

    The path is reported and has no writer: `DATA__ROOT` roots `originals/`,
    `streams/` and `quarantine/` too and is a mounted volume, so moving it from
    a form would strand every file arc has already filed.
    """
    return LocalDropSettingsRead.model_validate(await service.read())


#: Not `INVALID`: that description narrates POST /integrations' refusals
#: (unknown kind, wrong transport, disconnected storage), none of which this
#: operation can produce. Its one 422 is the service refusing an interval
#: outside the bounds the read reports.
INTERVAL_OUT_OF_BOUNDS: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": (
            "The interval is outside the documented bounds — below the "
            "minimum or above the maximum the read reports"
        ),
    }
}


@router.put("/local-drop/settings", responses=BAD_BODY | INTERVAL_OUT_OF_BOUNDS)
async def set_local_drop_settings(
    session: SessionDep, actor: ActorDep, submitted: LocalDropSettingsUpdate
) -> LocalDropSettingsRead:
    """Set how often arc sweeps the drop folder, from this moment on.

    **The running scheduler is re-timed before this answers**, so a 200 means
    the sweep is already on the new interval — no restart, and no window in
    which Settings shows one number and the job runs another.

    PUT rather than PATCH because the resource is singular and the write is
    idempotent: there is one drop folder and setting its interval twice leaves
    the same one row.
    """
    return LocalDropSettingsRead.model_validate(
        await set_scan_interval(session, submitted.scan_interval_seconds, actor=actor)
    )


@router.patch("/{integration_id}/folders/{folder_id}", responses=BAD_BODY | NOT_FOUND)
async def update_folder(
    service: ServiceDep,
    actor: ActorDep,
    integration_id: str,
    folder_id: uuid.UUID,
    submitted: FolderUpdate,
) -> IntegrationRead:
    """Pause or resume one folder. The cursor is kept either way."""
    return IntegrationRead.model_validate(
        await service.set_folder_enabled(
            integration_id, folder_id, enabled=submitted.enabled, actor=actor
        )
    )


@router.delete(
    "/{integration_id}/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def remove_folder(
    service: ServiceDep, actor: ActorDep, integration_id: str, folder_id: uuid.UUID
) -> None:
    """Stop collecting through one folder.

    Removing the **last** folder removes the integration with it: an entry with
    no transport is a source arc claims to collect from and cannot reach.
    """
    await service.remove_folder(integration_id, folder_id, actor=actor)
