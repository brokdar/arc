"""Request/response schemas for the athlete profile."""

import datetime as dt
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.validation import PostgresJsonObject, PostgresText
from app.domain.athlete import MAX_HEIGHT_CM, MIN_HEIGHT_CM, Sex

#: Constraints live INSIDE the union member: applied to the union itself,
#: pydantic would try `min_length` against None and 500 with a TypeError.
AthleteName = Annotated[PostgresText, Field(min_length=1, max_length=200)]
HeightCm = Annotated[float, Field(ge=MIN_HEIGHT_CM, le=MAX_HEIGHT_CM)]


class AthleteRead(BaseModel):
    """The athlete profile as returned by the API.

    Every field is nullable: the profile is bootstrapped empty on first access
    and filled in from the UI.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str | None
    date_of_birth: dt.date | None
    sex: Sex
    height_cm: float | None
    #: Free-form per-discipline capability stub; opaque to the MVP.
    capabilities: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime


class AthleteUpdate(BaseModel):
    """Payload for partially updating the profile.

    Omitted fields are left unchanged; an explicit ``null`` clears a field
    (for ``sex`` and ``capabilities``, "clear" means back to ``unspecified``
    and ``{}`` — those two have an empty value rather than an absent one).
    """

    # `extra="forbid"` so a typo'd field name is a 422 rather than a silent
    # no-op: with one athlete and no undo, a lost edit is expensive.
    model_config = ConfigDict(extra="forbid")

    name: AthleteName | None = None
    date_of_birth: dt.date | None = None
    sex: Sex | None = None
    height_cm: HeightCm | None = None
    capabilities: PostgresJsonObject | None = None

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_the_future(cls, value: dt.date | None) -> dt.date | None:
        # Checked here rather than in the domain: "is this in the future"
        # needs a clock, and `app.domain` stays free of ambient state. The
        # lower bound (EARLIEST_BIRTH_YEAR) is a domain rule and lives there.
        if value is not None and value > dt.datetime.now(dt.UTC).date():
            raise ValueError("date_of_birth cannot be in the future")
        return value
