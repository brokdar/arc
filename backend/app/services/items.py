"""Business logic for items.

Services sit below ``app.api``: they speak in persistence/domain objects and
plain values, never in API request/response schemas. Translating to and from
the wire format is the router's job — that is what keeps the layered
import-linter contracts in ``pyproject.toml`` satisfiable.

Two rules this module illustrates for every service that follows:

* **Construction lives here** (:meth:`ItemService.from_session`), not in an
  adapter. `app.api` and `app.mcp` may not import each other, so wiring done
  inside a route is wiring the MCP tools cannot reuse.
* **The service owns the commit.** A mutating use-case ends with
  `persistence.db.commit`, inside the request/tool boundary, so a failure at
  COMMIT still becomes a proper error response (see `app/persistence/db.py`).
"""

import uuid
from collections.abc import Mapping
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.domain.actor import Actor
from app.persistence.db import commit
from app.persistence.items import Item, ItemRepository


class ItemService:
    """Use-cases for items. Raises AppError subclasses on domain violations.

    Every mutating method takes ``actor``. Nothing consumes it yet — WP-1 adds
    the `audit_log` table every write path appends to — but the parameter is
    the seam that makes the caller state its identity, and it is threaded from
    the API dependency and (WP-8) the MCP key label already.
    """

    def __init__(self, session: AsyncSession, repository: ItemRepository) -> None:
        self._session = session
        self._repository = repository

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, ItemRepository(session))

    async def get(self, item_id: uuid.UUID) -> Item:
        """Return a single item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        return item

    async def list(self, *, offset: int, limit: int) -> tuple[list[Item], int]:
        """Return a page of items plus the total count."""
        return await self._repository.list(offset=offset, limit=limit)

    async def create(self, *, actor: Actor, name: str, description: str | None) -> Item:
        """Create a new item with a unique name.

        Args:
            actor: Who is performing the write; recorded on the audit trail.
            name: The item's unique name.
            description: Optional free text.
        """
        if await self._repository.get_by_name(name) is not None:
            raise ConflictError(f"Item with name {name!r} already exists")
        item = await self._repository.add(Item(name=name, description=description))
        await commit(self._session)
        return item

    async def update(
        self, item_id: uuid.UUID, updates: Mapping[str, Any], *, actor: Actor
    ) -> Item:
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
        item = await self._repository.add(item)
        await commit(self._session)
        return item

    async def delete(self, item_id: uuid.UUID, *, actor: Actor) -> None:
        """Delete an item by id."""
        item = await self._repository.get(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        await self._repository.delete(item)
        await commit(self._session)
