"""Response schemas for computed zones.

Read-only by construction: zones are derived from an anchor version and a zone
model, never stored, so there is nothing to POST.
"""

from pydantic import BaseModel, ConfigDict

from app.api.schemas.anchors import AnchorVersionRead
from app.domain.anchors import AnchorUnit
from app.domain.zones import ZoneModel


class ZoneRead(BaseModel):
    """One half-open zone band ``[lower, upper)``."""

    model_config = ConfigDict(from_attributes=True)

    index: int
    name: str
    lower_pct: float
    #: Null on the top zone: there is no ceiling on a sprint.
    upper_pct: float | None
    lower: float
    upper: float | None
    unit: AnchorUnit


class ZonesRead(BaseModel):
    """The computed zones, with the two inputs they came from.

    The anchor version is returned in full because it is the provenance of
    every number in ``zones``; a client that caches the zones caches the
    version id with them.
    """

    model_config = ConfigDict(from_attributes=True)

    anchor_version: AnchorVersionRead
    model: ZoneModel
    zones: list[ZoneRead]
