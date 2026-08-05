"""Use-case for zones: resolve an anchor version, then derive.

Read-only, so there is no commit and no audit row: nothing is written because
zones are never stored (build-plan invariant 3). The whole service is the
resolution step — *which* anchor version, and *which* model — around the pure
`app.domain.zones.zones_for`.

Two entry points rather than one with a "give me exactly one of these"
selector: a stored prescription asks for a pinned version (reproducible), a
chart asks for whatever is in force. Splitting them means neither caller can
express the combination that has no meaning.
"""

import uuid
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import domain_rules
from app.domain.anchors import AnchorType
from app.domain.zones import Zone, ZoneModel, default_zone_model, zones_for
from app.persistence.anchors import AnchorVersionRow
from app.services.anchors import AnchorService


@dataclass(frozen=True, slots=True)
class ResolvedZones:
    """The zones, plus the two inputs they were derived from.

    Returned together because a zone list on its own is not interpretable:
    the anchor version is the provenance of every number in it.
    """

    anchor_version: AnchorVersionRow
    model: ZoneModel
    zones: list[Zone]


class ZoneService:
    """Derives zones from a stored anchor version."""

    def __init__(self, anchors: AnchorService) -> None:
        self._anchors = anchors

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(AnchorService.from_session(session))

    async def for_current_anchor(
        self, anchor_type: AnchorType, *, model: ZoneModel | None = None
    ) -> ResolvedZones:
        """Zones from the version of ``anchor_type`` in force now.

        Raises:
            NotFoundError: When no version of that type is in force.
            ValidationError: When no zone model derives from that anchor type,
                or the named model does not.
        """
        return self._derive(await self._anchors.current(anchor_type), model)

    async def for_version(
        self, anchor_version_id: uuid.UUID, *, model: ZoneModel | None = None
    ) -> ResolvedZones:
        """Zones from one specific, frozen anchor version.

        Raises:
            NotFoundError: When the version does not exist.
            ValidationError: When the model does not derive from its type.
        """
        return self._derive(await self._anchors.get(anchor_version_id), model)

    def _derive(self, row: AnchorVersionRow, model: ZoneModel | None) -> ResolvedZones:
        """Apply the zone model — the named one, or the anchor type's own."""
        with domain_rules():
            resolved_model = model or default_zone_model(row.anchor_type)
            zones = zones_for(row.to_domain(), resolved_model)
        return ResolvedZones(anchor_version=row, model=resolved_model, zones=zones)
