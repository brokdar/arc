"""Item ORM model and its repository. No business logic here."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db import Base


class Item(Base):
    """Example entity — replace with your first real domain model."""

    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ItemRepository:
    """SQLAlchemy repository for :class:`Item`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_id: uuid.UUID) -> Item | None:
        """Return the item with the given id, or None."""
        return await self._session.get(Item, item_id)

    async def get_by_name(self, name: str) -> Item | None:
        """Return the item with the given unique name, or None."""
        result = await self._session.execute(select(Item).where(Item.name == name))
        return result.scalar_one_or_none()

    async def list(self, *, offset: int, limit: int) -> tuple[list[Item], int]:
        """Return a page of items ordered by creation time, plus the total count."""
        total = await self._session.scalar(select(func.count()).select_from(Item))
        result = await self._session.execute(
            select(Item).order_by(Item.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars()), total or 0

    async def add(self, item: Item) -> Item:
        """Persist a new item and refresh server-generated fields."""
        self._session.add(item)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    async def delete(self, item: Item) -> None:
        """Delete an item."""
        await self._session.delete(item)
        await self._session.flush()
