"""Request/response schemas for plans and their week tags.

A plan's dates are half-open, `[starts_on, ends_before)` (see
`app.domain.plans`), and named so the exclusive end cannot be read as the
last day.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from app.api.validation import PostgresText
from app.domain.plans import MAX_PLAN_NAME_CHARS, PlanPhase, PlanWeekTag

PlanName = Annotated[PostgresText, Field(min_length=1, max_length=MAX_PLAN_NAME_CHARS)]


class PlanRead(BaseModel):
    """One plan."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phase: PlanPhase
    #: The plan's sessions are to be done as written: rules that would adapt
    #: them (progression, swaps) stand down for it.
    follow_exactly: bool
    active: bool
    #: The plan of every date no other active plan covers. Undated and always
    #: active.
    is_default: bool = Field(validation_alias="default")
    #: First day, inclusive; null is unbounded.
    starts_on: dt.date | None
    #: First day after the plan; null is unbounded.
    ends_before: dt.date | None
    created_at: dt.datetime
    updated_at: dt.datetime


class PlanCreate(BaseModel):
    """Payload for creating a plan."""

    model_config = ConfigDict(extra="forbid")

    name: PlanName
    phase: PlanPhase
    follow_exactly: bool = False
    active: bool = True
    starts_on: dt.date | None = None
    ends_before: dt.date | None = None


class PlanUpdate(BaseModel):
    """A partial change to a plan. Omitted fields are left alone.

    Only the dates take ``null``, meaning "unbounded on that side"
    (`.claude/rules/api-nullability.md`).
    """

    model_config = ConfigDict(extra="forbid")

    name: PlanName | SkipJsonSchema[None] = None
    phase: PlanPhase | SkipJsonSchema[None] = None
    follow_exactly: bool | SkipJsonSchema[None] = None
    active: bool | SkipJsonSchema[None] = None
    starts_on: dt.date | None = None
    ends_before: dt.date | None = None


class PlanWeekTagsWrite(BaseModel):
    """The whole set of tags one week carries. ``[]`` clears it."""

    model_config = ConfigDict(extra="forbid")

    tags: list[PlanWeekTag] = Field(max_length=len(PlanWeekTag))


class PlanWeekTagsRead(BaseModel):
    """The tags on one week of one plan."""

    plan_id: uuid.UUID
    #: The Monday the week starts on.
    week_start: dt.date
    tags: list[PlanWeekTag]
