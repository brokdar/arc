"""Business logic for items.

Services sit below ``app.api``: they speak in persistence/domain objects and
plain values, never in API request/response schemas. Translating to and from
the wire format is the router's job — that is what keeps the layered
import-linter contracts in ``pyproject.toml`` satisfiable.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from app.core.exceptions import ConflictError, NotFoundError
from app.persistence.items import Item, ItemRepository


class ItemService:
    """Use-cases for items. Raises AppError subclasses on domain violations."""

    def __init__(self, repository: ItemRepository) -> None:
        self._repository = repository

    async def get(self, item_id: uuid.UUID) -> Item:
        """Return a single item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        return item

    async def list(self, *, offset: int, limit: int) -> tuple[list[Item], int]:
        """Return a page of items plus the total count."""
        return await self._repository.list(offset=offset, limit=limit)

    async def create(self, *, name: str, description: str | None) -> Item:
        """Create a new item with a unique name."""
        if await self._repository.get_by_name(name) is not None:
            raise ConflictError(f"Item with name {name!r} already exists")
        return await self._repository.add(Item(name=name, description=description))

    async def update(self, item_id: uuid.UUID, updates: Mapping[str, Any]) -> Item:
        """Partially update an existing item.

        ``updates`` holds only the fields the caller explicitly supplied
        (i.e. pydantic's ``model_dump(exclude_unset=True)``).
        """
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        new_name = updates.get("name")
        if (
            new_name is not None
            and new_name != item.name
            and await self._repository.get_by_name(new_name) is not None
        ):
            raise ConflictError(f"Item with name {new_name!r} already exists")
        for field, value in updates.items():
            setattr(item, field, value)
        return await self._repository.add(item)

    async def delete(self, item_id: uuid.UUID) -> None:
        """Delete an item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        await self._repository.delete(item)
