"""Zone derivation — the first property-tested code in the repo.

Zone boundaries are the kind of table that is easy to get subtly wrong (a
transposed digit, a band that starts where the previous one started) and hard
to notice: every number still looks plausible. The properties below are what
"a zone scheme" actually means, so they hold for any scheme added later, not
just the two the MVP ships.
"""

import datetime as dt
from dataclasses import replace

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.domain.anchors import (
    ANCHOR_BOUNDS,
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
)
from app.domain.zones import (
    ZONE_MODEL_ANCHOR,
    ZoneModel,
    default_zone_model,
    zone_for_value,
    zones_for,
)

NOW = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)


def anchor(anchor_type: AnchorType, value: float) -> AnchorVersion:
    """An anchor version with everything but the value held constant."""
    return AnchorVersion(
        anchor_type=anchor_type,
        value=value,
        unit=AnchorUnit.WATT if anchor_type is AnchorType.FTP else AnchorUnit.BPM,
        provenance=Provenance.ESTIMATED,
        effective_date=NOW.date(),
        created_at=NOW,
        source=AnchorSource.ATHLETE,
    )


def anchor_values(anchor_type: AnchorType) -> st.SearchStrategy[float]:
    """Every value the domain accepts for this anchor type."""
    low, high = ANCHOR_BOUNDS[anchor_type]
    return st.floats(
        min_value=low, max_value=high, allow_nan=False, allow_infinity=False
    )


models = st.sampled_from(sorted(ZONE_MODEL_ANCHOR, key=lambda model: model.value))


@st.composite
def anchored_models(draw: st.DrawFn) -> tuple[AnchorVersion, ZoneModel]:
    """A zone model together with an anchor version it applies to."""
    model = draw(models)
    anchor_type = ZONE_MODEL_ANCHOR[model]
    return anchor(anchor_type, draw(anchor_values(anchor_type))), model


# --- the properties a zone scheme must have ----------------------------------


@given(anchored_models())
def test_zones_are_contiguous_and_ordered(
    case: tuple[AnchorVersion, ZoneModel],
) -> None:
    version, model = case

    zones = zones_for(version, model)

    assert [zone.index for zone in zones] == list(range(1, len(zones) + 1))
    assert zones[0].lower == 0.0, "the scheme must start at zero"
    for lower_zone, upper_zone in zip(zones, zones[1:], strict=False):
        # No gap and no overlap: one zone's exclusive upper bound IS the
        # next one's inclusive lower bound.
        assert lower_zone.upper == upper_zone.lower
        assert lower_zone.lower < upper_zone.lower
    assert zones[-1].upper is None, "the top zone must be open-ended"


@given(anchored_models(), st.floats(min_value=0, max_value=3))
def test_every_value_falls_in_exactly_one_zone(
    case: tuple[AnchorVersion, ZoneModel], fraction: float
) -> None:
    version, model = case
    zones = zones_for(version, model)
    value = fraction * version.value

    matches = [zone for zone in zones if zone.contains(value)]

    assert len(matches) == 1, f"{value} matched {[zone.name for zone in matches]}"
    assert zone_for_value(zones, value) is matches[0]


@given(anchored_models(), st.floats(min_value=1.01, max_value=4))
def test_zones_scale_linearly_with_the_anchor(
    case: tuple[AnchorVersion, ZoneModel], factor: float
) -> None:
    version, model = case
    scaled_value = version.value * factor
    low, high = ANCHOR_BOUNDS[version.anchor_type]
    assume(low <= scaled_value <= high)

    zones = zones_for(version, model)
    scaled = zones_for(replace(version, value=scaled_value), model)

    for original, bigger in zip(zones, scaled, strict=True):
        # Boundaries are percentages of the anchor, so scaling the anchor
        # scales every boundary by the same factor — and the percentages
        # themselves do not move.
        assert bigger.lower == pytest.approx(original.lower * factor)
        assert bigger.lower_pct == original.lower_pct
        assert bigger.upper_pct == original.upper_pct


@given(anchored_models())
def test_percentages_and_absolute_bounds_agree(
    case: tuple[AnchorVersion, ZoneModel],
) -> None:
    version, model = case

    for zone in zones_for(version, model):
        assert zone.lower == pytest.approx(zone.lower_pct * version.value)
        assert (zone.upper is None) == (zone.upper_pct is None)
        if zone.upper is not None and zone.upper_pct is not None:
            assert zone.upper == pytest.approx(zone.upper_pct * version.value)
        assert zone.unit is version.unit


# --- the specific schemes the MVP ships --------------------------------------


def test_coggan_7_reproduces_the_published_table() -> None:
    zones = zones_for(anchor(AnchorType.FTP, 250), ZoneModel.COGGAN_7)

    assert [zone.lower_pct for zone in zones] == [
        0.0,
        0.55,
        0.75,
        0.90,
        1.05,
        1.20,
        1.50,
    ]
    assert [zone.lower for zone in zones] == [
        0.0,
        137.5,
        187.5,
        225.0,
        262.5,
        300.0,
        375.0,
    ]
    assert zones[3].name == "Threshold"
    assert len(zones) == 7


def test_lthr_5_reproduces_the_published_table() -> None:
    zones = zones_for(anchor(AnchorType.LTHR, 170), ZoneModel.LTHR_5)

    assert [zone.lower_pct for zone in zones] == [0.0, 0.81, 0.90, 0.94, 1.00]
    assert [round(zone.lower, 1) for zone in zones] == [0.0, 137.7, 153.0, 159.8, 170.0]
    assert zones[-1].name == "SuperThreshold"
    assert len(zones) == 5


# --- the pairing rule ---------------------------------------------------------


def test_a_power_model_refuses_a_heart_rate_anchor() -> None:
    with pytest.raises(ValueError, match="derives from ftp"):
        zones_for(anchor(AnchorType.LTHR, 170), ZoneModel.COGGAN_7)


def test_a_heart_rate_model_refuses_a_power_anchor() -> None:
    with pytest.raises(ValueError, match="derives from lthr"):
        zones_for(anchor(AnchorType.FTP, 250), ZoneModel.LTHR_5)


def test_the_default_model_follows_the_anchor_type() -> None:
    assert default_zone_model(AnchorType.FTP) is ZoneModel.COGGAN_7
    assert default_zone_model(AnchorType.LTHR) is ZoneModel.LTHR_5


@pytest.mark.parametrize(
    "anchor_type", [AnchorType.MAX_HR, AnchorType.CP, AnchorType.W_PRIME]
)
def test_anchor_types_without_a_model_say_so(anchor_type: AnchorType) -> None:
    # MAX_HR is a real MVP anchor with no zone scheme of its own; CP and W'
    # are reserved and unused. All three must fail loudly rather than fall
    # back to somebody else's percentages.
    with pytest.raises(ValueError, match="no zone model derives from"):
        default_zone_model(anchor_type)
