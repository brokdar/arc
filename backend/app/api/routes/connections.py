"""HTTP endpoints for connecting a cloud account and picking folders.

Thin over `app.services.connections`, which owns the OAuth exchange, the
encryption and the commits.

**There is no `/feeds` collection any more.** `POST /api/v1/feeds` used to
create a `FeedRow` with nothing recording what the folder brings in, which is
exactly the folder-shaped configuration `app.api.routes.integrations` replaces:
a watched folder is now the *transport* of an integration, created and removed
through the integration that owns it. Leaving the route in place would leave
one write path that produces rows the panel cannot describe.

There is deliberately **no callback route here, and none anywhere**. The PKCE
paste flow is what makes that true: nothing on the internet ever has to reach
arc for a connection to be made, so `OPEN_PATHS` is unchanged and every route
below sits behind the session guard like the rest of `/api/v1`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ActorDep
from app.api.schemas.connections import (
    ConnectionList,
    ConnectionRead,
    DropboxAuthorizationRead,
    DropboxCodeSubmit,
    FolderList,
    FolderRead,
)
from app.api.schemas.integrations import IntegrationDiscoveryRead
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.connections import MAX_REMOTE_PATH_LENGTH
from app.persistence.db import SessionDep
from app.services.connections import ConnectionService
from app.services.integrations import IntegrationService

router = APIRouter(prefix="/connections", tags=["connections"])

type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {
    404: {"model": ErrorDetail, "description": "No such connection, feed or folder"}
}
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}
CONFLICT: Responses = {
    409: {
        "model": ErrorDetail,
        "description": (
            "A Dropbox account is already connected, the folder is already "
            "watched, or the credential needs re-authorising"
        ),
    }
}
INVALID: Responses = {
    422: {
        "model": ValidationErrorDetail,
        "description": "The setup is incomplete, or Dropbox refused the code",
    }
}
#: Dropbox's own 429, passed through with the delay it asked for rather than
#: surfacing as a 500 — a throttled read is transient, not a broken feature.
THROTTLED: Responses = {
    429: {"model": ErrorDetail, "description": "Dropbox is rate-limiting arc"}
}


def get_service(session: SessionDep) -> ConnectionService:
    """Bind the service to a request-scoped session."""
    return ConnectionService.from_session(session)


ServiceDep = Annotated[ConnectionService, Depends(get_service)]


def get_integrations(session: SessionDep) -> IntegrationService:
    """Bind the integration service to a request-scoped session."""
    return IntegrationService.from_session(session)


#: Discovery is a read *of a connection* that answers in the **integration**
#: vocabulary, so the path lives here with the rest of `/connections` and the
#: use-case lives with the catalogue that names what was found. One more
#: segment than `/connections/{connection_id}`, so nothing is shadowed and
#: `.claude/rules/api-collection-facets.md` has nothing to say about it.
IntegrationsDep = Annotated[IntegrationService, Depends(get_integrations)]

#: The remote folder to list. `""` — the default — is the Dropbox root.
#:
#: Typed `str` with a default rather than `str | None`: a query string delivers
#: `?path=null` as four letters, so advertising `null` in the contract is a
#: 422 waiting for the fuzzer to find it (`.claude/rules/api-nullability.md`).
PathQuery = Annotated[str, Query(max_length=MAX_REMOTE_PATH_LENGTH)]


@router.get("")
async def list_connections(service: ServiceDep) -> ConnectionList:
    """Every cloud account arc holds a credential for, with its folders."""
    return ConnectionList(
        items=[ConnectionRead.model_validate(row) for row in await service.list()]
    )


@router.post("/dropbox/authorize", responses=INVALID)
async def start_dropbox_authorization(
    service: ServiceDep, actor: ActorDep
) -> DropboxAuthorizationRead:
    """Begin connecting Dropbox: get the link the athlete opens.

    The link carries a PKCE challenge and **no redirect URI** — Dropbox shows
    the athlete a code, which they paste into `POST /connections/dropbox/complete`.
    That is what lets arc connect a cloud account from behind a home router
    without registering a redirect or being reachable from the internet.
    """
    started = await service.start_dropbox_authorization(actor=actor)
    return DropboxAuthorizationRead(
        authorize_url=started.authorize_url, expires_at=started.expires_at
    )


@router.post(
    "/dropbox/complete",
    status_code=status.HTTP_201_CREATED,
    responses=BAD_BODY | INVALID | CONFLICT,
)
async def complete_dropbox_authorization(
    service: ServiceDep, actor: ActorDep, submitted: DropboxCodeSubmit
) -> ConnectionRead:
    """Finish connecting Dropbox with the code the athlete pasted back."""
    return ConnectionRead.model_validate(
        await service.complete_dropbox(code=submitted.code, actor=actor)
    )


@router.get("/{connection_id}", responses=NOT_FOUND)
async def get_connection(
    service: ServiceDep, connection_id: uuid.UUID
) -> ConnectionRead:
    """One connection, with its folders."""
    return ConnectionRead.model_validate(await service.get(connection_id))


@router.get(
    "/{connection_id}/folders",
    responses=NOT_FOUND | CONFLICT | INVALID | THROTTLED,
)
async def list_folders(
    service: ServiceDep, connection_id: uuid.UUID, path: PathQuery = ""
) -> FolderList:
    """The folders directly under ``path`` — the folder picker's data.

    Folders only: the athlete is choosing a directory to watch, and the files
    in it are what the poll will find, not what this answers. A folder holding
    nothing but files is a 200 with an empty list.
    """
    return FolderList(
        items=[
            FolderRead(path_lower=folder.path_lower, name=folder.name)
            for folder in await service.folders(connection_id, path=path)
        ]
    )


@router.get(
    "/{connection_id}/discover",
    responses=NOT_FOUND | CONFLICT | INVALID | THROTTLED,
)
async def discover_integrations(
    integrations: IntegrationsDep, connection_id: uuid.UUID
) -> IntegrationDiscoveryRead:
    """The integrations arc thinks are already writing into this account.

    A read beside `/folders`, not a replacement for it: this one answers "where
    are my rides, and what wrote them", the browser answers "show me my
    Dropbox", and an athlete whose head unit files somewhere discovery does not
    look still needs the second one.

    Accepting a proposal is `POST /api/v1/integrations` with the fields on it,
    unchanged — the same write path, and the same refusals, as adding by hand.
    """
    return IntegrationDiscoveryRead.model_validate(
        await integrations.propose(connection_id)
    )


@router.delete(
    "/{connection_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def disconnect(
    service: ServiceDep, actor: ActorDep, connection_id: uuid.UUID
) -> None:
    """Forget a connection: revoke it upstream, delete it and all its feeds.

    204 even when the revoke call fails. The local credential is gone either
    way, which is what the athlete asked for; a token still alive on Dropbox's
    side can be finished off from Dropbox's own connected-apps page.
    """
    await service.disconnect(connection_id, actor=actor)
