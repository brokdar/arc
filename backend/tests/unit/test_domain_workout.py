"""The structured workout: flattening, and the rules a step tree must obey.

Flattening is the function everything downstream reads — display (WP-3),
alignment (WP-5), scoring (WP-7) — and its two decisions (repeat blocks
expand, ramps stay whole) are stated as properties here so they hold for any
tree, not just the ones anybody thought to write down.
"""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.workout import (
    MAX_FLAT_STEPS,
    MAX_NESTING_DEPTH,
    MAX_REPEAT_TIMES,
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    PercentOfAnchor,
    RampStep,
    RepeatBlock,
    SteadyStep,
    StepRole,
    Target,
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


@st.composite
def matching_targets(draw: st.DrawFn, start: Target) -> Target:
    """The far end of a ramp: same kind, same anchor, different numbers.

    A ramp's ends must agree channel by channel (D53), so the end target is
    generated *from* the start one rather than drawn independently — which
    also means the strategy never discards.
    """
    if isinstance(start, PercentOfAnchor):
        low = draw(fractions)
        return PercentOfAnchor(
            anchor_type=start.anchor_type, pct_low=low, pct_high=low + 0.05
        )
    assert isinstance(start, AbsoluteRange)
    low = draw(st.integers(min_value=40, max_value=110))
    return AbsoluteRange(low=float(low), high=float(low + 10), unit=start.unit)


roles = st.sampled_from(list(StepRole))
durations = st.integers(min_value=1, max_value=3_600)
distances = st.integers(min_value=100, max_value=50_000).map(float)


@st.composite
def extents(draw: st.DrawFn) -> dict[str, Any]:
    """Exactly one extent, time-based or distance-based.

    Both, because a step may state either and a suite that only ever
    generated durations would leave the distance half of every codec and
    every property untested.
    """
    if draw(st.booleans()):
        return {"duration_s": draw(durations)}
    return {"distance_m": draw(distances)}


@st.composite
def steady_steps(draw: st.DrawFn) -> SteadyStep:
    """A steady step, stating one extent of either kind."""
    return SteadyStep(
        targets=draw(target_maps()),
        role=draw(roles),
        **draw(extents()),
    )


@st.composite
def ramp_steps(draw: st.DrawFn) -> RampStep:
    """A ramp whose ends prescribe the same channels in the same terms."""
    start = draw(target_maps())
    end = {channel: draw(matching_targets(target)) for channel, target in start.items()}
    return RampStep(
        start_targets=start,
        end_targets=end,
        role=draw(roles),
        **draw(extents()),
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
    total = total_duration_s(workout)

    # `None` when any step is distance-based — and then it has to be `None` on
    # both sides, which is as much a conservation law as the sum is.
    assert total == total_duration_s(expand(workout))
    if total is None:
        assert any(flat.distance_m is not None for flat in flatten(workout))
    else:
        assert total == sum(flat.duration_s or 0 for flat in flatten(workout))


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


def test_a_flat_step_reports_whichever_extent_its_step_states() -> None:
    flat = flatten(
        EnduranceWorkout(
            steps=(SteadyStep(duration_s=600), SteadyStep(distance_m=5_000))
        )
    )

    assert (flat[0].duration_s, flat[0].distance_m) == (600, None)
    assert (flat[1].duration_s, flat[1].distance_m) == (None, 5_000)


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


def test_a_ramp_cannot_change_what_kind_of_target_it_prescribes() -> None:
    # There is no interpolation from "60 % of a number nobody has resolved
    # yet" to "250 W", so a prescription whose meaning would depend on when
    # the anchor resolves is refused rather than frozen (D53).
    with pytest.raises(ValueError, match="same kind of target"):
        RampStep(
            start_targets={
                Channel.POWER: PercentOfAnchor(
                    anchor_type=AnchorType.FTP, pct_low=0.6, pct_high=0.6
                )
            },
            end_targets={
                Channel.POWER: AbsoluteRange(low=250, high=250, unit=ChannelUnit.WATT)
            },
            duration_s=600,
        )


def test_a_ramp_cannot_change_which_anchor_it_is_a_percentage_of() -> None:
    with pytest.raises(ValueError, match="percentages of one anchor"):
        RampStep(
            start_targets={
                Channel.HR: PercentOfAnchor(
                    anchor_type=AnchorType.LTHR, pct_low=0.7, pct_high=0.7
                )
            },
            end_targets={
                Channel.HR: PercentOfAnchor(
                    anchor_type=AnchorType.MAX_HR, pct_low=0.9, pct_high=0.9
                )
            },
            duration_s=600,
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


def test_a_semantic_failure_keeps_its_place_in_the_document() -> None:
    # The rule is enforced in `__post_init__`, which knows the value and not
    # where it came from — but the codec promises every message names its
    # position, and "exactly one of duration_s or distance_m" is useless
    # against a forty-step document without one.
    with pytest.raises(ValueError, match=r"steps\[1\]: .*exactly one of"):
        workout_body_from_json(
            {
                "discipline": "cycling",
                "steps": [
                    {"kind": "steady", "duration_s": 60},
                    {"kind": "steady", "duration_s": 60, "distance_m": 100},
                ],
            }
        )


# --- the bounds hold while decoding, not only afterwards ----------------------


def nested_repeats(levels: int) -> dict[str, Any]:
    """A step document wrapped in ``levels`` repeat blocks."""
    document: dict[str, Any] = {"kind": "steady", "duration_s": 60}
    for _ in range(levels):
        document = {"kind": "repeat", "times": 2, "children": [document]}
    return document


def test_the_nesting_bound_is_checked_while_decoding() -> None:
    # Checking it only once the whole tree is built means never reaching the
    # check: decoding is recursive, so a deep enough document exhausts the
    # interpreter stack first.
    endurance_workout_from_json({"steps": [nested_repeats(MAX_NESTING_DEPTH)]})

    with pytest.raises(ValueError, match=r"children\[0\]: repeat blocks may nest"):
        endurance_workout_from_json({"steps": [nested_repeats(MAX_NESTING_DEPTH + 1)]})


def test_a_pathologically_nested_document_is_refused_not_a_stack_overflow() -> None:
    document = {"discipline": "cycling", "steps": [nested_repeats(2_000)]}

    with pytest.raises(ValueError, match="nest at most"):
        workout_body_from_json(document)


def test_the_repeat_count_bound_holds_through_the_codec() -> None:
    legal = {
        "kind": "repeat",
        "times": MAX_REPEAT_TIMES,
        "children": [{"kind": "steady", "duration_s": 60}],
    }
    endurance_workout_from_json({"steps": [legal]})

    with pytest.raises(ValueError, match="times must be between"):
        endurance_workout_from_json(
            {"steps": [legal | {"times": MAX_REPEAT_TIMES + 1}]}
        )


def test_a_workout_that_would_flatten_past_the_bound_is_refused() -> None:
    # 100^4 leaves, from four legally-nested blocks. The bound is computed by
    # multiplying the counts, not by expanding the tree — this test finishing
    # at all is the evidence, since expanding it would need a hundred million
    # `FlatStep`s before anyone could count them.
    document: dict[str, Any] = {"kind": "steady", "duration_s": 60}
    for _ in range(MAX_NESTING_DEPTH):
        document = {"kind": "repeat", "times": 100, "children": [document]}

    with pytest.raises(ValueError, match=f"at most {MAX_FLAT_STEPS} steps"):
        endurance_workout_from_json({"steps": [document]})
