"""`POST /sync`: the phone's outbox, uploaded. Thin layer over the service.

**An incompatible `schema_version` is a 409 with `code:
"schema_version_unsupported"`.** The phone reads the code, keeps every queued
item and shows "Update arc to upload"; nothing in the batch is applied.
Displaced: a silent retry, where the phone resends a batch the server will
never understand until the queue grows past what the athlete would accept to
lose — and a 422, which the phone would read as "this item is broken" and set
aside work that is fine. 409 because the request conflicts with the server's
state (the build it runs), not with any rule the batch broke. Pinned by
`tests/unit/test_sync_api.py::test_version_skew`.

A **422** is one mutation the server cannot apply (`code:
"mutation_rejected"`, naming its `mutation_id`) or a malformed body; either
way nothing is applied and the phone sets the named item aside and resends
the rest. A **401** applies nothing; the phone keeps its queue and drains it
after the next login.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import ActorDep
from app.api.schemas.sync import (
    ActiveSessionRead,
    MutationResultRead,
    SchemaVersionUnsupported,
    SyncBatch,
    SyncRejected,
    SyncResult,
)
from app.core.exceptions import ErrorDetail
from app.domain.sync import MAX_SCHEMA_VERSION, MutationKind
from app.persistence.db import SessionDep
from app.services.sync import MutationInput, SyncService

router = APIRouter(prefix="/sync", tags=["sync"])

type Responses = dict[int | str, dict[str, Any]]
RESPONSES: Responses = {
    400: {"model": ErrorDetail, "description": "Malformed body"},
    409: {
        "model": SchemaVersionUnsupported,
        "description": "The batch's schema_version is not one this server "
        "understands; update the app",
    },
    422: {
        "model": SyncRejected,
        "description": "One mutation cannot be applied, or the body is "
        "malformed; nothing was applied",
    },
}

#: The envelope fields every mutation shares; the rest are the kind's own.
_ENVELOPE = {
    "kind",
    "mutation_id",
    "client_edit_time",
    "session_client_id",
    "set_client_id",
    "exercise_id",
}


def get_service(session: SessionDep) -> SyncService:
    """Bind the service to a request-scoped session."""
    return SyncService.from_session(session)


ServiceDep = Annotated[SyncService, Depends(get_service)]


@router.post("", responses=RESPONSES)
async def sync(service: ServiceDep, actor: ActorDep, payload: SyncBatch) -> SyncResult:
    """Apply the outbox's mutations once, in order, all or nothing."""
    results = await service.apply(
        [
            MutationInput(
                mutation_id=mutation.mutation_id,
                kind=MutationKind(mutation.kind),
                client_edit_time=mutation.client_edit_time,
                session_client_id=getattr(mutation, "session_client_id", None),
                set_client_id=getattr(mutation, "set_client_id", None),
                exercise_id=getattr(mutation, "exercise_id", None),
                fields=mutation.model_dump(
                    mode="json", exclude=_ENVELOPE, exclude_unset=True
                ),
            )
            for mutation in payload.mutations
        ],
        schema_version=payload.schema_version,
        actor=actor,
    )
    return SyncResult(
        schema_version=MAX_SCHEMA_VERSION,
        results=[
            MutationResultRead(mutation_id=r.mutation_id, status=r.status)
            for r in results
        ],
        active_sessions=[
            ActiveSessionRead.model_validate(one)
            for one in await service.active_sessions()
        ],
    )
