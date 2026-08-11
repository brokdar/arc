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
    ODOMETER_COVERAGE_FLOOR,
    ODOMETER_DIP_SAMPLES,
    Assessment,
    AveragingBasis,
    LoadBasis,
    Measured,
    MetricExplanation,
    NotAssessed,
    PerformedSet,
    SelectedLoad,
    StrengthVolume,
    average_cadence,
    average_power,
    average_speed_kmh,
    averaging_basis,
    channel_average,
    channel_maximum,
    channel_minimum,
    coasting_time_s,
    distance_km,
    efficiency_factor,
    elevation_gain_m,
    intensity_factor,
    max_speed_kmh,
    normalized_power,
    select_training_load,
    stopped_time_s,
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


def riding(seconds: int, *, recording_time_s: float | None = None) -> AveragingBasis:
    """The basis of a session whose speed column moved for ``seconds`` rows."""
    basis = averaging_basis(
        [9.0] * seconds,
        recording_time_s=float(seconds)
        if recording_time_s is None
        else recording_time_s,
    )
    assert isinstance(basis, AveragingBasis)
    return basis


def test_the_averaging_basis_is_moving_time_when_there_is_any() -> None:
    # D194: an average divides by the seconds the athlete was travelling,
    # which is what a head unit does and what every other platform reports.
    # D196: counted off the cleaned column, one second per row, so it is the
    # same series the numerators are integrated over.
    basis = averaging_basis([9.0] * 1_150 + [0.0] * 50, recording_time_s=1_200.0)

    assert isinstance(basis, AveragingBasis)
    assert basis.seconds == pytest.approx(1_150.0)
    assert basis.from_moving_time
    assert basis.label == "moving time"
    assert basis.rows == tuple(range(1_150))


def test_the_averaging_basis_falls_back_to_recording_time_without_speed() -> None:
    # An indoor session with no speed channel has no moving time at all.
    # Refusing to average it would be a worse answer than averaging it over
    # the duration that does exist — but the fallback has to be visible.
    basis = averaging_basis([], recording_time_s=1_200.0)

    assert isinstance(basis, AveragingBasis)
    assert basis.seconds == pytest.approx(1_200.0)
    assert not basis.from_moving_time
    assert basis.label == "recording time"
    assert basis.rows is None
    assert "no speed was recorded" in basis.assumption


def test_a_speed_channel_that_covers_half_the_ride_is_refused_as_a_divisor() -> None:
    """D196, the case that used to double an average power.

    The sensor dies halfway through: the column that remains is perfectly
    plausible — 1 800 real moving seconds — and dividing a whole hour's
    readings by it would report twice the power the athlete produced. Under
    `SPEED_COVERAGE_FLOOR` the basis refuses to be moving time at all, and
    says which fraction of the ride it saw.
    """
    basis = averaging_basis([9.0] * 1_800 + [None] * 1_801, recording_time_s=3_600.0)

    assert isinstance(basis, AveragingBasis)
    assert not basis.from_moving_time
    assert basis.seconds == pytest.approx(3_600.0)
    assert "50%" in basis.assumption
    # The observed moving seconds are still reported — they are a fact about
    # the column — and so are the seconds it said nothing about.
    assert basis.moving_s == pytest.approx(1_800.0)
    assert basis.uncovered_s == pytest.approx(1_800.0)


def test_the_coverage_floor_is_a_line_and_both_sides_of_it_are_pinned() -> None:
    # 90 % of the recorded seconds is enough to divide by; 89 % is not. The
    # boundary is asserted from both sides because a threshold nobody straddles
    # in a test is a threshold that can silently move.
    enough = averaging_basis([9.0] * 900 + [None] * 100, recording_time_s=1_000.0)
    too_little = averaging_basis([9.0] * 890 + [None] * 110, recording_time_s=1_000.0)

    assert isinstance(enough, AveragingBasis)
    assert isinstance(too_little, AveragingBasis)
    assert enough.from_moving_time
    assert not too_little.from_moving_time


def test_a_recording_that_never_moved_says_that_rather_than_no_speed() -> None:
    # A trainer session with a speed channel reading zero is not a session
    # with no speed channel, and telling the athlete it recorded no speed
    # would send them looking for a sensor that worked perfectly.
    basis = averaging_basis([0.0] * 600, recording_time_s=600.0)

    assert isinstance(basis, AveragingBasis)
    assert not basis.from_moving_time
    assert "never reached 1 km/h" in basis.assumption


def test_the_averaging_basis_of_a_session_with_no_duration_is_a_reason() -> None:
    assert isinstance(averaging_basis([], recording_time_s=0.0), NotAssessed)


def test_average_power_is_work_over_moving_time_not_the_device_average() -> None:
    # 100 rows of 200 W over 200 s of moving time: half the ride was moving
    # without a power reading, so the average is 100 W, not 200. Rows with no
    # reading contribute no joules while still costing their second.
    assessed = average_power([200.0] * 100 + [None] * 100, riding(200))

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0)
    assert any("moving time" in note for note in assessed.explanation.assumptions)
    # And it says, on the number itself, that the load did not move with it.
    assert any("recording time" in note for note in assessed.explanation.assumptions)


def test_average_power_sums_only_the_seconds_its_divisor_counted() -> None:
    """D196's invariant, stated as arithmetic.

    Half an hour at 200 W and half an hour at a red light, still pedalling at
    200 W into the turbo of a trainer that reports speed. The average is 200 W
    either way — but only because the 1 800 stopped seconds are absent from
    *both* the sum and the divisor. Divide by moving time while summing every
    row and it reads 400 W.
    """
    speed = [9.0] * 1_800 + [0.0] * 1_800
    basis = averaging_basis(speed, recording_time_s=3_600.0)
    assessed = average_power([200.0] * 3_600, basis)

    assert isinstance(basis, AveragingBasis)
    assert isinstance(assessed, Measured)
    assert basis.seconds == pytest.approx(1_800.0)
    assert assessed.value == pytest.approx(200.0)
    assert "while moving" in assessed.explanation.formula


def test_average_power_over_a_speedless_session_says_which_divisor_it_used() -> None:
    # 200 W for 100 rows, no speed channel: the divisor is the 400 s of
    # recording time, and the explanation says the number reads low because
    # of it rather than leaving the athlete to discover that.
    basis = averaging_basis([], recording_time_s=400.0)
    assessed = average_power([200.0] * 100, basis)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(50.0)
    assert any(
        "no speed was recorded" in note for note in assessed.explanation.assumptions
    )


def test_average_power_without_power_names_the_missing_channel() -> None:
    assert average_power([None] * 10, riding(10)) == NotAssessed(
        "no power was recorded"
    )


def test_average_power_without_a_basis_carries_the_basis_reason() -> None:
    # One reason, not two: "no duration to average over" is the whole story,
    # and inventing a second sentence for it here would put two different
    # explanations of one fact on one page.
    basis = averaging_basis([], recording_time_s=0.0)

    assert average_power([200.0] * 10, basis) == basis


def test_distance_integrates_the_speed_channel_when_there_is_no_odometer() -> None:
    # 600 s at 10 m/s is 6 km, and the 600 unrecorded rows add nothing.
    assessed = distance_km([10.0] * 600 + [None] * 600)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(6.0)
    # And it says which of the two distances this is, first (D197).
    assert (
        "integrated from the 1 Hz speed channel"
        in (assessed.explanation.assumptions[0])
    )
    assert "no odometer channel" in assessed.explanation.assumptions[0]


def test_distance_prefers_the_odometer_over_integrating_speed() -> None:
    # The reference ride's shape, shrunk: a head unit integrates internally at
    # a far higher rate than the 1 Hz speed it writes, so its own cumulative
    # distance runs ~1.5 % ahead of anything reconstructed from that column
    # (40.95 km against 40.32 over 41 km). The metric reports the device's
    # number and names it.
    speed = [10.0] * 600  # Σ v × Δt = 6 000 m
    odometer = [metre * 10.15 for metre in range(600)]  # 0 m to 6 079.85 m

    assessed = distance_km(speed, odometer)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(6.07985)
    assert assessed.value == pytest.approx(0.985**-1 * 6.0, rel=2e-3)
    assert "odometer" in assessed.explanation.assumptions[0]
    assert "odometer" in assessed.explanation.formula


def test_distance_ignores_an_odometer_that_goes_backwards() -> None:
    # A reset odometer holds perfectly plausible metres in a nonsensical
    # order, so nothing upstream can have caught it — and the metres it lost
    # are not recoverable. Integrating speed is the honest answer, said out
    # loud rather than reported as if the device had agreed.
    speed = [10.0] * 600
    reset = [float(metre) for metre in range(300)] + [
        float(metre) for metre in range(300)
    ]

    assessed = distance_km(speed, reset)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(6.0)
    assert "goes backwards" in assessed.explanation.assumptions[0]
    # A reset is told from a glitch by how long it stays down, and the reason
    # says which test it failed.
    assert (
        f"more than {ODOMETER_DIP_SAMPLES} readings"
        in (assessed.explanation.assumptions[0])
    )


def test_distance_rides_over_a_self_correcting_odometer_glitch() -> None:
    # A garbled packet puts one or two readings below where the odometer had
    # already been and the device then carries on from where it was. Discarding
    # a thousand good readings over that is the cure being worse than the
    # disease, so a dip that recovers within ODOMETER_DIP_SAMPLES is ridden
    # over — and the span is taken from the running maximum, so a dip at the
    # very end cannot shorten the ride either.
    speed = [10.0] * 1_000
    glitchy: list[float | None] = [float(metre) for metre in range(1_000)]
    glitchy[500] = 400.0  # a 100 m dip …
    glitchy[501] = 401.0
    glitchy[999] = 900.0  # … and one on the last reading

    assessed = distance_km(speed, glitchy)

    assert isinstance(assessed, Measured)
    # 998 m, the highest reading, not 900 and not the speed column's 10 km.
    assert assessed.value == pytest.approx(0.998)
    assert "odometer" in assessed.explanation.assumptions[0]
    assert not any(
        "goes backwards" in note for note in assessed.explanation.assumptions
    )


def test_distance_refuses_an_odometer_that_covers_too_little_of_the_ride() -> None:
    # The failure a monotonicity check cannot see (D202). A device that stops
    # writing its distance field 60 % of the way through a 10 km ride leaves a
    # perfectly ordered column whose span is 6.08 km — a real number of metres,
    # just not this ride's, and reported with an assumption that used to claim
    # it covered the whole recording.
    speed = [10.0] * 1_000  # 10.0 km integrated
    stops_early: list[float | None] = [metre * 10.15 for metre in range(600)] + [
        None
    ] * 400

    assessed = distance_km(speed, stops_early)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(10.0)
    assert "covers only 60%" in assessed.explanation.assumptions[0]
    assert (
        "integrated from the 1 Hz speed channel"
        in (assessed.explanation.assumptions[0])
    )


def test_the_odometer_coverage_floor_is_where_it_says_it_is() -> None:
    # Straddled rather than sampled: a floor nobody tests either side of is a
    # constant, not a rule.
    speed = [10.0] * 1_000

    def covering(rows: int) -> Assessment:
        return distance_km(
            speed, [metre * 10.15 for metre in range(rows)] + [None] * (1_000 - rows)
        )

    at_the_floor = covering(round(ODOMETER_COVERAGE_FLOOR * 1_000))
    below_it = covering(round(ODOMETER_COVERAGE_FLOOR * 1_000) - 1)

    assert isinstance(at_the_floor, Measured)
    assert isinstance(below_it, Measured)
    assert "odometer" in at_the_floor.explanation.assumptions[0]
    assert below_it.value == pytest.approx(10.0)
    assert "covers only" in below_it.explanation.assumptions[0]


def test_a_partly_covered_odometer_says_what_it_actually_covered() -> None:
    # The assumption this replaced promised the odometer "covers the whole
    # recording" whatever the column did — false for the very first real file
    # this ran against, whose odometer starts 29 rows in. It now states what
    # was verified, both ways.
    speed = [10.0] * 1_000
    whole = distance_km(speed, [metre * 10.15 for metre in range(1_000)])
    partial = distance_km(speed, [None] * 50 + [metre * 10.15 for metre in range(950)])

    assert isinstance(whole, Measured)
    assert isinstance(partial, Measured)
    assert "covers every second" in whole.explanation.assumptions[1]
    assert "covers 95%" in partial.explanation.assumptions[1]


def test_a_merged_sessions_distance_sums_each_recordings_own_odometer() -> None:
    # D202. Two recordings on one grid, each odometer counting from its own
    # zero, with a 100-row gap between them: the joined column runs
    # 0 -> 6 080, gap, 0 -> 4 000. Read end to end that is not a smaller
    # number, it is a different ride — and it is arc's own join that made it,
    # not the athlete's hardware.
    speed = [10.0] * 600 + [None] * 100 + [8.0] * 600
    odometer: list[float | None] = (
        [metre * 10.15 for metre in range(600)]
        + [None] * 100
        + [metre * 8.12 for metre in range(600)]
    )
    first = 599 * 10.15
    second = 599 * 8.12

    assessed = distance_km(speed, odometer, segments=((0, 600), (700, 1_300)))

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx((first + second) / 1000)
    # Emphatically not the span of the joined column, which is what reading it
    # end to end would give.
    assert assessed.value > second / 1000 * 1.5
    assert "each recording's own odometer span" in assessed.explanation.assumptions[0]
    # And nothing anywhere accuses the device of resetting a column arc joined.
    assert not any(
        "reset" in note or "corrupted" in note
        for note in assessed.explanation.assumptions
    )


def test_a_merge_of_one_odometer_and_one_without_reports_both_sources() -> None:
    # The worst of the three join failures: read end to end, the first file's
    # odometer is monotone all by itself, so the whole session used to report
    # the first recording's distance and drop the second entirely.
    speed = [10.0] * 600 + [None] * 100 + [8.0] * 600
    odometer: list[float | None] = [metre * 10.15 for metre in range(600)] + [
        None
    ] * 700

    assessed = distance_km(speed, odometer, segments=((0, 600), (700, 1_300)))

    assert isinstance(assessed, Measured)
    # The odometer's 6.08 km plus the second recording's integrated 4.8 km.
    assert assessed.value == pytest.approx(6.07985 + 4.8)
    assert (
        "1 of them from the device's own odometer"
        in (assessed.explanation.assumptions[0])
    )
    assert (
        "1 integrated from the 1 Hz speed channel"
        in (assessed.explanation.assumptions[0])
    )
    # Both inputs are named, because two kinds of kilometre were added.
    assert "odometer" in assessed.explanation.inputs
    assert "speed" in assessed.explanation.inputs


def test_distance_ignores_an_odometer_that_never_advanced() -> None:
    assessed = distance_km([10.0] * 600, [0.0] * 600)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(6.0)
    assert "never advanced" in assessed.explanation.assumptions[0]


def test_distance_reads_an_odometer_even_with_no_speed_channel() -> None:
    # The odometer is a whole account of the ride's distance on its own; a
    # trainer that reported distance and no speed is still a ride that went
    # somewhere.
    assessed = distance_km([None] * 600, [0.0] + [4_000.0] * 599)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(4.0)


def test_distance_without_speed_or_odometer_names_the_missing_channel() -> None:
    assert distance_km([None] * 10) == NotAssessed("no speed was recorded")
    assert distance_km([None] * 10, [None] * 10) == NotAssessed("no speed was recorded")


def test_average_speed_is_distance_over_the_same_basis_as_average_power() -> None:
    # 6 km in 600 s of moving time is 36 km/h — and the 600 stopped seconds
    # the session also lasted do not drag it down, which is the whole point
    # of averaging over moving time.
    ride = [10.0] * 600 + [0.0] * 600
    basis = averaging_basis(ride, recording_time_s=1_200.0)
    assessed = average_speed_kmh(distance_km(ride), basis)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(36.0)


def test_average_speed_divides_the_whole_rides_distance_by_moving_time() -> None:
    # D198: the numerator is the ride's total — the odometer's span, which has
    # no per-row decomposition to restrict — and the divisor is moving time.
    # 6.08 km in 600 s of moving time, with 600 s standing still that the
    # odometer did not advance through.
    speed = [10.0] * 600 + [0.0] * 600
    odometer = [metre * 10.15 for metre in range(600)] + [6_079.85] * 600
    basis = averaging_basis(speed, recording_time_s=1_200.0)

    assessed = average_speed_kmh(distance_km(speed, odometer), basis)

    assert isinstance(assessed, Measured)
    # 6.07985 km / (600 s / 3600) — not / 1 200 s, which is D198's whole point.
    assert assessed.value == pytest.approx(36.479, abs=1e-3)
    # The source sentence travels with it, so a km/h figure is not the one
    # number on the page whose provenance has to be looked up elsewhere.
    assert any("odometer" in note for note in assessed.explanation.assumptions)
    assert any("whole recording" in note for note in assessed.explanation.assumptions)


def test_average_speed_says_which_way_its_divisor_leans_and_only_that_way() -> None:
    # Two assumptions on one number that contradict each other are worse than
    # either alone. Over moving time the average reads *higher* than distance ÷
    # elapsed; over the recording-time fallback — every indoor ride, because
    # there is no speed channel to count moving rows off — it reads lower, and
    # saying "higher" there directly contradicts the basis' own sentence
    # sitting above it.
    moving = average_speed_kmh(distance_km([10.0] * 600), riding(600))
    indoor = average_speed_kmh(
        distance_km([], [0.0, 20_000.0]),
        averaging_basis([], recording_time_s=3_600.0),
    )

    assert isinstance(moving, Measured)
    assert isinstance(indoor, Measured)
    assert any(
        "reads higher than distance ÷ elapsed time" in note
        for note in moving.explanation.assumptions
    )
    assert not any("reads higher" in note for note in indoor.explanation.assumptions)
    assert any(
        "distance ÷ recording time, which reads lower" in note
        for note in indoor.explanation.assumptions
    )


def test_average_speed_does_not_carry_the_load_duration_note() -> None:
    # The note belongs to average power, which has a load beside it computed
    # over a different duration. Average speed has no duration term in any
    # load model, and telling a km/h figure that TSS uses recording time
    # answers a question its reader did not ask.
    ride = [10.0] * 600
    assessed = average_speed_kmh(distance_km(ride), riding(600))
    powered = average_power([200.0] * 600, riding(600))

    assert isinstance(assessed, Measured)
    assert isinstance(powered, Measured)
    assert not any("TSS" in note for note in assessed.explanation.assumptions)
    assert any("TSS" in note for note in powered.explanation.assumptions)


def test_average_speed_propagates_the_reason_it_has_no_distance() -> None:
    absent = distance_km([None] * 10)

    assert average_speed_kmh(absent, riding(100)) == absent


def test_max_speed_is_reported_in_kilometres_per_hour() -> None:
    assessed = max_speed_kmh([5.0, 12.5, None, 9.0])

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(45.0)


def test_stopped_time_is_elapsed_minus_moving() -> None:
    # A 3 600 s ride with 3 000 s of movement stood still for 600 s, of which
    # the head unit paused for 120 (3 600 − 3 480 of recording time).
    basis = averaging_basis([9.0] * 3_000 + [0.0] * 480, recording_time_s=3_480.0)
    assessed = stopped_time_s(
        elapsed_time_s=3_600.0, recording_time_s=3_480.0, basis=basis
    )

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(600.0)
    assert "120 s" in assessed.explanation.inputs["of which the device paused for"]
    # And the pause is named for what it might be: on a merged session
    # (WP-6.5) the gap between two files is time the athlete may have spent
    # anywhere, and nothing in the data says otherwise.
    assert any("merged" in note for note in assessed.explanation.assumptions)


def test_stopped_time_does_not_count_a_speed_dropout_as_standing_still() -> None:
    # 60 s of the recording carry no speed reading at all. They are not
    # standing still — nothing is known about them — so they come out of the
    # subtraction rather than into the total.
    basis = averaging_basis(
        [9.0] * 3_000 + [0.0] * 480 + [None] * 60, recording_time_s=3_540.0
    )
    assessed = stopped_time_s(
        elapsed_time_s=3_600.0, recording_time_s=3_540.0, basis=basis
    )

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(540.0)  # 3 600 − 3 000 − 60
    assert assessed.explanation.inputs["no speed reading"] == "60 s"


def test_stopped_time_without_speed_is_not_a_whole_ride_at_a_standstill() -> None:
    # `elapsed − 0` would claim the athlete never moved. The reason names the
    # channel that is missing instead.
    assessed = stopped_time_s(
        elapsed_time_s=3_600.0,
        recording_time_s=3_600.0,
        basis=averaging_basis([], recording_time_s=3_600.0),
    )

    assert isinstance(assessed, NotAssessed)
    assert "speed" in assessed.reason


def test_the_minimum_is_taken_over_the_repaired_column() -> None:
    assessed = channel_minimum("temperature", [14.0, None, 9.5, 21.0])

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(9.5)


def test_a_channel_that_never_recorded_names_itself() -> None:
    assert channel_minimum("temperature", [None] * 10) == NotAssessed(
        "no temperature was recorded"
    )


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
    steady = [200.0] * 3_600
    assessed = variability_index(normalized_power(steady), steady)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(1.0)


def test_variability_index_stays_above_one_on_a_ride_full_of_traffic_lights() -> None:
    """The ratio's definition, pinned against the case that broke it (D196).

    An hour at a steady 200 W with twenty-four 25-second lights the head unit
    recorded through: NP comes out around 184 W, and the *published* average
    power — divided by the 3 000 s of moving time — comes out at 200 W. Taking
    the ratio of those two gives 0.92, a variability index below the steady
    ride's 1.0, for a ride that was strictly more ragged. Both terms have to
    come off the same series.
    """
    watts: list[float | None] = []
    for second in range(3_600):
        light = 25 <= second % 150 < 50
        watts.append(0.0 if light else 200.0)

    assessed = variability_index(
        normalized_power([value for value in watts if value is not None]), watts
    )

    assert isinstance(assessed, Measured)
    assert assessed.value >= 1.0
    # And it is not the ratio against the moving-time average, which is what
    # the header prints beside it.
    moving_seconds = sum(1 for value in watts if value)
    published = sum(value for value in watts if value is not None) / moving_seconds
    assert published == pytest.approx(200.0)
    assert assessed.value > (
        normalized_power([value for value in watts if value is not None]) / published
    )


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


def test_average_cadence_excludes_the_seconds_spent_coasting() -> None:
    # D199, in the reference ride's proportions: 356 freewheeling seconds out
    # of 5 738 drag a mean-over-everything from 82.8 rpm to 77.7, which is the
    # gap between arc's old number and every other platform's.
    ride = [83.0] * 5_382 + [0.0] * 356

    assessed = average_cadence(ride)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(83.0)
    # The excluded seconds are on the number, so 83 rpm cannot quietly
    # describe six minutes fewer than the reader thinks.
    assert assessed.explanation.inputs["coasting"] == "356 of 5738 s at 0 rpm"
    assert assessed.explanation.inputs["readings"] == "5382 of 5738 at 1 Hz"
    assert "coasting (0 rpm) excluded" in assessed.explanation.assumptions[0]


def test_average_cadence_without_a_single_turn_of_the_cranks_is_not_zero() -> None:
    # "0 rpm" is a claim about how the athlete rode; "nothing was pedalled" is
    # a claim about the recording, and only the second one is true here.
    assessed = average_cadence([0.0] * 600)

    assert isinstance(assessed, NotAssessed)
    assert "0 rpm" in assessed.reason


def test_average_cadence_without_the_channel_names_it() -> None:
    assert average_cadence([None] * 10) == NotAssessed("no cadence was recorded")


def test_elevation_gain_ignores_barometric_wander_on_a_flat_road() -> None:
    # Two metres of sawtooth wander, once a second, for twenty minutes. The
    # smoothing flattens it and nothing left clears the 3 m threshold, so a
    # flat road reports a flat road — where a raw positive-delta sum would
    # report over a kilometre of climbing.
    wander = [100.0, 102.0] * 600

    assessed = elevation_gain_m(wander)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(0.0)


def test_elevation_gain_counts_a_staircase_exactly() -> None:
    # The threshold is charged per *climb*, not per step, so five clean 20 m
    # risers separated by flat sections add up to exactly 100 m — no threshold
    # is subtracted and none is counted twice. The plateaus are long enough
    # that the centred 15 s mean reproduces each step's endpoints exactly.
    staircase: list[float | None] = [0.0] * 60
    for step in range(5):
        staircase += [float(step * 20 + metre) for metre in range(1, 21)]
        staircase += [float((step + 1) * 20)] * 60

    assessed = elevation_gain_m(staircase)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0)
    assert assessed.explanation.inputs["climbs counted"] == "1"


def test_elevation_gain_banks_a_long_climb_in_one_piece() -> None:
    # The failure the per-step form had: a threshold applied to every step
    # charges a noisy climb the threshold once per wobble. Here the same
    # 100 m is gained through 1 m of jitter and still reports 100 m.
    jittery: list[float | None] = [0.0] * 30
    for metre in range(1, 101):
        jittery += [float(metre), float(metre) - 1.0]
    jittery += [100.0] * 30

    assessed = elevation_gain_m(jittery)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0, abs=1.0)


def test_elevation_gain_loses_nothing_at_the_ends_of_a_climb() -> None:
    # A trace that *starts* and *ends* mid-climb, which the summit test above
    # deliberately does not: a shrinking window at the two ends costs about
    # half_window × slope ÷ 2 metres each, 3.5 m per end on a 100 m climb
    # sampled at a metre a second — 7 % of the climb, and invisible in any
    # test whose trace begins and finishes on the flat. Reflecting the ends
    # through the endpoint continues the trace's own slope instead, so a
    # straight run is smoothed to itself.
    steep = [float(metre) for metre in range(101)]  # 100 m at 1 m/s

    assessed = elevation_gain_m(steep)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(100.0, abs=0.01)


def test_elevation_gain_counts_a_climb_broken_by_a_flat_exactly() -> None:
    # The realistic shape of the same effect: 150 m of steady drag, a flat
    # section mid-slope, then 150 m more, with the trace beginning and ending
    # on the gradient. Every metre is real terrain and every metre is counted —
    # no threshold subtracted at the joint, nothing shaved off the two ends.
    first = [0.3 * row for row in range(500)]
    plateau = [first[-1]] * 60
    second = [first[-1] + 0.3 * row for row in range(1, 501)]
    climb: list[float] = [*first, *plateau, *second]

    assessed = elevation_gain_m(climb)

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(climb[-1] - climb[0], abs=0.05)
    assert assessed.explanation.inputs["climbs counted"] == "1"


def test_elevation_gain_rounds_off_a_summit_by_about_a_metre() -> None:
    # The documented cost of averaging first: a summit left again immediately
    # reads a little low, because a centred mean has no way to know the apex
    # was not noise. Pinned rather than hidden — an athlete comparing against a
    # device needs to know which way this leans, and by how much. A 300 m climb
    # at 0.3 m/s of ascent (a steady 6 % at 18 km/h) loses 1.1 m of it.
    rise = [0.3 * row for row in range(1_000)]
    flat = [0.0] * 60
    tent: list[float | None] = [*flat, *rise, *reversed(rise), *flat]

    assessed = elevation_gain_m(tent)

    assert isinstance(assessed, Measured)
    # The apex is 299.7 m and the smoothed trace peaks 0.98 m below it: the
    # centred window there holds seven rows of each flank, which average out
    # a little under the summit itself. "About a metre", as the explanation
    # says, and the flat run-in and run-out keep this measuring the summit
    # rather than the ends of the series.
    assert assessed.value == pytest.approx(298.72, abs=0.02)
    assert 299.7 - assessed.value < 1.5
    assert any(
        "reads about a metre lower" in note for note in assessed.explanation.assumptions
    )


def test_no_metric_function_raises_on_an_empty_series() -> None:
    # The whole point of `NotAssessed`: a session with nothing in it produces
    # an artefact full of reasons, never an exception and never a NaN.
    for assessed in (
        average_power([], riding(100)),
        average_power([], averaging_basis([], recording_time_s=0.0)),
        distance_km([]),
        average_speed_kmh(distance_km([]), riding(100)),
        max_speed_kmh([]),
        variability_index(200.0, []),
        stopped_time_s(
            elapsed_time_s=0.0,
            recording_time_s=0.0,
            basis=averaging_basis([], recording_time_s=0.0),
        ),
        channel_minimum("temperature", []),
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
