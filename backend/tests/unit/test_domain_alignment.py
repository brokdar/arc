"""Interval detection and structure alignment (build plan WP-5.2, A7.1).

The property tests are the load-bearing ones. A worked example proves the
algorithm agrees with itself on one plan; the properties state what alignment
*means* — order is preserved, confidence falls as the execution drifts from
the prescription — and hold for the plans nobody thought to write down.
"""

from random import Random

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.domain.alignment import (
    CONFIDENCE_FLOOR,
    LOW_CONFIDENCE_REASON,
    Alignment,
    align,
    align_strength,
    default_threshold,
    detect_work_intervals,
    smooth,
)
from app.domain.workout import (
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    SteadyStep,
    StepRole,
    flatten,
)


def step(role: StepRole, duration_s: int, watts: float | None = None) -> SteadyStep:
    """One steady step, optionally with a power target."""
    targets = (
        {}
        if watts is None
        else {
            Channel.POWER: AbsoluteRange(
                low=watts - 5, high=watts + 5, unit=ChannelUnit.WATT
            )
        }
    )
    return SteadyStep(role=role, duration_s=duration_s, targets=targets)


def series(*blocks: tuple[float, int]) -> list[float | None]:
    """A power series from ``(watts, seconds)`` blocks."""
    return [watts for watts, seconds in blocks for _ in range(seconds)]


# --- detection ----------------------------------------------------------------


def test_a_steady_ride_has_no_intervals() -> None:
    # The floor on the default threshold is what makes this true: without it
    # a flat series sits at its own 25th-90th midpoint and reports itself as
    # one interval the length of the ride.
    assert detect_work_intervals(series((200.0, 3_600))) == []


def test_four_efforts_are_found_where_they_were_ridden() -> None:
    watts = series((100.0, 300))
    for _ in range(4):
        watts += series((280.0, 300), (100.0, 300))

    intervals = detect_work_intervals(watts)

    assert len(intervals) == 4
    for index, interval in enumerate(intervals):
        expected_start = 300 + index * 600
        # The 10 s centred smoothing moves each boundary by at most half the
        # window; anything more would put the table out of step with the chart.
        assert abs(interval.start_index - expected_start) <= 5
        assert interval.duration_s == pytest.approx(300, abs=10)
        assert interval.average_power == pytest.approx(280.0, abs=5)


def test_an_interval_reports_its_own_statistics() -> None:
    watts = series((100.0, 300), (280.0, 300), (100.0, 300))
    beats = series((110.0, 300), (165.0, 300), (120.0, 300))

    [interval] = detect_work_intervals(watts, hr_fixed=beats)

    assert interval.max_power == pytest.approx(280.0)
    assert interval.average_hr == pytest.approx(165.0, abs=3)
    assert interval.end_index - interval.start_index == interval.duration_s


def test_runs_shorter_than_the_minimum_are_noise() -> None:
    # A ten-second surge out of a junction is not an interval.
    watts = series((100.0, 600), (400.0, 10), (100.0, 600))

    assert detect_work_intervals(watts) == []


def test_detection_needs_no_anchor_and_is_deterministic() -> None:
    watts = series((100.0, 300), (280.0, 300), (100.0, 300))

    assert detect_work_intervals(watts) == detect_work_intervals(watts)


def test_a_recording_with_no_power_detects_nothing_rather_than_raising() -> None:
    assert detect_work_intervals([None] * 600) == []
    assert detect_work_intervals([]) == []


def test_smoothing_leaves_a_hole_a_hole() -> None:
    # A recording stop is not a period of low power, so nothing is smoothed
    # across it and no effort is invented ramping into it.
    smoothed = smooth([200.0] * 5 + [None] * 60 + [200.0] * 5, 10)

    assert smoothed[30] is None
    assert smoothed[0] == pytest.approx(200.0)


def test_a_series_too_short_to_have_a_distribution_has_no_threshold() -> None:
    assert default_threshold([]) is None
    assert default_threshold([200.0]) is None


# --- alignment ----------------------------------------------------------------


def four_by_five() -> EnduranceWorkout:
    """Warm-up, 4 × 5 min at 280 W off 5 min, cool-down."""
    steps = [step(StepRole.WARMUP, 600, 140.0)]
    for _ in range(4):
        steps.append(step(StepRole.WORK, 300, 280.0))
        steps.append(step(StepRole.RECOVERY, 300, 120.0))
    steps.append(step(StepRole.COOLDOWN, 300, 120.0))
    return EnduranceWorkout(steps=tuple(steps))


def ridden(lead_in_s: int = 0, efforts: int = 4) -> list[float | None]:
    """The 4×5 session as it was ridden, with an optional lead-in."""
    watts = series((120.0, lead_in_s)) if lead_in_s else []
    watts += series((140.0, 600))
    for _ in range(efforts):
        watts += series((280.0, 300), (120.0, 300))
    return watts + series((120.0, 300))


def test_an_as_prescribed_session_aligns_every_work_step() -> None:
    plan = flatten(four_by_five())
    detected = detect_work_intervals(ridden())

    result = align(plan, detected)

    assert len(result.aligned) == 4
    assert result.excluded == ()
    assert result.unmatched_steps == ()
    assert result.unmatched_intervals == ()
    assert all(pair.confidence > 0.9 for pair in result.aligned)


def test_the_assignment_preserves_order() -> None:
    plan = flatten(four_by_five())
    detected = detect_work_intervals(ridden())

    result = align(plan, detected)

    steps = [pair.step_index for pair in result.aligned]
    intervals = [pair.interval_index for pair in result.aligned]
    assert steps == sorted(steps)
    assert intervals == sorted(intervals)


def test_a_lead_in_is_what_the_offset_is_for() -> None:
    """A7.1: a constant offset mis-assigns everything after it.

    The athlete switched the head unit on 400 s before starting the workout
    and then rode only three of the four efforts. Every effort is the
    prescribed length, so duration cannot tell the assignment which effort is
    which — and unshifted, each detected effort sits closer to the *next*
    planned step than to its own, so the assignment slides by one and reports
    the first step as the one that was skipped. Applying the offset lines them
    up from the front, and the step actually missing — the last — is the one
    reported.
    """
    lead_in_s = 400
    plan = flatten(four_by_five())
    detected = detect_work_intervals(ridden(lead_in_s=lead_in_s, efforts=3))
    work_steps = [flat.index for flat in plan if flat.role is StepRole.WORK]

    without_offset = align(plan, detected)
    with_offset = align(plan, detected, offset_s=lead_in_s)

    assert without_offset.unmatched_steps == (work_steps[0],)
    assert with_offset.unmatched_steps == (work_steps[-1],)
    assert with_offset.offset_s == lead_in_s


def test_a_pair_below_the_floor_is_excluded_with_its_reason() -> None:
    # One 20 s effort against a 5 min prescription: duration ratio 0.067, far
    # under the floor. Aligning it anyway would let WP-7 score a step against
    # an effort that is not it.
    plan = flatten(EnduranceWorkout(steps=(step(StepRole.WORK, 300, 280.0),)))
    detected = detect_work_intervals(
        series((100.0, 600), (280.0, 40), (100.0, 600)), min_duration_s=30
    )

    result = align(plan, detected)

    assert result.aligned == ()
    assert len(result.excluded) == 1
    assert result.excluded[0].confidence < CONFIDENCE_FLOOR
    assert result.excluded[0].reason == LOW_CONFIDENCE_REASON


def test_extra_efforts_are_reported_rather_than_forced_onto_steps() -> None:
    plan = flatten(EnduranceWorkout(steps=(step(StepRole.WORK, 300, 280.0),)))
    detected = detect_work_intervals(ridden())

    result = align(plan, detected)

    assert len(result.aligned) == 1
    assert len(result.unmatched_intervals) == 3


def test_intensity_mismatch_lowers_confidence_when_a_target_exists() -> None:
    plan = flatten(EnduranceWorkout(steps=(step(StepRole.WORK, 300, 280.0),)))
    detected = detect_work_intervals(ridden(efforts=1))
    watts_by_step = {plan[0].index: 280.0}

    on_target = align(plan, detected, target_watts=watts_by_step)
    off_target = align(plan, detected, target_watts={plan[0].index: 400.0})

    assert on_target.aligned[0].confidence > off_target.aligned[0].confidence


def test_a_plan_with_no_work_steps_aligns_nothing() -> None:
    plan = flatten(EnduranceWorkout(steps=(step(StepRole.WARMUP, 600, 140.0),)))

    result = align(plan, detect_work_intervals(ridden()))

    assert result == Alignment(
        offset_s=0,
        aligned=(),
        excluded=(),
        unmatched_steps=(),
        unmatched_intervals=(0, 1, 2, 3),
    )


# --- properties ---------------------------------------------------------------


@st.composite
def plans(draw: st.DrawFn) -> tuple[EnduranceWorkout, list[int], int]:
    """A synthetic interval session, plus its work durations and lead-in."""
    count = draw(st.integers(min_value=1, max_value=5))
    durations = draw(
        st.lists(
            st.integers(min_value=120, max_value=480),
            min_size=count,
            max_size=count,
        )
    )
    lead_in = draw(st.integers(min_value=0, max_value=300))
    steps = [step(StepRole.WARMUP, 300, 140.0)]
    for duration in durations:
        steps.append(step(StepRole.WORK, duration, 280.0))
        steps.append(step(StepRole.RECOVERY, 300, 120.0))
    return EnduranceWorkout(steps=tuple(steps)), durations, lead_in


@given(plans(), st.integers(min_value=0, max_value=2**32 - 1))
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.large_base_example],
)
def test_a_noisily_executed_plan_recovers_its_own_step_order(
    plan_and_shape: tuple[EnduranceWorkout, list[int], int],
    seed: int,
) -> None:
    """Render a plan to a noisy power series and align it back.

    Noise on every sample and a random lead-in, which is what a real
    recording is: the assignment has to recover the order regardless. The
    noise comes from a `Random` seeded by a drawn integer rather than from
    `st.randoms`, so a failure replays exactly without hypothesis having to
    track ten thousand individual draws.
    """
    workout, durations, lead_in = plan_and_shape
    rng = Random(seed)  # noqa: S311 — sensor noise, not a secret
    watts: list[float | None] = [
        120.0 + rng.uniform(-10, 10) for _ in range(lead_in + 300)
    ]
    for duration in durations:
        watts += [280.0 + rng.uniform(-15, 15) for _ in range(duration)]
        watts += [120.0 + rng.uniform(-10, 10) for _ in range(300)]

    detected = detect_work_intervals(watts)
    assume(len(detected) == len(durations))

    result = align(flatten(workout), detected, offset_s=lead_in)

    steps = [pair.step_index for pair in result.aligned]
    intervals = [pair.interval_index for pair in result.aligned]
    assert steps == sorted(steps)
    assert intervals == sorted(intervals)
    assert len(result.aligned) == len(durations)


@given(
    planned_s=st.integers(min_value=60, max_value=600),
    stretch=st.floats(min_value=1.0, max_value=4.0),
)
def test_confidence_falls_as_the_duration_drifts(
    planned_s: int, stretch: float
) -> None:
    """Confidence is monotonically non-increasing under injected mismatch."""
    plan = flatten(EnduranceWorkout(steps=(step(StepRole.WORK, planned_s, 280.0),)))
    ridden_s = int(planned_s * stretch)
    closer = int(planned_s * (1 + (stretch - 1) / 2))

    def confidence(seconds: int) -> float:
        watts = series((100.0, 300), (280.0, seconds), (100.0, 300))
        detected = detect_work_intervals(watts, min_duration_s=30)
        assume(len(detected) == 1)
        result = align(plan, detected)
        pairs = [*result.aligned, *result.excluded]
        assume(len(pairs) == 1)
        return pairs[0].confidence

    assert confidence(closer) >= confidence(ridden_s) - 1e-9


# --- strength -----------------------------------------------------------------


def test_strength_pairs_by_index_and_names_both_leftovers() -> None:
    result = align_strength(prescribed=[1, 2, 3, 4], performed=[1, 2, 3])

    assert [pair.performed_index for pair in result.pairs] == [0, 1, 2]
    assert result.unmatched_prescribed == (3,)
    assert result.unmatched_performed == ()


def test_extra_sets_beyond_the_prescription_are_reported() -> None:
    result = align_strength(prescribed=[1], performed=[1, 2, 3])

    assert result.unmatched_performed == (1, 2)
