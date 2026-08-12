"""Time in zone, the three-zone collapse and the polarization index (A5.4).

The PI fixtures are Appendix A.3's, verified against the reference platform.
They are the first numbers in the product that describe training *quality*
rather than quantity, and the formula is easy to reproduce wrongly (fractions
of the banded total, not of elapsed time; a log of a ratio of a ratio), so
each one is pinned rather than derived here.
"""

import datetime as dt
import math
from collections.abc import Sequence

import pytest

from app.domain.anchors import (
    ANCHOR_UNITS,
    AnchorSource,
    AnchorType,
    AnchorVersion,
    Provenance,
)
from app.domain.metrics import (
    THREE_ZONE_BANDS,
    Measured,
    NotAssessed,
    TimeInZone,
    polarization_index,
    time_in_zone,
)
from app.domain.zones import ZONE_MODEL_ANCHOR, ZoneModel, zones_for


def anchor(anchor_type: AnchorType, value: float) -> AnchorVersion:
    """One anchor version, everything but the value held constant."""
    return AnchorVersion(
        anchor_type=anchor_type,
        value=value,
        unit=ANCHOR_UNITS[anchor_type],
        provenance=Provenance.ESTIMATED,
        effective_date=dt.date(2026, 5, 1),
        created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        source=AnchorSource.ATHLETE,
    )


FTP = anchor(AnchorType.FTP, 250.0)
LTHR = anchor(AnchorType.LTHR, 165.0)


def power_zones(watts: Sequence[float | None]) -> TimeInZone | NotAssessed:
    """Band a power series by the Coggan model off the fixture FTP."""
    return time_in_zone(
        watts, zones_for(FTP, ZoneModel.COGGAN_7), ZoneModel.COGGAN_7, anchor=FTP
    )


def test_the_appendix_polarization_fixture() -> None:
    """72.6 % / 19.4 % / 8.0 % → 1.4762, displayed as **1.48**."""
    assessed = polarization_index(72.6, 19.4, 8.0)

    assert isinstance(assessed, Measured)
    assert round(assessed.value, 2) == 1.48


@pytest.mark.parametrize(
    ("easy", "moderate", "hard", "expected"),
    [
        (80.0, 5.0, 15.0, 2.38),  # polarized
        (80.0, 15.0, 5.0, 1.43),  # pyramidal
    ],
)
def test_the_reference_distributions(
    easy: float, moderate: float, hard: float, expected: float
) -> None:
    assessed = polarization_index(easy, moderate, hard)

    assert isinstance(assessed, Measured)
    assert round(assessed.value, 2) == expected


def test_the_index_is_scale_free() -> None:
    # The formula takes fractions of the banded total, so the same
    # distribution measured in seconds and in percent must agree.
    in_percent = polarization_index(72.6, 19.4, 8.0)
    in_seconds = polarization_index(7_260.0, 1_940.0, 800.0)

    assert isinstance(in_percent, Measured)
    assert isinstance(in_seconds, Measured)
    assert in_percent.value == pytest.approx(in_seconds.value)


@pytest.mark.parametrize(
    ("easy", "moderate", "hard", "band"),
    [
        (80.0, 0.0, 20.0, "moderate"),
        (80.0, 20.0, 0.0, "hard"),
        (0.0, 50.0, 50.0, "easy"),
    ],
)
def test_a_degenerate_split_is_not_assessed_rather_than_infinite(
    easy: float, moderate: float, hard: float, band: str
) -> None:
    # Without the guard these are ±inf or a division by zero, and an infinite
    # PI would sort as the most polarized session ever ridden.
    assessed = polarization_index(easy, moderate, hard)

    assert isinstance(assessed, NotAssessed)
    assert band in assessed.reason


def test_an_empty_distribution_is_not_assessed() -> None:
    assert polarization_index(0.0, 0.0, 0.0) == NotAssessed("no time fell in any zone")


def test_time_in_zone_sums_to_the_readings_it_banded() -> None:
    # One second per row carrying a reading — not elapsed time, because a stop
    # is not time in Z1.
    watts: list[float | None] = [*[100.0] * 600, *[None] * 300, *[230.0] * 600]
    banded = power_zones(watts)

    assert isinstance(banded, TimeInZone)
    assert banded.total_s == pytest.approx(1_200.0)
    assert sum(zone.seconds for zone in banded.zones) == pytest.approx(1_200.0)


def test_every_band_of_the_model_is_present_even_when_empty() -> None:
    # A zone with no time in it is a fact about the ride; dropping it would
    # make the bar chart's shape depend on the data.
    banded = power_zones([100.0] * 60)

    assert isinstance(banded, TimeInZone)
    assert [zone.index for zone in banded.zones] == [1, 2, 3, 4, 5, 6, 7]


def test_the_three_zone_collapse_follows_the_model() -> None:
    # 100 W is Z1 (<55 % of 250), 200 W is Z3 (80 %), 300 W is Z5 (120 %).
    banded = power_zones([100.0] * 100 + [200.0] * 50 + [300.0] * 25)

    assert isinstance(banded, TimeInZone)
    assert banded.easy_s == pytest.approx(100.0)
    assert banded.moderate_s == pytest.approx(50.0)
    assert banded.hard_s == pytest.approx(25.0)


def test_the_hr_model_bands_are_the_five_zone_mapping() -> None:
    # The addenda's 1-2 / 3-4 / 5-7 numbers are the *power* model's. The rule
    # behind them is where **threshold** sits, and applying that rule to
    # `lthr_5` puts its Z4 (`SubThreshold`, 94-100 %LTHR) in moderate and
    # leaves Z5 (`SuperThreshold`, from 100 %LTHR) alone in hard.
    easy, moderate, hard = THREE_ZONE_BANDS[ZoneModel.LTHR_5]

    assert easy == frozenset({1, 2})
    assert moderate == frozenset({3, 4})
    assert hard == frozenset({5})


def test_the_two_models_draw_the_hard_boundary_at_the_same_place() -> None:
    # The property the mapping exists to hold: in both models, "hard" is the
    # bands strictly **above** threshold. `coggan_7`'s Z4 is Threshold and
    # `lthr_5`'s Z4 is SubThreshold, so neither belongs in hard.
    for model in (ZoneModel.COGGAN_7, ZoneModel.LTHR_5):
        _easy, moderate, hard = THREE_ZONE_BANDS[model]
        anchor_type = ZONE_MODEL_ANCHOR[model]
        zones = zones_for(anchor(anchor_type, 200.0), model)
        by_index = {zone.index: zone for zone in zones}
        # Every hard band starts at or above the anchor; no moderate one does.
        assert all(by_index[index].lower_pct >= 1.0 for index in hard)
        assert all(by_index[index].lower_pct < 1.0 for index in moderate)


def test_sub_threshold_heart_rate_is_moderate_not_hard() -> None:
    """The regression: 94-100 %LTHR is tempo-to-threshold, not hard riding.

    An earlier mapping put `lthr_5`'s Z4 in the hard band, which counted a
    steady sub-threshold effort as hard and inflated the polarization index of
    exactly the sessions the index exists to describe.
    """
    # 120 bpm is 73 %LTHR (Z1), 160 bpm is 97 % (Z4 — SubThreshold), 170 bpm
    # is 103 % (Z5 — SuperThreshold).
    zones = zones_for(LTHR, ZoneModel.LTHR_5)
    banded = time_in_zone(
        [120.0] * 600 + [160.0] * 300 + [170.0] * 100,
        zones,
        ZoneModel.LTHR_5,
        anchor=LTHR,
    )

    assert isinstance(banded, TimeInZone)
    assert banded.easy_s == pytest.approx(600.0)
    assert banded.moderate_s == pytest.approx(300.0)
    assert banded.hard_s == pytest.approx(100.0)
    # And the index follows: counting the 300 sub-threshold seconds as hard
    # would move it by more than a rounding step.
    assert isinstance(banded.polarization_index, Measured)
    assert round(banded.polarization_index.value, 2) == round(
        math.log10((600 / 300) * (100 / 1000) * 100), 2
    )


def test_the_hr_channel_bands_against_lthr() -> None:
    zones = zones_for(LTHR, ZoneModel.LTHR_5)
    # 120 bpm is 73 % of LTHR (Z1), 155 bpm is 94 % (Z4 starts at 94 %).
    banded = time_in_zone(
        [120.0] * 300 + [166.0] * 100, zones, ZoneModel.LTHR_5, anchor=LTHR
    )

    assert isinstance(banded, TimeInZone)
    assert banded.easy_s == pytest.approx(300.0)
    assert banded.hard_s == pytest.approx(100.0)


def test_the_distribution_records_the_model_it_used() -> None:
    # A5.5: `(anchor version, zone model) -> zones` is only deterministic
    # while the model is recorded beside the anchor.
    banded = power_zones([100.0] * 60)

    assert isinstance(banded, TimeInZone)
    assert banded.model is ZoneModel.COGGAN_7
    assert "250 W" in banded.explanation.inputs["anchor"]


def test_a_channel_that_was_not_recorded_is_not_assessed() -> None:
    assert isinstance(power_zones([None] * 600), NotAssessed)
