"""Predicted load and predicted volume, from the frozen intent alone.

The numbers here are what a calendar card, a week rail and every agent
proposal will quote, so the two worked examples are committed fixtures: a flat
hour at threshold (the definition of the TSS scale) and a four-interval
session whose expected load was computed independently of the implementation.
"""

import datetime as dt
import uuid
from dataclasses import fields
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.anchors import (
    AnchorSource,
    AnchorType,
    AnchorUnit,
    AnchorVersion,
    Provenance,
)
from app.domain.prediction import (
    MAX_PREDICTABLE_DURATION_S,
    PinnedAnchor,
    PredictedLoad,
    PredictedVolume,
    predict_endurance_load,
    predict_strength_volume,
)
from app.domain.strength import (
    Load,
    LoadKind,
    StrengthGroup,
    StrengthSet,
    StrengthWorkout,
)
from app.domain.workout import (
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    PercentOfAnchor,
    RampStep,
    RepeatBlock,
    SteadyStep,
    Step,
    StepRole,
    Target,
    Targets,
)

NOW = dt.datetime(2026, 6, 1, 7, 30, tzinfo=dt.UTC)
FTP_VERSION_ID = uuid.uuid7()


def ftp_anchor(value: float = 250.0) -> dict[AnchorType, PinnedAnchor]:
    """The pinned FTP version a prediction resolves against."""
    return {
        AnchorType.FTP: PinnedAnchor(
            version_id=FTP_VERSION_ID,
            version=AnchorVersion(
                anchor_type=AnchorType.FTP,
                value=value,
                unit=AnchorUnit.WATT,
                provenance=Provenance.ESTIMATED,
                effective_date=dt.date(2026, 6, 1),
                created_at=NOW,
                source=AnchorSource.ATHLETE,
            ),
        )
    }


def power(pct_low: float, pct_high: float | None = None) -> Targets:
    """A power target as a percentage range of FTP."""
    return {
        Channel.POWER: PercentOfAnchor(
            anchor_type=AnchorType.FTP,
            pct_low=pct_low,
            pct_high=pct_low if pct_high is None else pct_high,
        )
    }


# --- the worked examples ------------------------------------------------------


def test_an_hour_at_threshold_is_one_hundred() -> None:
    workout = EnduranceWorkout(
        steps=(SteadyStep(duration_s=3_600, targets=power(1.0)),)
    )

    predicted = predict_endurance_load(workout, ftp_anchor())

    assert predicted is not None
    assert predicted.intensity_factor == pytest.approx(1.0, abs=0.001)
    assert predicted.load == pytest.approx(100.0, abs=0.5)
    assert predicted.duration_s == 3_600
    assert predicted.coverage == 1.0
    assert predicted.anchor_version_id == FTP_VERSION_ID


def interval_session() -> EnduranceWorkout:
    """15 min @ 55 %, 4 x (4 min @ 105 % / 4 min @ 50 %), 10 min @ 50 %."""
    return EnduranceWorkout(
        steps=(
            SteadyStep(duration_s=900, targets=power(0.55), role=StepRole.WARMUP),
            RepeatBlock(
                times=4,
                children=(
                    SteadyStep(duration_s=240, targets=power(1.05)),
                    SteadyStep(
                        duration_s=240, targets=power(0.50), role=StepRole.RECOVERY
                    ),
                ),
            ),
            SteadyStep(duration_s=600, targets=power(0.50), role=StepRole.COOLDOWN),
        )
    )


def test_the_interval_fixture() -> None:
    """FTP 250 W: 3 420 s, NP 196.4 W, IF 0.786, load 58.6 TSS.

    Computed independently of the implementation with a naive rolling mean
    (window 30, chunk per sample) over the same 1 Hz series, then committed
    here. It is the case that would catch a change to the expansion, the
    midpoint rule or the window.
    """
    predicted = predict_endurance_load(interval_session(), ftp_anchor(250.0))

    assert predicted is not None
    assert predicted.duration_s == 3_420
    assert predicted.intensity_factor == pytest.approx(0.786, abs=0.001)
    assert predicted.load == pytest.approx(58.6, abs=0.1)
    assert predicted.coverage == 1.0


def test_a_variable_session_costs_more_than_its_average_power_suggests() -> None:
    # The reason the 1 Hz expansion goes through the 30 s rolling mean at all:
    # the same average power ridden in intervals must not predict the same
    # load as steady riding.
    # Mean power of the interval session, as a fraction of FTP:
    # (900x0.55 + 4x(240x1.05 + 240x0.50) + 600x0.50) / 3420 = 0.6675.
    mean_fraction = 0.6675
    intervals = predict_endurance_load(interval_session(), ftp_anchor())
    steady = predict_endurance_load(
        EnduranceWorkout(
            steps=(SteadyStep(duration_s=3_420, targets=power(mean_fraction)),),
        ),
        ftp_anchor(),
    )

    assert intervals is not None
    assert steady is not None
    assert steady.intensity_factor == pytest.approx(mean_fraction, abs=0.001)
    assert intervals.intensity_factor > steady.intensity_factor
    assert intervals.load > steady.load


# --- the resolution rules -----------------------------------------------------


def test_a_range_is_predicted_at_its_midpoint() -> None:
    midpoint = predict_endurance_load(
        EnduranceWorkout(steps=(SteadyStep(duration_s=1_800, targets=power(0.9)),)),
        ftp_anchor(),
    )
    ranged = predict_endurance_load(
        EnduranceWorkout(
            steps=(SteadyStep(duration_s=1_800, targets=power(0.85, 0.95)),)
        ),
        ftp_anchor(),
    )

    assert midpoint is not None
    assert ranged is not None
    assert ranged.load == pytest.approx(midpoint.load)


def test_an_absolute_target_is_used_as_given() -> None:
    absolute = predict_endurance_load(
        EnduranceWorkout(
            steps=(
                SteadyStep(
                    duration_s=1_800,
                    targets={
                        Channel.POWER: AbsoluteRange(
                            low=200.0, high=300.0, unit=ChannelUnit.WATT
                        )
                    },
                ),
            )
        ),
        ftp_anchor(250.0),
    )

    # Midpoint 250 W against a 250 W FTP.
    assert absolute is not None
    assert absolute.intensity_factor == pytest.approx(1.0, abs=0.001)


def test_a_ramp_is_interpolated_between_its_two_midpoints() -> None:
    ramp = predict_endurance_load(
        EnduranceWorkout(
            steps=(
                RampStep(
                    duration_s=600,
                    start_targets=power(0.5),
                    end_targets=power(1.0),
                ),
            )
        ),
        ftp_anchor(),
    )
    steady_at_the_mean = predict_endurance_load(
        EnduranceWorkout(steps=(SteadyStep(duration_s=600, targets=power(0.75)),)),
        ftp_anchor(),
    )

    assert ramp is not None
    assert steady_at_the_mean is not None
    # A ramp's mean is the mean of its ends (50 % -> 100 % FTP averages 75 %),
    # so its IF sits near 0.75 — and above the steady ride of the same mean,
    # because 4th-power weighting charges for the variation.
    assert ramp.intensity_factor == pytest.approx(0.75, abs=0.05)
    assert ramp.intensity_factor > steady_at_the_mean.intensity_factor


def test_a_step_with_no_power_target_lowers_coverage_but_still_predicts() -> None:
    workout = EnduranceWorkout(
        steps=(
            SteadyStep(duration_s=1_800, targets=power(0.8)),
            SteadyStep(
                duration_s=600,
                targets={
                    Channel.CADENCE: AbsoluteRange(
                        low=85.0, high=95.0, unit=ChannelUnit.RPM
                    )
                },
                role=StepRole.RECOVERY,
            ),
        )
    )

    predicted = predict_endurance_load(workout, ftp_anchor())

    assert predicted is not None
    assert predicted.coverage == pytest.approx(1_800 / 2_400)
    assert predicted.duration_s == 2_400
    assert predicted.load > 0
    # The uncovered stretch counted as zero watts, so the number is an
    # under-estimate and the explanation has to say so.
    assert any("0 W" in assumption for assumption in predicted.explanation.assumptions)


# --- when there is nothing honest to say --------------------------------------


def test_a_distance_based_step_is_not_predictable() -> None:
    workout = EnduranceWorkout(
        steps=(
            SteadyStep(duration_s=600, targets=power(0.6)),
            SteadyStep(distance_m=20_000.0, targets=power(0.85)),
        )
    )

    assert predict_endurance_load(workout, ftp_anchor()) is None


def test_without_a_pinned_ftp_there_is_no_prediction() -> None:
    workout = EnduranceWorkout(
        steps=(SteadyStep(duration_s=3_600, targets=power(1.0)),)
    )

    assert predict_endurance_load(workout, {}) is None


def test_a_workout_with_no_power_target_at_all_is_not_predictable() -> None:
    # coverage == 0: an unstructured hour has a duration and nothing to
    # integrate. A load of 0.0 would read as "an easy ride", which is a
    # different claim.
    workout = EnduranceWorkout(steps=(SteadyStep(duration_s=3_600),))

    assert predict_endurance_load(workout, ftp_anchor()) is None


def test_a_prescription_longer_than_a_day_is_not_predictable() -> None:
    # The workout model bounds steps and step counts, not their product, so
    # the 1 Hz expansion needs its own ceiling.
    long_step = SteadyStep(duration_s=43_200, targets=power(0.5))
    workout = EnduranceWorkout(steps=(long_step, long_step, long_step))

    assert sum(43_200 for _ in range(3)) > MAX_PREDICTABLE_DURATION_S
    assert predict_endurance_load(workout, ftp_anchor()) is None


# --- the explanation ----------------------------------------------------------


def test_the_prediction_explains_itself_in_terms_of_the_anchor_version() -> None:
    predicted = predict_endurance_load(interval_session(), ftp_anchor(250.0))

    assert predicted is not None
    explanation = predicted.explanation
    assert "IF²" in explanation.formula
    assert "36" in explanation.formula
    # The pinned version's own value, provenance and effective date — not
    # "the current FTP", which would stop being true the moment a new anchor
    # version is appended.
    assert explanation.inputs["FTP"] == "250 W (estimated, effective 2026-06-01)"
    assert "3420 s" in explanation.inputs["duration"]
    assert "target ranges reduced to their midpoint" in explanation.assumptions
    assert explanation.citation is not None
    assert "Coggan" in explanation.citation


# --- strength: a different axis -----------------------------------------------


def strength_workout(*sets: StrengthSet) -> StrengthWorkout:
    """A strength workout of one group per prescription line."""
    return StrengthWorkout(groups=tuple(StrengthGroup(items=(item,)) for item in sets))


def test_volume_load_is_sets_times_reps_times_kilograms() -> None:
    workout = strength_workout(
        StrengthSet(
            exercise_id="back_squat",
            sets=5,
            reps=5,
            load=Load(LoadKind.KG, 100.0),
        ),
        StrengthSet(
            exercise_id="romanian_deadlift",
            sets=3,
            reps=8,
            load=Load(LoadKind.KG, 60.0),
        ),
    )

    volume = predict_strength_volume(workout)

    assert volume.volume_load_kg == pytest.approx(5 * 5 * 100 + 3 * 8 * 60)
    assert volume.total_sets == 8
    assert volume.coverage == 1.0


def test_sets_without_kilograms_count_toward_the_total_but_not_the_volume() -> None:
    workout = strength_workout(
        StrengthSet(
            exercise_id="back_squat", sets=3, reps=5, load=Load(LoadKind.KG, 100.0)
        ),
        StrengthSet(exercise_id="push_up", sets=3, reps=12, load=Load.bodyweight()),
        StrengthSet(
            exercise_id="bench_press", sets=2, reps=5, load=Load(LoadKind.RPE, 8.0)
        ),
    )

    volume = predict_strength_volume(workout)

    assert volume.volume_load_kg == pytest.approx(1_500.0)
    assert volume.total_sets == 8
    assert volume.coverage == pytest.approx(3 / 8)


def test_a_session_with_no_kilograms_has_no_volume_load() -> None:
    workout = strength_workout(
        StrengthSet(exercise_id="push_up", sets=4, reps=15, load=Load.bodyweight())
    )

    volume = predict_strength_volume(workout)

    assert volume.volume_load_kg is None
    assert volume.total_sets == 4
    assert volume.coverage == 0.0


def test_load_and_volume_are_different_axes() -> None:
    # Kilograms and TSS are not summable (spec v2 §5.4, §8.3). The types keep
    # that true by construction: neither carries the other's quantity, so
    # there is no field a caller could total across the two.
    load_fields = {entry.name for entry in fields(PredictedLoad)}
    volume_fields = {entry.name for entry in fields(PredictedVolume)}

    assert "load" in load_fields
    assert "volume_load_kg" in volume_fields
    assert load_fields & volume_fields == {"coverage"}


# --- the property: a prediction never raises ----------------------------------
#
# Same shape as the step-tree strategies in `test_domain_workout`, with shorter
# durations: this property expands every generated tree to a 1 Hz series, so
# the size of the tree is paid for twice.

fractions = st.floats(
    min_value=0.3, max_value=1.5, allow_nan=False, allow_infinity=False
)


@st.composite
def percent_targets(draw: st.DrawFn) -> tuple[Channel, PercentOfAnchor]:
    """A percentage target on a channel that has an anchor."""
    channel = draw(st.sampled_from([Channel.POWER, Channel.HR]))
    anchor = (
        AnchorType.FTP
        if channel is Channel.POWER
        else draw(st.sampled_from([AnchorType.LTHR, AnchorType.MAX_HR]))
    )
    low = draw(fractions)
    return channel, PercentOfAnchor(
        anchor_type=anchor, pct_low=low, pct_high=low + 0.05
    )


@st.composite
def absolute_targets(draw: st.DrawFn) -> tuple[Channel, Target]:
    """An absolute target, on cadence or on power."""
    if draw(st.booleans()):
        low = draw(st.integers(min_value=40, max_value=110))
        return Channel.CADENCE, AbsoluteRange(
            low=float(low), high=float(low + 10), unit=ChannelUnit.RPM
        )
    low = draw(st.integers(min_value=80, max_value=400))
    return Channel.POWER, AbsoluteRange(
        low=float(low), high=float(low + 20), unit=ChannelUnit.WATT
    )


@st.composite
def target_maps(draw: st.DrawFn) -> Targets:
    """A per-channel target map, sometimes empty (an unstructured step)."""
    pairs = draw(st.lists(st.one_of(percent_targets(), absolute_targets()), max_size=3))
    return dict(pairs)


@st.composite
def matching_targets(draw: st.DrawFn, start: Target) -> Target:
    """The far end of a ramp: same kind, same anchor, different numbers."""
    if isinstance(start, PercentOfAnchor):
        low = draw(fractions)
        return PercentOfAnchor(
            anchor_type=start.anchor_type, pct_low=low, pct_high=low + 0.05
        )
    low = draw(st.integers(min_value=40, max_value=110))
    return AbsoluteRange(low=float(low), high=float(low + 10), unit=start.unit)


durations = st.integers(min_value=1, max_value=300)
distances = st.integers(min_value=100, max_value=50_000).map(float)


@st.composite
def extents(draw: st.DrawFn) -> dict[str, Any]:
    """Exactly one extent — mostly time-based, sometimes the distance case.

    Weighted rather than even: a distance-based step makes the whole workout
    unpredictable, and an even split would spend half the examples asserting
    the same ``None``.
    """
    if draw(st.integers(min_value=0, max_value=9)) > 0:
        return {"duration_s": draw(durations)}
    return {"distance_m": draw(distances)}


@st.composite
def steady_steps(draw: st.DrawFn) -> SteadyStep:
    """A steady step, possibly with no targets at all."""
    return SteadyStep(
        targets=draw(target_maps()),
        role=draw(st.sampled_from(list(StepRole))),
        **draw(extents()),
    )


@st.composite
def ramp_steps(draw: st.DrawFn) -> RampStep:
    """A ramp whose ends prescribe the same channels in the same terms."""
    start = draw(target_maps().filter(bool))
    end = {channel: draw(matching_targets(target)) for channel, target in start.items()}
    return RampStep(
        start_targets=start,
        end_targets=end,
        role=draw(st.sampled_from(list(StepRole))),
        **draw(extents()),
    )


def step_trees(depth: int) -> st.SearchStrategy[Step]:
    """Steps nested at most ``depth`` repeat blocks deep."""
    leaves: st.SearchStrategy[Step] = st.one_of(steady_steps(), ramp_steps())
    if depth <= 0:
        return leaves
    return st.one_of(
        leaves,
        st.builds(
            RepeatBlock,
            times=st.integers(min_value=1, max_value=3),
            children=st.lists(step_trees(depth - 1), min_size=1, max_size=3).map(tuple),
        ),
    )


workouts = (
    st.lists(step_trees(2), min_size=1, max_size=3)
    .map(tuple)
    .map(lambda steps: EnduranceWorkout(steps=steps))
)


@given(workouts, st.booleans())
@settings(max_examples=50, deadline=None)
def test_prediction_never_raises_for_any_tree_flatten_accepts(
    workout: EnduranceWorkout, with_ftp: bool
) -> None:
    predicted = predict_endurance_load(workout, ftp_anchor() if with_ftp else {})

    if predicted is None:
        return
    assert predicted.duration_s > 0
    assert 0 < predicted.coverage <= 1.0
    assert predicted.load >= 0
    assert predicted.intensity_factor >= 0
    assert predicted.anchor_version_id == FTP_VERSION_ID
