"""HTTP endpoints for connecting a cloud account and picking folders.

Thin over `app.services.connections`, which owns the OAuth exchange, the
encryption and the commits.

**There is no `/feeds` collection any more.** `POST /api/v1/feeds` used to
create a `FeedRow` with nothing recording what the folder brings in, which is
exactly the folder-shaped configuration `app.api.routes.integrations` replaces:
a watched folder is now the *transport* of an integration, created and removed
through the integration that owns it. Leaving the route in place would leave
one write path that produces rows the panel cannot describe.

There is deliberately **no callback route here, and none anywhere**, even now
that Dropbox redirects the athlete back. The callback is a *frontend page* —
`/settings/dropbox/callback` — which reads the code out of its own query
string and posts it to `POST /connections/dropbox/complete` below, with the
athlete's session cookie. The redirect lands in the browser they are already
logged in to, so nothing unauthenticated ever has to reach arc: `OPEN_PATHS`
is unchanged and every route here sits behind the session guard like the rest
of `/api/v1`.

A backend callback would have had to be public — Dropbox's redirect carries no
cookie arc can require, because it is a fresh navigation from dropbox.com —
and that is a route on the open internet accepting an authorization code, on a
box whose whole security posture is "nothing on the internet reaches it".
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ActorDep
from app.api.schemas.connections import (
    ConnectionList,
    ConnectionRead,
    DropboxAppKeySubmit,
    DropboxAuthorizationRead,
    DropboxAuthorizationStart,
    DropboxCodeSubmit,
    DropboxSetupRead,
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


@router.get("/dropbox/setup")
async def read_dropbox_setup(service: ServiceDep) -> DropboxSetupRead:
    """Whether Dropbox can be connected yet, and on whose app key.

    **Its own endpoint rather than a field on `GET /connections`.** The
    connection list is empty at exactly the moment this answer is needed — the
    athlete has registered nothing and connected nothing — so folding
    `app_key_set` into it would leave the add flow with nowhere to read it
    from, and the first sign of a missing app key would be a 422 from
    `POST /dropbox/authorize` after a click that should never have been
    offered.
    """
    setup = await service.dropbox_setup()
    return DropboxSetupRead(app_key_set=setup.app_key_set, source=setup.source)


@router.put("/dropbox/app", responses=BAD_BODY | INVALID | CONFLICT)
async def set_dropbox_app_key(
    service: ServiceDep, actor: ActorDep, submitted: DropboxAppKeySubmit
) -> DropboxSetupRead:
    """Store the app key of the Dropbox app the athlete registered.

    Takes effect immediately, in this process: the next authorize call reads
    it back from the database rather than from a `Settings` object frozen at
    boot, which is what makes connecting possible without a restart.

    PUT rather than POST because the resource is singular and the write is
    idempotent — arc holds one Dropbox app, and setting it twice leaves the
    same one row.
    """
    setup = await service.set_dropbox_app_key(app_key=submitted.app_key, actor=actor)
    return DropboxSetupRead(app_key_set=setup.app_key_set, source=setup.source)


@router.delete("/dropbox/app", status_code=status.HTTP_204_NO_CONTENT)
async def clear_dropbox_app_key(service: ServiceDep, actor: ActorDep) -> None:
    """Forget the stored app key and fall back to `DROPBOX__APP_KEY`.

    204 whether or not anything was stored: the desired state — arc holds no
    app key of its own — is what the athlete asked for, and it is true either
    way.
    """
    await service.clear_dropbox_app_key(actor=actor)


@router.post("/dropbox/authorize", responses=BAD_BODY | INVALID)
async def start_dropbox_authorization(
    service: ServiceDep,
    actor: ActorDep,
    submitted: DropboxAuthorizationStart = DropboxAuthorizationStart(),
) -> DropboxAuthorizationRead:
    """Begin connecting Dropbox: get the link the athlete opens.

    The body is optional and carries one field. With a `redirect_uri` the link
    carries it and a `state`, and Dropbox sends the athlete back to that page
    with the code in its query string. Without one — an empty body, which is
    what the step sends when the browser's origin is not one Dropbox will
    redirect to — the link is the pre-existing paste URL and Dropbox shows the
    code on screen.

    The URI is the *browser's*, not a header's, and the service decides
    whether Dropbox will accept it before anything is stored.
    """
    started = await service.start_dropbox_authorization(
        actor=actor, redirect_uri=submitted.redirect_uri
    )
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
    """Finish connecting Dropbox with the code that came back.

    Called by the athlete's own browser either way: by the form they pasted
    into, or by the callback page at `/settings/dropbox/callback` reading its
    own query string. The `state` is forwarded verbatim when there is one —
    the service, not this route, decides whether it matches.
    """
    return ConnectionRead.model_validate(
        await service.complete_dropbox(
            code=submitted.code, state=submitted.state, actor=actor
        )
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
