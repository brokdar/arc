"""HTTP endpoints for connecting a cloud account and picking folders.

Thin over `app.services.connections`, which owns the OAuth exchange, the
encryption and the commits. Two routers rather than one because feeds are a
collection in their own right: a feed is addressed by its own id for `PATCH`
and `DELETE`, and nesting it under `/connections/{id}/feeds/{feed_id}` would
make every write carry an id it does not need.

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
    DropboxAppKeySubmit,
    DropboxAuthorizationRead,
    DropboxCodeSubmit,
    DropboxSetupRead,
    FeedCreate,
    FeedRead,
    FeedUpdate,
    FolderList,
    FolderRead,
)
from app.core.exceptions import ErrorDetail, ValidationErrorDetail
from app.persistence.connections import MAX_REMOTE_PATH_LENGTH
from app.persistence.db import SessionDep
from app.services.connections import ConnectionService

router = APIRouter(prefix="/connections", tags=["connections"])
feeds_router = APIRouter(prefix="/feeds", tags=["connections"])

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
    `app_key_set` into it would leave the panel with nowhere to read it from,
    and the first sign of a missing app key would be a 422 from
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


@feeds_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=BAD_BODY | NOT_FOUND | CONFLICT,
)
async def create_feed(
    service: ServiceDep, actor: ActorDep, submitted: FeedCreate
) -> FeedRead:
    """Watch a folder on a connection.

    The path is normalised, so `/Apps/WahooFitness/` and `/apps/wahoofitness`
    are one feed and the second attempt is a 409.
    """
    return FeedRead.model_validate(
        await service.create_feed(
            connection_id=submitted.connection_id,
            remote_path=submitted.remote_path,
            actor=actor,
        )
    )


@feeds_router.patch("/{feed_id}", responses=BAD_BODY | NOT_FOUND)
async def update_feed(
    service: ServiceDep, actor: ActorDep, feed_id: uuid.UUID, submitted: FeedUpdate
) -> FeedRead:
    """Turn a feed's polling on or off. The cursor is kept either way."""
    return FeedRead.model_validate(
        await service.set_feed_enabled(feed_id, enabled=submitted.enabled, actor=actor)
    )


@feeds_router.delete(
    "/{feed_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_feed(service: ServiceDep, actor: ActorDep, feed_id: uuid.UUID) -> None:
    """Stop watching a folder and forget its polling state."""
    await service.delete_feed(feed_id, actor=actor)
