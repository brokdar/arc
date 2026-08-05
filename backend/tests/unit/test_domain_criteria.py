"""Success criteria: the vocabulary, its rules, and its stored JSON form.

Evaluation is WP-7. What matters now is that the serialized form is a stable
data contract — a criterion written today has to be readable by the scorer
built later — and that a criterion nobody could evaluate cannot be
constructed.
"""

import pytest

from app.domain.anchors import AnchorType
from app.domain.criteria import (
    AbsoluteLimit,
    Band,
    Ceiling,
    CriterionKind,
    DurationFloor,
    LoadWithin,
    PercentLimit,
    SetsCompleted,
    StepSelector,
    StepSelectorKind,
    SuccessCriterion,
    TimeInBand,
    criteria_from_json,
    criteria_to_json,
    criterion_from_json,
    criterion_to_json,
    kind_of,
    referenced_anchor_types,
)
from app.domain.workout import (
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    RepeatBlock,
    SteadyStep,
    StepRole,
    flatten,
)

EVERY_KIND: list[SuccessCriterion] = [
    TimeInBand(
        selector=StepSelector.of_role(StepRole.WORK),
        band=Band(channel=Channel.POWER, low=0.95, high=1.05),
        min_fraction=0.8,
    ),
    DurationFloor(min_seconds=1_800),
    Ceiling(
        channel=Channel.POWER,
        limit=PercentLimit(anchor_type=AnchorType.FTP, pct=0.6),
        max_seconds_above=120,
    ),
    SetsCompleted(min_fraction=0.9),
    LoadWithin(pct_tolerance=0.05),
]


# --- the tagged-union form ----------------------------------------------------


@pytest.mark.parametrize("criterion", EVERY_KIND, ids=lambda c: kind_of(c).value)
def test_every_criterion_round_trips(criterion: SuccessCriterion) -> None:
    document = criterion_to_json(criterion)

    assert document["kind"] == kind_of(criterion).value
    assert criterion_from_json(document) == criterion


def test_the_mvp_set_is_exactly_the_five_the_plan_names() -> None:
    assert {kind.value for kind in CriterionKind} == {
        "time_in_band",
        "duration_floor",
        "ceiling",
        "sets_completed",
        "load_within",
    }


def test_a_criteria_list_round_trips() -> None:
    assert criteria_from_json(criteria_to_json(EVERY_KIND)) == tuple(EVERY_KIND)


def test_an_unknown_kind_names_the_ones_that_exist() -> None:
    with pytest.raises(ValueError, match="is not one of: time_in_band"):
        criterion_from_json({"kind": "vibes"})


def test_a_field_from_another_criterion_is_rejected() -> None:
    # `extra` fields are refused so a criterion written against a future
    # vocabulary is a loud error, not a silently half-applied rule.
    with pytest.raises(ValueError, match="unknown field"):
        criterion_from_json(
            {"kind": "duration_floor", "min_seconds": 60, "channel": "power"}
        )


# --- the rules ----------------------------------------------------------------


def test_a_ceiling_must_match_its_channel() -> None:
    with pytest.raises(ValueError, match="power is measured in W"):
        Ceiling(
            channel=Channel.POWER,
            limit=AbsoluteLimit(value=150, unit=ChannelUnit.BPM),
            max_seconds_above=0,
        )


def test_a_cadence_ceiling_cannot_be_a_percentage_of_an_anchor() -> None:
    with pytest.raises(ValueError, match="cadence cannot be capped"):
        Ceiling(
            channel=Channel.CADENCE,
            limit=PercentLimit(anchor_type=AnchorType.FTP, pct=0.8),
            max_seconds_above=0,
        )


@pytest.mark.parametrize("fraction", [-0.1, 1.5])
def test_fractions_stay_between_zero_and_one(fraction: float) -> None:
    with pytest.raises(ValueError, match="must be between 0 and 1"):
        SetsCompleted(min_fraction=fraction)


def test_an_inverted_band_is_rejected() -> None:
    with pytest.raises(ValueError, match="below low"):
        Band(channel=Channel.POWER, low=1.05, high=0.95)


def test_a_selector_carries_only_its_own_argument() -> None:
    with pytest.raises(ValueError, match="a role selector needs a role"):
        StepSelector(StepSelectorKind.ROLE)
    with pytest.raises(ValueError, match="an index selector needs an index"):
        StepSelector(StepSelectorKind.INDEX)
    with pytest.raises(ValueError, match="carries no role and no index"):
        StepSelector(StepSelectorKind.ALL, index=0)


# --- selection ----------------------------------------------------------------


def sample_workout() -> EnduranceWorkout:
    """Warm-up, then 2 x (work / recovery)."""
    return EnduranceWorkout(
        steps=(
            SteadyStep(duration_s=600, role=StepRole.WARMUP),
            RepeatBlock(
                times=2,
                children=(
                    SteadyStep(duration_s=300, role=StepRole.WORK),
                    SteadyStep(duration_s=180, role=StepRole.RECOVERY),
                ),
            ),
        )
    )


def test_a_role_selector_picks_every_step_with_that_role() -> None:
    steps = flatten(sample_workout())

    picked = StepSelector.of_role(StepRole.WORK).select(steps)

    assert [step.index for step in picked] == [1, 3]


def test_an_all_selector_picks_everything() -> None:
    steps = flatten(sample_workout())

    assert len(StepSelector.all_steps().select(steps)) == len(steps)


def test_an_index_selector_picks_one_step() -> None:
    steps = flatten(sample_workout())

    assert [step.index for step in StepSelector.at_index(2).select(steps)] == [2]


def test_an_index_past_the_end_selects_nothing_rather_than_raising() -> None:
    # A criterion can outlive an edit that shortened the workout; WP-7 reports
    # that as an unevaluable criterion, not as a crash.
    steps = flatten(sample_workout())

    assert StepSelector.at_index(99).select(steps) == []


# --- anchors ------------------------------------------------------------------


def test_a_percentage_ceiling_is_an_anchor_a_session_must_pin() -> None:
    # "no more than 60 % FTP" is as unresolvable without a pinned FTP as a
    # target of "88-93 % FTP" is.
    assert referenced_anchor_types(EVERY_KIND) == frozenset({AnchorType.FTP})


def test_an_absolute_ceiling_needs_no_anchor() -> None:
    criteria = [
        Ceiling(
            channel=Channel.POWER,
            limit=AbsoluteLimit(value=180, unit=ChannelUnit.WATT),
            max_seconds_above=60,
        )
    ]

    assert referenced_anchor_types(criteria) == frozenset()
