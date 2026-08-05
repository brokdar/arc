"""Request/response schemas for anchor versions.

There is no update payload here, and there never will be: anchor history is
append-only (build-plan invariant 3).
"""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.pagination import Page
from app.api.validation import PostgresText
from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    Provenance,
    StalenessState,
)

Protocol = Annotated[PostgresText, Field(min_length=1, max_length=200)]
#: The appendable anchor types — the MVP three, spelled as a `Literal` so the
#: contract does not advertise the reserved `cp`/`w_prime` (which the service
#: also refuses, for callers that do not come through this schema).
WritableAnchorType = Literal[AnchorType.FTP, AnchorType.LTHR, AnchorType.MAX_HR]
#: Bounds are per anchor type (`app.domain.anchors.ANCHOR_BOUNDS`), so the
#: schema only rejects what is nonsensical for every type; the domain gives
#: the precise message.
AnchorValue = Annotated[float, Field(gt=0, le=100_000)]


class AnchorVersionCreate(BaseModel):
    """Payload for appending a version to an anchor's history."""

    model_config = ConfigDict(extra="forbid")

    anchor_type: WritableAnchorType
    value: AnchorValue
    provenance: Provenance
    #: Defaults to the anchor type's own unit; a different one is rejected
    #: rather than converted.
    unit: AnchorUnit | None = None
    #: Required when ``provenance`` is ``tested``.
    protocol: Protocol | None = None
    #: The date the value applies from; today when omitted. May be back-dated.
    effective_date: dt.date | None = None
    ci_low: AnchorValue | None = None
    ci_high: AnchorValue | None = None

    @model_validator(mode="after")
    def _confidence_interval_is_ordered(self) -> AnchorVersionCreate:
        if (
            self.ci_low is not None
            and self.ci_high is not None
            and self.ci_low > self.ci_high
        ):
            raise ValueError("ci_low must not exceed ci_high")
        return self


class AnchorVersionRead(BaseModel):
    """One anchor version as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    anchor_type: AnchorType
    value: float
    unit: AnchorUnit
    provenance: Provenance
    protocol: str | None
    effective_date: dt.date
    ci_low: float | None
    ci_high: float | None
    source: AnchorSource
    #: Hardcoded `fresh` in the MVP — the staleness model is deferred.
    staleness_state: StalenessState
    created_at: dt.datetime


AnchorVersionsPage = Page[AnchorVersionRead]
