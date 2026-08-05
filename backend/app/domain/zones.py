"""Training zones, derived from a declared zone model and one anchor version.

Invariant 3 of the build plan: **zones are always computed, never stored.**
A stored zone table is a copy of two things that change independently (the
anchor and the model), and the copy is what goes stale. Everything here is a
pure function of ``(anchor_version, zone_model)``, so a zone chart, a
prescription target and a time-in-zone metric cannot disagree.

The boundary percentages below are the published schemes, reproduced with two
deliberate conventions:

* **Bands are half-open** — ``lower <= x < upper`` — and contiguous, so the
  set of zones partitions ``[0, inf)`` with no gap and no overlap for any
  value. Published tables state inclusive integer bands with one-point gaps
  (Friel's HR zones run 81-89 then 90-93); those gaps are closed *upward*
  onto the next zone's lower bound, which is where a value of 89.4 %LTHR
  belongs anyway.
* **The top zone is open-ended** (``upper`` / ``upper_pct`` are ``None``).
  There is no ceiling on a sprint.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.anchors import AnchorType, AnchorUnit, AnchorVersion


class ZoneModel(StrEnum):
    """The zone schemes the MVP supports.

    One per channel: power zones off FTP, heart-rate zones off LTHR. Adding a
    model is adding a member plus a row in :data:`_ZONE_SCHEMES`.
    """

    #: Coggan's 7-zone power scheme, as %FTP.
    COGGAN_7 = "coggan_7"
    #: A 5-zone heart-rate scheme, as %LTHR (Friel's 7 bands collapsed to 5).
    LTHR_5 = "lthr_5"


#: The anchor type each model derives from. A zone model is not a free choice:
#: applying %FTP boundaries to a heart rate produces plausible-looking
#: nonsense, so the pairing is checked rather than trusted.
ZONE_MODEL_ANCHOR: dict[ZoneModel, AnchorType] = {
    ZoneModel.COGGAN_7: AnchorType.FTP,
    ZoneModel.LTHR_5: AnchorType.LTHR,
}

#: The model used when the caller names an anchor type but no model.
DEFAULT_ZONE_MODEL: dict[AnchorType, ZoneModel] = {
    anchor_type: model for model, anchor_type in ZONE_MODEL_ANCHOR.items()
}

#: ``(zone name, lower bound as a fraction of the anchor)``, ascending.
#: The upper bound of each zone is the lower bound of the next, which is what
#: makes the scheme a partition by construction rather than by proofreading.
#:
#: Coggan 7 (%FTP): 55 / 75 / 90 / 105 / 120 / 150 — "Training and Racing with
#: a Power Meter", Allen & Coggan, the canonical table.
#: LTHR 5 (%LTHR): 81 / 90 / 94 / 100 — Friel's cycling heart-rate zones with
#: 5a/5b/5c merged into one open-ended Z5, since the MVP scores nothing that
#: distinguishes them.
_ZONE_SCHEMES: dict[ZoneModel, Sequence[tuple[str, float]]] = {
    ZoneModel.COGGAN_7: (
        ("Active Recovery", 0.00),
        ("Endurance", 0.55),
        ("Tempo", 0.75),
        ("Threshold", 0.90),
        ("VO2max", 1.05),
        ("Anaerobic Capacity", 1.20),
        ("Neuromuscular Power", 1.50),
    ),
    ZoneModel.LTHR_5: (
        ("Recovery", 0.00),
        ("Aerobic", 0.81),
        ("Tempo", 0.90),
        ("SubThreshold", 0.94),
        ("SuperThreshold", 1.00),
    ),
}


@dataclass(frozen=True, slots=True)
class Zone:
    """One half-open band ``[lower, upper)`` of a computed zone scheme.

    Args:
        index: 1-based position in the scheme, so ``Z3`` is ``index == 3``.
        name: The scheme's own name for the zone.
        lower_pct: Lower bound as a fraction of the anchor value (``0.55`` is
            55 %FTP). Kept alongside the absolute bound because prescriptions
            are written in percentages and reading them back should not
            require dividing.
        upper_pct: Upper bound as a fraction, or ``None`` for the top zone.
        lower: Absolute lower bound, inclusive, in :attr:`unit`.
        upper: Absolute upper bound, exclusive, or ``None`` for the top zone.
        unit: Unit of :attr:`lower` and :attr:`upper` — the anchor's own.
    """

    index: int
    name: str
    lower_pct: float
    upper_pct: float | None
    lower: float
    upper: float | None
    unit: AnchorUnit

    def contains(self, value: float) -> bool:
        """Whether ``value`` (in :attr:`unit`) falls in this band."""
        if value < self.lower:
            return False
        return self.upper is None or value < self.upper


def zones_for(anchor_version: AnchorVersion, model: ZoneModel) -> list[Zone]:
    """Compute the zones of ``model`` from one anchor version.

    Args:
        anchor_version: The version the zones are derived from. Callers store
            *its id*, never the result — that is what makes a later
            recomputation reproducible.
        model: The declared zone model.

    Returns:
        The zones, ascending, contiguous, the last one open-ended.

    Raises:
        ValueError: When ``model`` does not derive from this anchor's type.
    """
    required = ZONE_MODEL_ANCHOR[model]
    if anchor_version.anchor_type is not required:
        raise ValueError(
            f"zone model {model.value} derives from {required.value}, not "
            f"{anchor_version.anchor_type.value}"
        )

    scheme = _ZONE_SCHEMES[model]
    anchor_value = anchor_version.value
    zones: list[Zone] = []
    for index, (name, lower_pct) in enumerate(scheme, start=1):
        upper_pct = scheme[index][1] if index < len(scheme) else None
        zones.append(
            Zone(
                index=index,
                name=name,
                lower_pct=lower_pct,
                upper_pct=upper_pct,
                lower=lower_pct * anchor_value,
                upper=None if upper_pct is None else upper_pct * anchor_value,
                unit=anchor_version.unit,
            )
        )
    return zones


def default_zone_model(anchor_type: AnchorType) -> ZoneModel:
    """Return the zone model that derives from ``anchor_type``.

    Raises:
        ValueError: For anchor types no MVP model derives from (``MAX_HR``,
            and the reserved ``CP``/``W_PRIME``).
    """
    model = DEFAULT_ZONE_MODEL.get(anchor_type)
    if model is None:
        supported = ", ".join(sorted(t.value for t in DEFAULT_ZONE_MODEL))
        raise ValueError(
            f"no zone model derives from {anchor_type.value}; zones are "
            f"available for: {supported}"
        )
    return model


def zone_for_value(zones: Sequence[Zone], value: float) -> Zone | None:
    """Return the zone ``value`` falls in, or ``None`` if it is below Z1."""
    return next((zone for zone in zones if zone.contains(value)), None)
