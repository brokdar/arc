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
    MAX_PROTOCOL_CHARS,
    AnchorSource,
    AnchorType,
    AnchorUnit,
    Provenance,
    StalenessState,
)

#: Bounded by the domain's own constant (`AnchorVersion` refuses more), so the
#: schema's 422 and the domain's refusal can never disagree about the limit.
Protocol = Annotated[PostgresText, Field(min_length=1, max_length=MAX_PROTOCOL_CHARS)]
#: The appendable anchor types, spelled as a `Literal` so the contract does
#: not advertise the reserved `cp`/`w_prime` (which the service also refuses,
#: for callers that do not come through this schema). `resting_hr` joined them
#: with WP-5: HRSS reads it, so the athlete has to be able to enter it.
WritableAnchorType = Literal[
    AnchorType.FTP,
    AnchorType.LTHR,
    AnchorType.MAX_HR,
    AnchorType.RESTING_HR,
]
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


class RepriceReportRead(BaseModel):
    """What appending an anchor version did to previously recorded sessions.

    Mirrors `app.ingest.repricing.RepriceReport`, field for field.
    """

    #: Sessions whose current metric artefact was checked.
    examined: int
    #: Sessions that got a new metric version priced against the new anchor.
    repriced: int
    #: Sessions whose price the new version could not change.
    unchanged: int
    #: Sessions whose recompute failed; each stays individually recomputable.
    failed: int
    #: Non-null only when the scan itself failed after the append committed —
    #: the counts are then zero and mean "unknown".
    note: str | None


class AnchorVersionAppended(AnchorVersionRead):
    """The appended version, plus what appending it did to the history."""

    reprice: RepriceReportRead


AnchorVersionsPage = Page[AnchorVersionRead]
