"""The Coggan chain: NP, IF, TSS.

These are the numbers everything downstream is denominated in — the planned
load on a calendar card (WP-3), the actual load of a ride (WP-5), every
adherence score (WP-7) — so the fixtures here are the anchor for all of them.
The real-world case at the bottom is the one that pins the arithmetic to
something outside this repository.
"""

import datetime as dt
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.activity import SessionDiscipline
from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
)
from app.domain.metrics import (
    NP_WINDOW_S,
    LoadBasis,
    Measured,
    MetricExplanation,
    NotAssessed,
    PerformedSet,
    SelectedLoad,
    StrengthVolume,
    average_power,
    channel_average,
    channel_maximum,
    coasting_time_s,
    efficiency_factor,
    elevation_gain_m,
    intensity_factor,
    normalized_power,
    select_training_load,
    strength_volume,
    training_load,
    variability_index,
    work_above_ftp_kj,
    work_kj,
)


def ftp_anchor(watts: float) -> AnchorVersion:
    """An FTP version, for the metrics that name one as an input."""
    return AnchorVersion(
        anchor_type=AnchorType.FTP,
        value=watts,
        unit=AnchorUnit.WATT,
        provenance=Provenance.ESTIMATED,
        effective_date=dt.date(2026, 6, 1),
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        source=AnchorSource.ATHLETE,
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


# --- WP-5: the rest of the power chain (Appendix A.1) -------------------------


def test_average_power_is_work_over_recording_time_not_the_device_average() -> None:
    # 100 rows of 200 W over 200 s of recording time: half the ride was
    # recorded without power, so the average is 100 W, not 200. A head unit
    # would say 200 — which is exactly why the caveat is on the explanation.
    assessed = average_power([200.0] * 100 + [None] * 100, 200.0)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0)
    assert any("head unit" in note for note in assessed.explanation.assumptions)


def test_average_power_without_power_names_the_missing_channel() -> None:
    assert average_power([None] * 10, 10.0) == NotAssessed("no power was recorded")


def test_work_counts_only_the_rows_that_recorded_power() -> None:
    assessed = work_kj([1_000.0] * 60 + [None] * 60)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(60.0)


def test_work_above_ftp_is_the_excess_only() -> None:
    assessed = work_above_ftp_kj([300.0] * 100 + [100.0] * 100, ftp_anchor(200.0))

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(10.0)  # 100 W over, 100 s, no credit below


def test_work_above_ftp_without_an_anchor_says_so() -> None:
    assert isinstance(work_above_ftp_kj([300.0] * 10, None), NotAssessed)


def test_variability_index_is_one_for_a_perfectly_steady_ride() -> None:
    assessed = variability_index(200.0, 200.0)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(1.0)


def test_efficiency_factor_is_watts_per_beat() -> None:
    assessed = efficiency_factor(200.0, 160.0)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(1.25)


def test_coasting_needs_both_movement_and_no_pedalling() -> None:
    # 10 rows moving without power (coasting), 10 rows moving with power,
    # 10 rows stopped at a light with no power (not coasting).
    watts = [0.0] * 10 + [180.0] * 10 + [0.0] * 10
    speed = [10.0] * 20 + [0.0] * 10

    assessed = coasting_time_s(watts, speed)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(10.0)


def test_coasting_without_a_speed_channel_names_speed() -> None:
    assert coasting_time_s([0.0] * 10, [None] * 10) == NotAssessed(
        "no speed was recorded"
    )


def test_the_maximum_is_taken_over_the_repaired_column() -> None:
    # The caller passes `power_fixed`, so the 1 900 W spike is already gone;
    # this pins that the function does not go looking for a raw column.
    assessed = channel_maximum("power", [100.0, 250.0, None, 200.0])

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(250.0)


def test_elevation_gain_ignores_wander_inside_the_hysteresis_band() -> None:
    # A metre of barometric wander, once a second, for ten minutes: a raw
    # positive-delta sum would report ~300 m of climbing on a flat road.
    wander = [100.0, 101.0] * 300

    assessed = elevation_gain_m(wander)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(0.0)


def test_elevation_gain_counts_a_real_climb() -> None:
    climb = [float(metre) for metre in range(100, 200)] + [
        float(metre) for metre in range(200, 150, -1)
    ]

    assessed = elevation_gain_m(climb)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0)  # 100 m up, then back down


def test_no_metric_function_raises_on_an_empty_series() -> None:
    # The whole point of `NotAssessed`: a session with nothing in it produces
    # an artefact full of reasons, never an exception and never a NaN.
    for assessed in (
        average_power([], 100.0),
        work_kj([]),
        work_above_ftp_kj([], ftp_anchor(200.0)),
        coasting_time_s([], []),
        channel_average("power", []),
        channel_maximum("power", []),
        elevation_gain_m([]),
    ):
        assert isinstance(assessed, NotAssessed)
        assert assessed.reason


# --- WP-5: dual load and its selection (A5.2) --------------------------------


def measured(value: float) -> Measured:
    """A metric result with a throwaway explanation."""
    return Measured(value=value, explanation=MetricExplanation(formula="x", inputs={}))


def test_cycling_with_power_selects_power_and_keeps_the_hr_number() -> None:
    selected = select_training_load(
        measured(79.0), measured(75.0), SessionDiscipline.CYCLING
    )

    assert isinstance(selected, SelectedLoad)
    assert selected.training_load == pytest.approx(79.0)
    assert selected.basis is LoadBasis.POWER
    assert selected.rule == "power available and preferred for cycling"
    # A5.2: the counterfactual is only renderable because the loser is stored.
    assert selected.hr_load == pytest.approx(75.0)


def test_cycling_without_power_falls_back_to_hr_and_says_why() -> None:
    selected = select_training_load(
        NotAssessed("no power was recorded"),
        measured(75.0),
        SessionDiscipline.CYCLING,
    )

    assert isinstance(selected, SelectedLoad)
    assert selected.basis is LoadBasis.HR
    assert selected.power_load is None
    assert "no power was recorded" in selected.rule


def test_strength_prefers_the_hr_model_even_when_power_exists() -> None:
    selected = select_training_load(
        measured(30.0), measured(45.0), SessionDiscipline.STRENGTH
    )

    assert isinstance(selected, SelectedLoad)
    assert selected.basis is LoadBasis.HR
    assert selected.training_load == pytest.approx(45.0)


def test_neither_model_computable_carries_both_reasons() -> None:
    selected = select_training_load(
        NotAssessed("no power was recorded"),
        NotAssessed("no heart rate was recorded"),
        SessionDiscipline.CYCLING,
    )

    assert isinstance(selected, NotAssessed)
    assert "no power was recorded" in selected.reason
    assert "no heart rate was recorded" in selected.reason


# --- WP-5: strength volume (A-6) ---------------------------------------------


def test_volume_load_counts_only_the_sets_logged_in_kilograms() -> None:
    volume = strength_volume(
        [
            PerformedSet(reps=5, load_kg=100.0),
            PerformedSet(reps=5, load_kg=100.0),
            PerformedSet(reps=10, load_kg=None),
        ]
    )

    assert isinstance(volume, StrengthVolume)
    assert volume.volume_load_kg == pytest.approx(1_000.0)
    assert volume.sets_completed == 3
    assert volume.coverage == pytest.approx(2 / 3)


def test_a_bodyweight_session_has_sets_but_no_volume_load() -> None:
    volume = strength_volume([PerformedSet(reps=20, load_kg=None)] * 3)

    assert isinstance(volume, StrengthVolume)
    assert volume.volume_load_kg is None
    assert volume.sets_completed == 3
    assert volume.coverage == 0.0


def test_no_sets_logged_is_not_a_zero_volume() -> None:
    assert strength_volume([]) == NotAssessed("no sets were logged")
