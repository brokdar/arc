"""HRSS — the heart-rate load model, and the variant it is deliberately not.

Two HRSS forms are in circulation and they disagree by a lot on exactly the
sessions HR load exists for. The Jensen test below is what stops the cheap one
from being reintroduced by a well-meaning refactor: it is the only test here
that would still pass if the arithmetic were merely *wrong*, and fail if it
were wrong in that particular way.

The fixture at the top comes from Appendix A.2 and is verified outside this
repository.
"""

import datetime as dt
from collections.abc import Sequence

import pytest

from app.domain.anchors import (
    ANCHOR_UNITS,
    AnchorSource,
    AnchorType,
    AnchorVersion,
    Provenance,
)
from app.domain.athlete import Sex
from app.domain.metrics import HRSS_C, HRSS_K, Measured, NotAssessed, hrss


def anchor(anchor_type: AnchorType, value: float) -> AnchorVersion:
    """One anchor version, everything but the value held constant."""
    return AnchorVersion(
        anchor_type=anchor_type,
        value=value,
        unit=ANCHOR_UNITS[anchor_type],
        provenance=Provenance.TESTED,
        protocol="ramp test",
        effective_date=dt.date(2026, 5, 1),
        created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        source=AnchorSource.ATHLETE,
    )


RESTING = anchor(AnchorType.RESTING_HR, 65.0)
MAXIMUM = anchor(AnchorType.MAX_HR, 190.0)
THRESHOLD = anchor(AnchorType.LTHR, 171.25)


def assess(
    hr: Sequence[float | None],
    *,
    resting: AnchorVersion | None = RESTING,
    maximum: AnchorVersion | None = MAXIMUM,
    threshold: AnchorVersion | None = THRESHOLD,
    sex: Sex = Sex.MALE,
) -> Measured | NotAssessed:
    """Run HRSS over one series with the fixture's anchors."""
    return hrss(hr, resting_hr=resting, max_hr=maximum, lthr=threshold, sex=sex)


def test_the_appendix_fixture() -> None:
    """Male, HR_max 190, HR_rest 65, LTHR 171.25, one hour at HRr 0.70.

    ``HRr_LT = 0.85`` → ``TRIMP_LT_1h = 166.924``; the hour's TRIMP is
    103.067, so HRSS is **61.7**. Verified outside this repository
    (Appendix A.2); if the arithmetic drifts, this is the test that says so.
    """
    # HRr 0.70 of a 125 bpm reserve above 65 bpm is 152.5 bpm.
    assessed = assess([152.5] * 3_600)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(61.7, abs=0.1)


def test_the_threshold_normalisation_is_one_hundred_for_an_hour_at_lthr() -> None:
    # The definition of the scale, and the reason HRSS is comparable to TSS.
    assessed = assess([THRESHOLD.value] * 3_600)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0, abs=0.1)


def test_a_square_wave_costs_more_than_a_steady_series_of_the_same_mean() -> None:
    """Jensen's inequality: the per-sample form is not the average-HR form.

    ``e^(k·x̄) ≤ mean(e^(k·xᵢ))``, so computing one TRIMP from the session's
    average heart rate systematically under-reports variable sessions —
    intervals, and every strength session. A single-average implementation
    would make these two series identical, which is what this pins.
    """
    square_wave = ([170.0] * 120 + [110.0] * 120) * 15
    steady = [140.0] * len(square_wave)
    assert sum(square_wave) / len(square_wave) == pytest.approx(
        sum(steady) / len(steady)
    )

    varied, flat = assess(square_wave), assess(steady)

    assert isinstance(varied, Measured)
    assert isinstance(flat, Measured)
    assert varied.value > flat.value


def test_heart_rates_below_resting_count_as_zero_rather_than_negative() -> None:
    # The per-sample reserve is clamped (Appendix A.2); an unclamped form
    # would make a warm-up row *subtract* load.
    below = assess([50.0] * 600)
    at_rest = assess([RESTING.value] * 600)

    assert isinstance(below, Measured)
    assert isinstance(at_rest, Measured)
    assert below.value == pytest.approx(0.0)
    assert at_rest.value == pytest.approx(0.0)


def test_hrss_is_sex_dependent_because_the_exponential_does_not_cancel() -> None:
    male = assess([152.5] * 3_600, sex=Sex.MALE)
    female = assess([152.5] * 3_600, sex=Sex.FEMALE)

    assert isinstance(male, Measured)
    assert isinstance(female, Measured)
    assert male.value != pytest.approx(female.value)
    assert HRSS_K[Sex.MALE] == 1.92
    assert HRSS_K[Sex.FEMALE] == 1.67
    assert HRSS_C == 0.64


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"resting": None}, "resting HR"),
        ({"maximum": None}, "max HR"),
        ({"threshold": None}, "threshold HR (LTHR)"),
    ],
)
def test_a_missing_anchor_names_itself(kwargs: dict[str, None], expected: str) -> None:
    assessed = assess([150.0] * 600, **kwargs)  # type: ignore[arg-type]

    assert isinstance(assessed, NotAssessed)
    assert expected in assessed.reason


def test_unspecified_sex_cannot_be_assessed() -> None:
    assessed = assess([150.0] * 600, sex=Sex.UNSPECIFIED)

    assert isinstance(assessed, NotAssessed)
    assert "sex" in assessed.reason


def test_a_threshold_at_or_below_resting_is_guarded_not_computed() -> None:
    # Appendix A.2: the threshold reserve is conventionally unclamped, so this
    # would flip the sign of every HRSS or divide by zero.
    assessed = assess([150.0] * 600, threshold=anchor(AnchorType.LTHR, 65.0))

    assert assessed == NotAssessed("threshold HR is not above resting HR")


def test_a_max_at_or_below_resting_is_guarded() -> None:
    # A transposed pair of readings: 80 bpm entered as the maximum and 100 as
    # the resting rate. The reserve is then zero or negative and every term
    # divides by it.
    assessed = assess(
        [150.0] * 600,
        resting=anchor(AnchorType.RESTING_HR, 100.0),
        maximum=anchor(AnchorType.MAX_HR, 80.0),
    )

    assert assessed == NotAssessed("max HR is not above resting HR")


def test_no_heart_rate_recorded_names_the_channel() -> None:
    assert assess([None] * 600) == NotAssessed("no heart rate was recorded")


def test_recording_stops_are_excluded_rather_than_read_as_resting() -> None:
    # Nulls are holes, not zero-effort seconds: an hour at threshold with a
    # ten-minute pause in the middle must cost exactly the hour it recorded.
    with_stop = assess([THRESHOLD.value] * 3_600 + [None] * 600)
    without = assess([THRESHOLD.value] * 3_600)

    assert isinstance(with_stop, Measured)
    assert isinstance(without, Measured)
    assert with_stop.value == pytest.approx(without.value)
