"""HTTP endpoints for items. Thin layer over the service.

The only logic here is HTTP: parsing input schemas and serializing the
service's persistence objects into response schemas.

This router carries no auth dependency of its own: `app.main` mounts it on
the protected `/api/v1` router (``Depends(require_session)``), which also
declares the shared 401 response. New routers go there too unless they have a
deliberate reason to be public.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import ActorDep
from app.api.pagination import PageParamsDep
from app.api.schemas.items import ItemCreate, ItemRead, ItemsPage, ItemUpdate
from app.core.exceptions import ErrorDetail
from app.persistence.db import SessionDep
from app.services.items import ItemService

router = APIRouter(prefix="/items", tags=["items"])

# Declared per-status error responses become part of the OpenAPI contract:
# generated frontend types, typed MSW mocks, and Schemathesis all rely on them.
type Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "Item not found"}}
CONFLICT: Responses = {409: {"model": ErrorDetail, "description": "Name already taken"}}
# FastAPI returns 400 (not 422) for bodies that fail to parse at all.
BAD_BODY: Responses = {400: {"model": ErrorDetail, "description": "Malformed body"}}


def get_service(session: SessionDep) -> ItemService:
    """Bind the service to a request-scoped session.

    The wiring itself lives in the service layer, so `app.mcp` — which may not
    import `app.api` — builds the same object the same way.
    """
    return ItemService.from_session(session)


ServiceDep = Annotated[ItemService, Depends(get_service)]


@router.get("")
async def list_items(service: ServiceDep, page: PageParamsDep) -> ItemsPage:
    """List items, newest first."""
    items, total = await service.list(offset=page.offset, limit=page.limit)
    return ItemsPage(
        items=[ItemRead.model_validate(item) for item in items],
        total=total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=CONFLICT | BAD_BODY)
async def create_item(
    service: ServiceDep, actor: ActorDep, payload: ItemCreate
) -> ItemRead:
    """Create a new item."""
    item = await service.create(
        actor=actor, name=payload.name, description=payload.description
    )
    return ItemRead.model_validate(item)


@router.get("/{item_id}", responses=NOT_FOUND)
async def get_item(service: ServiceDep, item_id: uuid.UUID) -> ItemRead:
    """Get a single item."""
    return ItemRead.model_validate(await service.get(item_id))


@router.patch("/{item_id}", responses=NOT_FOUND | CONFLICT | BAD_BODY)
async def update_item(
    service: ServiceDep, actor: ActorDep, item_id: uuid.UUID, payload: ItemUpdate
) -> ItemRead:
    """Partially update an item."""
    item = await service.update(
        item_id, payload.model_dump(exclude_unset=True), actor=actor
    )
    return ItemRead.model_validate(item)


@router.delete(
    "/{item_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_item(service: ServiceDep, actor: ActorDep, item_id: uuid.UUID) -> None:
    """Delete an item."""
    await service.delete(item_id, actor=actor)
