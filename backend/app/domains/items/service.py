"""Business logic for the items domain."""

import uuid

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import PageParams
from app.domains.items.models import Item
from app.domains.items.repository import ItemRepository
from app.domains.items.schemas import ItemCreate, ItemRead, ItemsPage, ItemUpdate


class ItemService:
    """Use-cases for items. Raises AppError subclasses on domain violations."""

    def __init__(self, repository: ItemRepository) -> None:
        self._repository = repository

    async def get(self, item_id: uuid.UUID) -> ItemRead:
        """Return a single item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        return ItemRead.model_validate(item)

    async def list(self, page: PageParams) -> ItemsPage:
        """Return a page of items."""
        items, total = await self._repository.list(offset=page.offset, limit=page.limit)
        return ItemsPage(
            items=[ItemRead.model_validate(item) for item in items],
            total=total,
            offset=page.offset,
            limit=page.limit,
        )

    async def create(self, payload: ItemCreate) -> ItemRead:
        """Create a new item with a unique name."""
        if await self._repository.get_by_name(payload.name) is not None:
            raise ConflictError(f"Item with name {payload.name!r} already exists")
        item = await self._repository.add(
            Item(name=payload.name, description=payload.description)
        )
        return ItemRead.model_validate(item)

    async def update(self, item_id: uuid.UUID, payload: ItemUpdate) -> ItemRead:
        """Partially update an existing item."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        updates = payload.model_dump(exclude_unset=True)
        new_name = updates.get("name")
        if (
            new_name is not None
            and new_name != item.name
            and await self._repository.get_by_name(new_name) is not None
        ):
            raise ConflictError(f"Item with name {new_name!r} already exists")
        for field, value in updates.items():
            setattr(item, field, value)
        return ItemRead.model_validate(await self._repository.add(item))

    async def delete(self, item_id: uuid.UUID) -> None:
        """Delete an item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        await self._repository.delete(item)
