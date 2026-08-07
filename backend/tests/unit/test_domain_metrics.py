"""The Coggan chain: NP, IF, TSS.

These are the numbers everything downstream is denominated in — the planned
load on a calendar card (WP-3), the actual load of a ride (WP-5), every
adherence score (WP-7) — so the fixtures here are the anchor for all of them.
The real-world case at the bottom is the one that pins the arithmetic to
something outside this repository.
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.metrics import (
    NP_WINDOW_S,
    MetricExplanation,
    intensity_factor,
    normalized_power,
    training_load,
)


def test_a_constant_series_has_np_equal_to_its_mean() -> None:
    # Every rolling window of a flat series is the same number, so the 4th
    # power and the 4th root cancel exactly.
    assert normalized_power([200.0] * 600) == pytest.approx(200.0, abs=1e-9)


def test_a_series_shorter_than_the_window_uses_what_it_has() -> None:
    # 10 samples, a 30 s window: the leading samples are averaged over a
    # shorter window rather than dropped, so a short series still has an NP.
    assert normalized_power([250.0] * 10) == pytest.approx(250.0, abs=1e-9)
    assert normalized_power([100.0]) == pytest.approx(100.0, abs=1e-9)


def test_an_empty_series_is_zero_watts() -> None:
    # Pinned behaviour: 0.0, not an exception. A series with no samples
    # carries no work, and every caller would otherwise write the same
    # length check to say so.
    assert normalized_power([]) == 0.0


def test_a_square_wave_reads_higher_than_a_steady_series_of_the_same_mean() -> None:
    # The whole reason NP exists: 4th-power weighting makes variable riding
    # cost more than steady riding at the same average power.
    square_wave = ([300.0] * 60 + [100.0] * 60) * 10
    steady = [200.0] * len(square_wave)

    assert sum(square_wave) / len(square_wave) == pytest.approx(
        sum(steady) / len(steady)
    )
    assert normalized_power(square_wave) > normalized_power(steady)


def test_the_window_scales_with_the_sample_rate() -> None:
    # 2 Hz means 60 samples per 30 s window, so the same physical series
    # sampled twice as fast must produce (very nearly) the same NP.
    at_1hz = ([300.0] * 60 + [100.0] * 60) * 10
    at_2hz = [value for value in at_1hz for _ in range(2)]

    assert normalized_power(at_2hz, sample_hz=2) == pytest.approx(
        normalized_power(at_1hz), rel=0.01
    )


def test_a_sample_rate_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="sample_hz must be at least 1"):
        normalized_power([200.0], sample_hz=0)


def test_the_window_is_thirty_seconds() -> None:
    assert NP_WINDOW_S == 30


def test_intensity_factor_is_np_over_ftp() -> None:
    assert intensity_factor(225.0, 250.0) == pytest.approx(0.9)


def test_intensity_factor_refuses_a_zero_ftp() -> None:
    with pytest.raises(ValueError, match="ftp_watts must be above 0"):
        intensity_factor(225.0, 0.0)


def test_an_hour_at_threshold_is_one_hundred_points() -> None:
    # The definition of the scale.
    assert training_load(3_600, 1.0) == pytest.approx(100.0)


def test_training_load_refuses_a_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_s must not be negative"):
        training_load(-1, 1.0)


def test_the_real_world_fixture() -> None:
    """A verified real ride: 5 737 s, NP 141 W, FTP 200 W.

    IF = 141 / 200 = 0.705, load = 5737 × 0.705² / 36 = 79.2 TSS.

    This case is the anchor for every load number the application will ever
    show: it came from a real recording scored outside this repository, so if
    the arithmetic here drifts, this is the test that says so.
    """
    factor = intensity_factor(141.0, 200.0)
    load = training_load(5_737, factor)

    assert factor == pytest.approx(0.705, abs=0.001)
    assert load == pytest.approx(79.2, abs=0.5)


@given(
    st.lists(
        st.floats(min_value=0, max_value=1_500, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=400,
    )
)
def test_np_is_finite_and_within_the_extremes_of_the_series(
    watts: list[float],
) -> None:
    # NP is a mean of means: it cannot exceed the biggest sample, and a
    # non-negative series cannot produce a negative NP.
    result = normalized_power(watts)

    assert math.isfinite(result)
    assert 0.0 <= result <= max(watts) + 1e-9


def test_an_explanation_holds_what_the_ui_and_the_agent_both_read() -> None:
    explanation = MetricExplanation(
        formula="TSS = duration_s × IF² / 36",
        inputs={"FTP": "250 W (estimated, effective 2026-06-01)"},
        assumptions=("target ranges reduced to their midpoint",),
        citation="Allen & Coggan, Training and Racing with a Power Meter",
    )

    assert explanation.inputs["FTP"].startswith("250 W")
    assert explanation.assumptions == ("target ranges reduced to their midpoint",)
    assert MetricExplanation(formula="x", inputs={}).assumptions == ()
    assert MetricExplanation(formula="x", inputs={}).citation is None
