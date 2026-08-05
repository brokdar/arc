"""The structured workout: flattening, and the rules a step tree must obey.

Flattening is the function everything downstream reads — display (WP-3),
alignment (WP-5), scoring (WP-7) — and its two decisions (repeat blocks
expand, ramps stay whole) are stated as properties here so they hold for any
tree, not just the ones anybody thought to write down.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.workout import (
    MAX_NESTING_DEPTH,
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    PercentOfAnchor,
    RampStep,
    RepeatBlock,
    SteadyStep,
    StepRole,
    Targets,
    endurance_workout_from_json,
    endurance_workout_to_json,
    expand,
    flatten,
    referenced_anchor_types,
    total_duration_s,
    workout_body_from_json,
    workout_body_to_json,
)

# --- strategies ---------------------------------------------------------------

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
def absolute_targets(draw: st.DrawFn) -> tuple[Channel, AbsoluteRange]:
    """An absolute target on the one channel with no anchor."""
    low = draw(st.integers(min_value=40, max_value=110))
    return Channel.CADENCE, AbsoluteRange(
        low=float(low), high=float(low + 10), unit=ChannelUnit.RPM
    )


@st.composite
def target_maps(draw: st.DrawFn) -> Targets:
    """A per-channel target map with at least one entry."""
    pairs = draw(
        st.lists(
            st.one_of(percent_targets(), absolute_targets()), min_size=1, max_size=3
        )
    )
    return dict(pairs)


roles = st.sampled_from(list(StepRole))
durations = st.integers(min_value=1, max_value=3_600)


@st.composite
def steady_steps(draw: st.DrawFn) -> SteadyStep:
    """A steady step, always time-based so durations stay summable."""
    return SteadyStep(
        duration_s=draw(durations),
        targets=draw(target_maps()),
        role=draw(roles),
    )


@st.composite
def ramp_steps(draw: st.DrawFn) -> RampStep:
    """A ramp whose ends prescribe the same channels."""
    start = draw(target_maps())
    end = {channel: target for channel, target in draw(target_maps()).items()}
    # A ramp must start and end on the same channels; reuse the start map's
    # keys rather than filtering, so the strategy never discards.
    end = {channel: start[channel] for channel in start} | {
        channel: value for channel, value in end.items() if channel in start
    }
    return RampStep(
        start_targets=start,
        end_targets=end,
        duration_s=draw(durations),
        role=draw(roles),
    )


def step_trees(depth: int) -> st.SearchStrategy[object]:
    """Steps nested at most ``depth`` repeat blocks deep."""
    leaves = st.one_of(steady_steps(), ramp_steps())
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


# --- the properties flattening must have -------------------------------------


@given(workouts)
@settings(max_examples=100)
def test_flatten_expand_round_trips(workout: EnduranceWorkout) -> None:
    # The round trip: flattening produces a sequence of leaves, rebuilding a
    # repeat-free workout from them flattens to exactly the same leaves, and
    # rebuilding again changes nothing. Anything that dropped, duplicated or
    # reordered a step would break one of the three.
    flat = flatten(workout)
    expanded = expand(workout)

    assert [step.step for step in flat] == [step.step for step in flatten(expanded)]
    assert expand(expanded) == expanded
    assert all(not isinstance(step, RepeatBlock) for step in expanded.steps)


@given(workouts)
@settings(max_examples=100)
def test_flat_steps_are_indexed_in_execution_order(workout: EnduranceWorkout) -> None:
    flat = flatten(workout)

    assert [step.index for step in flat] == list(range(len(flat)))
    assert len(flat) == len(expand(workout).steps)


@given(workouts)
@settings(max_examples=100)
def test_repeat_blocks_expand_and_are_traceable(workout: EnduranceWorkout) -> None:
    for step in flatten(workout):
        # Every enclosing block contributed one 1-based iteration number, and
        # the path names one position per level of the tree.
        assert all(iteration >= 1 for iteration in step.repetition)
        assert len(step.path) == len(step.repetition) + 1


@given(workouts)
@settings(max_examples=100)
def test_duration_is_conserved_by_expansion(workout: EnduranceWorkout) -> None:
    assert total_duration_s(workout) == total_duration_s(expand(workout))
    assert total_duration_s(workout) == sum(
        step.step.duration_s or 0 for step in flatten(workout)
    )


@given(workouts)
@settings(max_examples=100)
def test_serialization_round_trips(workout: EnduranceWorkout) -> None:
    assert endurance_workout_from_json(endurance_workout_to_json(workout)) == workout


# --- ramps stay ramps ---------------------------------------------------------


def test_a_ramp_is_not_chopped_into_steady_slices() -> None:
    # Slicing a ramp would invent a step count the athlete never rode. The
    # flat step carries both ends instead, so a consumer that only speaks
    # steady blocks can take the midpoint and one that understands ramps can
    # do better.
    ramp = RampStep(
        start_targets={
            Channel.POWER: AbsoluteRange(low=100, high=100, unit=ChannelUnit.WATT)
        },
        end_targets={
            Channel.POWER: AbsoluteRange(low=300, high=300, unit=ChannelUnit.WATT)
        },
        duration_s=600,
    )

    flat = flatten(EnduranceWorkout(steps=(ramp,)))

    assert len(flat) == 1
    assert flat[0].is_ramp
    assert flat[0].start_targets != flat[0].end_targets


def test_a_steady_step_reports_one_set_of_targets_at_both_ends() -> None:
    steady = SteadyStep(
        duration_s=60,
        targets={
            Channel.POWER: AbsoluteRange(low=200, high=220, unit=ChannelUnit.WATT)
        },
    )

    flat = flatten(EnduranceWorkout(steps=(steady,)))[0]

    assert not flat.is_ramp
    assert flat.start_targets == flat.end_targets == steady.targets


# --- a worked example ---------------------------------------------------------


def sweet_spot_workout() -> EnduranceWorkout:
    """20 min warm-up ramp, 3 x (8 min on / 4 min off), 10 min cool-down."""
    return EnduranceWorkout(
        steps=(
            RampStep(
                start_targets={
                    Channel.POWER: PercentOfAnchor(
                        anchor_type=AnchorType.FTP, pct_low=0.5, pct_high=0.5
                    )
                },
                end_targets={
                    Channel.POWER: PercentOfAnchor(
                        anchor_type=AnchorType.FTP, pct_low=0.75, pct_high=0.75
                    )
                },
                duration_s=1_200,
                role=StepRole.WARMUP,
            ),
            RepeatBlock(
                times=3,
                children=(
                    SteadyStep(
                        duration_s=480,
                        targets={
                            Channel.POWER: PercentOfAnchor(
                                anchor_type=AnchorType.FTP, pct_low=0.88, pct_high=0.93
                            )
                        },
                        role=StepRole.WORK,
                        name="Sweet spot",
                    ),
                    SteadyStep(
                        duration_s=240,
                        targets={
                            Channel.POWER: PercentOfAnchor(
                                anchor_type=AnchorType.FTP, pct_low=0.5, pct_high=0.6
                            )
                        },
                        role=StepRole.RECOVERY,
                    ),
                ),
            ),
            SteadyStep(duration_s=600, role=StepRole.COOLDOWN),
        )
    )


def test_the_worked_example_flattens_as_ridden() -> None:
    flat = flatten(sweet_spot_workout())

    assert len(flat) == 1 + 3 * 2 + 1
    assert [step.role for step in flat[1:7]] == [
        StepRole.WORK,
        StepRole.RECOVERY,
    ] * 3
    # Third rep of the block, first child of it.
    assert flat[5].repetition == (3,)
    assert flat[5].path == (1, 0)
    assert total_duration_s(sweet_spot_workout()) == 1_200 + 3 * 720 + 600


def test_the_anchors_a_prescription_needs_are_discoverable() -> None:
    # This is what a planned session pins at creation time.
    assert referenced_anchor_types(sweet_spot_workout()) == frozenset({AnchorType.FTP})


def test_an_absolute_prescription_needs_no_anchor() -> None:
    workout = EnduranceWorkout(
        steps=(
            SteadyStep(
                duration_s=60,
                targets={
                    Channel.POWER: AbsoluteRange(
                        low=200, high=220, unit=ChannelUnit.WATT
                    )
                },
            ),
        )
    )

    assert referenced_anchor_types(workout) == frozenset()


# --- the rules ----------------------------------------------------------------


def test_a_step_needs_exactly_one_extent() -> None:
    with pytest.raises(ValueError, match="exactly one of duration_s or distance_m"):
        SteadyStep()
    with pytest.raises(ValueError, match="exactly one of duration_s or distance_m"):
        SteadyStep(duration_s=60, distance_m=1_000)


def test_cadence_cannot_be_a_percentage_of_an_anchor() -> None:
    # There is no cadence anchor, and "80 % of FTP rpm" is not a quantity.
    with pytest.raises(ValueError, match="cadence cannot be prescribed"):
        SteadyStep(
            duration_s=60,
            targets={
                Channel.CADENCE: PercentOfAnchor(
                    anchor_type=AnchorType.FTP, pct_low=0.8, pct_high=0.9
                )
            },
        )


def test_power_cannot_be_a_percentage_of_a_heart_rate_anchor() -> None:
    with pytest.raises(ValueError, match="power cannot be prescribed"):
        SteadyStep(
            duration_s=60,
            targets={
                Channel.POWER: PercentOfAnchor(
                    anchor_type=AnchorType.LTHR, pct_low=0.8, pct_high=0.9
                )
            },
        )


def test_an_absolute_target_must_use_the_channel_unit() -> None:
    with pytest.raises(ValueError, match="power is measured in W"):
        SteadyStep(
            duration_s=60,
            targets={
                Channel.POWER: AbsoluteRange(low=100, high=120, unit=ChannelUnit.BPM)
            },
        )


def test_an_implausible_absolute_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="must lie between"):
        SteadyStep(
            duration_s=60,
            targets={
                Channel.POWER: AbsoluteRange(
                    low=100, high=25_000, unit=ChannelUnit.WATT
                )
            },
        )


def test_an_inverted_target_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="below pct_low"):
        PercentOfAnchor(anchor_type=AnchorType.FTP, pct_low=0.9, pct_high=0.8)


def test_a_ramp_must_start_and_end_on_the_same_channels() -> None:
    with pytest.raises(ValueError, match="same channels"):
        RampStep(
            start_targets={
                Channel.POWER: AbsoluteRange(low=100, high=100, unit=ChannelUnit.WATT)
            },
            end_targets={
                Channel.HR: AbsoluteRange(low=120, high=120, unit=ChannelUnit.BPM)
            },
            duration_s=300,
        )


def test_a_repeat_block_needs_children_and_at_least_one_iteration() -> None:
    with pytest.raises(ValueError, match="at least one child"):
        RepeatBlock(times=3, children=())
    with pytest.raises(ValueError, match="times must be between"):
        RepeatBlock(times=0, children=(SteadyStep(duration_s=60),))


def test_nesting_is_bounded() -> None:
    # The tree arrives as user-supplied JSON and flattening is exponential in
    # depth, so the bound is a guard, not a style rule.
    step: object = SteadyStep(duration_s=60)
    for _ in range(MAX_NESTING_DEPTH + 1):
        step = RepeatBlock(times=2, children=(step,))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="nest at most"):
        EnduranceWorkout(steps=(step,))  # type: ignore[arg-type]


def test_a_workout_needs_a_step() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        EnduranceWorkout(steps=())


def test_a_distance_step_makes_the_total_duration_unknowable() -> None:
    # A partial sum that silently ignores half the prescription is worse than
    # no number.
    workout = EnduranceWorkout(
        steps=(SteadyStep(duration_s=600), SteadyStep(distance_m=5_000))
    )

    assert total_duration_s(workout) is None


# --- the discipline-tagged envelope -------------------------------------------


def test_the_body_envelope_round_trips_and_names_its_discipline() -> None:
    workout = sweet_spot_workout()

    document = workout_body_to_json(workout)

    assert document["discipline"] == Discipline.CYCLING.value
    assert workout_body_from_json(document) == workout


def test_an_untagged_body_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing required field 'discipline'"):
        workout_body_from_json({"steps": []})


def test_an_unknown_step_kind_names_the_ones_that_exist() -> None:
    with pytest.raises(ValueError, match="not one of: steady, ramp, repeat"):
        workout_body_from_json(
            {"discipline": "cycling", "steps": [{"kind": "interval"}]}
        )


def test_an_unknown_field_is_rejected_rather_than_ignored() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        workout_body_from_json(
            {
                "discipline": "cycling",
                "steps": [{"kind": "steady", "duration_s": 60, "colour": "red"}],
            }
        )
