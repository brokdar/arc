"""Request/response schemas for items."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from app.api.pagination import Page
from app.api.validation import PostgresText

# Constraints live INSIDE the union member: applied to the union itself,
# pydantic would try `min_length` against None and 500 with a TypeError.
ItemName = Annotated[PostgresText, Field(min_length=1, max_length=255)]
ItemDescription = Annotated[PostgresText, Field(max_length=2000)]


class ItemCreate(BaseModel):
    """Payload for creating an item."""

    name: ItemName
    description: ItemDescription | None = None


class ItemUpdate(BaseModel):
    """Payload for partially updating an item.

    Omitted fields are left unchanged; ``description`` may be set to null to
    clear it, but ``name`` is non-nullable.
    """

    # SkipJsonSchema[None] keeps `name` optional at runtime while the OpenAPI
    # contract truthfully says "string, not nullable" — explicit null is
    # rejected by the validator below.
    name: ItemName | SkipJsonSchema[None] = None
    description: ItemDescription | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str | None:
        # Only runs when the field is explicitly provided (defaults are not
        # validated), so this rejects `{"name": null}` while allowing omission.
        if value is None:
            raise ValueError("name cannot be null; omit the field to keep it")
        return value


class ItemRead(BaseModel):
    """Item as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


ItemsPage = Page[ItemRead]
