"""The scoring engine: axis math on hand-computed series, and the rule table.

Two halves, and they are tested differently on purpose.

The **axes** are arithmetic over a synthetic 1 Hz series written inline, in the
style of `test_domain_alignment.py`: every expected number here is one a reader
can do in their head from the fixture above it, because an axis test whose
expectation came out of the implementation proves only that the implementation
is deterministic.

The **verdict table** is enumerated. It is a nine-row decision table and the
order of the rows is the whole design, so every row gets a case that reaches
it — including the rows that only fire because an earlier one did not.
"""

from collections.abc import Sequence
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.anchors import AnchorType
from app.domain.criteria import (
    AbsoluteLimit,
    Band,
    Ceiling,
    DurationFloor,
    LoadWithin,
    PercentLimit,
    SetsCompleted,
    StepSelector,
    SuccessCriterion,
    TimeInBand,
)
from app.domain.metrics import Measured, NotAssessed
from app.domain.purpose import Purpose
from app.domain.scoring import (
    ABANDONED_COMPLETION,
    DISCIPLINE_EXCESS_WINDOW_S,
    EXECUTION_FLOOR,
    OVER_COMPLETION_RATIO,
    PACING_ALLOWED_FADE,
    SHORT_COMPLETION,
    STANDALONE_REASON,
    AxisResult,
    CompletionState,
    ScoredStep,
    ScoringInputs,
    TargetBias,
    Verdict,
    VerdictEvidence,
    VerdictRule,
    completion_state,
    score_session,
    score_to_json,
    suggest_verdict,
    trailing_mean,
    worst_state,
)
from app.domain.sessions import SessionStatus
from app.domain.templates import ScoringAxis
from app.domain.workout import (
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    FlatStep,
    RepeatBlock,
    SteadyStep,
    StepRole,
    flatten,
)

WORK = StepSelector.of_role(StepRole.WORK)


def watts(*blocks: tuple[float | None, int]) -> list[float | None]:
    """A 1 Hz series from ``(value, seconds)`` blocks."""
    return [value for value, seconds in blocks for _ in range(seconds)]


def step(role: StepRole, duration_s: int, target: float | None = None) -> SteadyStep:
    """One steady step, optionally with a point power target."""
    targets = (
        {}
        if target is None
        else {
            Channel.POWER: AbsoluteRange(low=target, high=target, unit=ChannelUnit.WATT)
        }
    )
    return SteadyStep(role=role, duration_s=duration_s, targets=targets)


def intervals_workout() -> list[FlatStep]:
    """Warm-up, then 2 x (5 min at 250 W, 5 min easy). Five flattened steps."""
    return flatten(
        EnduranceWorkout(
            steps=(
                step(StepRole.WARMUP, 300),
                RepeatBlock(
                    times=2,
                    children=(
                        step(StepRole.WORK, 300, 250.0),
                        step(StepRole.RECOVERY, 300),
                    ),
                ),
            )
        )
    )


def endurance_inputs(
    *,
    criteria: tuple[SuccessCriterion, ...] = (),
    axes: tuple[ScoringAxis, ...] = (ScoringAxis.COMPLETION,),
    power: list[float | None] | None = None,
    scored: tuple[ScoredStep, ...] = (),
    excluded: tuple[int, ...] = (),
    unmatched: tuple[int, ...] = (),
    planned_duration_s: int | None = 1_500,
    actual_duration_s: float | None = 1_500.0,
    anchors: dict[AnchorType, float] | None = None,
    standalone: bool = False,
) -> ScoringInputs:
    """A ride's inputs, with only the parts a given test cares about filled."""
    return ScoringInputs(
        purpose=Purpose.THRESHOLD,
        axes=axes,
        criteria=criteria,
        steps=tuple(intervals_workout()),
        planned_duration_s=planned_duration_s,
        actual_duration_s=actual_duration_s,
        channels={} if power is None else {Channel.POWER: power},
        scored_steps=scored,
        excluded_steps=excluded,
        unmatched_steps=unmatched,
        anchors=anchors or {},
        standalone=standalone,
    )


def rep(
    step_index: int,
    repetition: int,
    start: int,
    end: int,
    *,
    block: tuple[int, ...] = (1,),
    target: float = 250.0,
) -> ScoredStep:
    """One aligned work step, ridden over ``[start, end)`` against 250 W.

    ``block`` defaults to `intervals_workout`'s only repeat block, which sits
    at top-level index 1.
    """
    return ScoredStep(
        step_index=step_index,
        repetition=(repetition,),
        block=block,
        confidence=0.9,
        start_index=start,
        end_index=end,
        targets={Channel.POWER: target},
    )


def axis(inputs: ScoringInputs, which: ScoringAxis) -> AxisResult:
    """One axis of a freshly computed score."""
    result = score_session(inputs).axis(which)
    assert result is not None, f"{which.value} was not computed"
    return result


# --- the smoothing window a band is compared through --------------------------


def test_a_zero_or_one_second_window_returns_the_readings_unchanged() -> None:
    values: list[float | None] = [1.0, 2.0, None, 4.0]

    assert trailing_mean(values, 0) == values
    assert trailing_mean(values, 1) == values


def test_the_window_trails_rather_than_centring() -> None:
    # Row 2 averages rows 0-2 and nothing after them. A centred window (the one
    # `app.domain.alignment.smooth` applies) would average rows 1-3 and give
    # 30.0 — which is the whole reason this function exists beside that one.
    assert trailing_mean([10.0, 20.0, 30.0, 40.0], 3) == [10.0, 15.0, 20.0, 30.0]


def test_a_window_holding_no_reading_stays_unrecorded() -> None:
    # A recording stop is not a period of low power: averaging across one would
    # score a step against samples that do not exist.
    assert trailing_mean([None, None, 100.0], 2) == [None, None, 100.0]


def naive_trailing_mean(
    values: Sequence[float | None], window_s: int
) -> list[float | None]:
    """The definition, re-sliced per row — O(n × window) and obviously right."""
    if window_s <= 1:
        return list(values)
    smoothed: list[float | None] = []
    for index in range(len(values)):
        window = [
            value
            for value in values[max(0, index - window_s + 1) : index + 1]
            if value is not None
        ]
        smoothed.append(sum(window) / len(window) if window else None)
    return smoothed


@given(
    values=st.lists(
        st.one_of(
            st.none(),
            st.floats(min_value=-2_000.0, max_value=2_000.0, allow_nan=False, width=32),
        ),
        max_size=200,
    ),
    window_s=st.integers(min_value=0, max_value=64),
)
def test_the_carried_sum_agrees_with_re_slicing_the_window(
    values: list[float | None], window_s: int
) -> None:
    """The O(n) window is the O(n × window) definition, gaps and all.

    The implementation carries a running sum rather than re-summing each row
    (D163), which is a rewrite of arithmetic that was already correct — so the
    property worth stating is that it did not change the answer. The `None`
    handling is the delicate half: a row with no reading must count as neither
    a zero nor a sample, and a window holding none of them must stay `None`
    rather than divide by nothing.

    Compared with a tolerance, not for equality: a carried sum and a fresh sum
    over the same floats differ in the last bits, and pinning those would be
    pinning the order of additions rather than the function.
    """
    expected = naive_trailing_mean(values, window_s)
    actual = trailing_mean(values, window_s)

    assert len(actual) == len(expected)
    for index, (one, other) in enumerate(zip(actual, expected, strict=True)):
        assert (one is None) == (other is None), f"row {index} disagrees on absence"
        if one is not None and other is not None:
            assert one == pytest.approx(other, rel=1e-9, abs=1e-9), f"row {index}"


# --- completion ---------------------------------------------------------------


def test_completion_is_recorded_over_prescribed_seconds() -> None:
    result = axis(endurance_inputs(actual_duration_s=1_200.0), ScoringAxis.COMPLETION)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.8)


def test_a_longer_ride_completes_the_prescription_once_not_twice() -> None:
    result = axis(endurance_inputs(actual_duration_s=3_000.0), ScoringAxis.COMPLETION)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0
    # The clamp hides the overshoot from the axis, so the ratio travels in the
    # explanation — the verdict table reads it to tell a long ride from an
    # exact one.
    assert result.assessment.explanation.inputs["ratio"] == "2.000"


def test_a_distance_prescription_has_no_completion_to_report() -> None:
    result = axis(endurance_inputs(planned_duration_s=None), ScoringAxis.COMPLETION)

    assert isinstance(result.assessment, NotAssessed)
    assert "states no duration" in result.assessment.reason


def test_a_duration_floor_is_checked_under_completion() -> None:
    result = axis(
        endurance_inputs(
            criteria=(DurationFloor(min_seconds=1_800),),
            actual_duration_s=1_500.0,
        ),
        ScoringAxis.COMPLETION,
    )

    (outcome,) = result.criteria
    assert outcome.passed is False
    assert (outcome.observed, outcome.required) == (1_500.0, 1_800.0)


# --- adherence ----------------------------------------------------------------


#: Two work steps, one ridden at the target and one 50 W under it.
HALF_IN_BAND = watts((100.0, 300), (250.0, 300), (100.0, 300), (200.0, 300))

BAND = Band(channel=Channel.POWER, low=0.95, high=1.05, smoothing_s=0)


def adherence_inputs(**overrides: object) -> ScoringInputs:
    """A ride with both work steps aligned and one time-in-band criterion."""
    defaults: dict[str, object] = {
        "criteria": (TimeInBand(selector=WORK, band=BAND, min_fraction=0.8),),
        "axes": (ScoringAxis.COMPLETION, ScoringAxis.ADHERENCE),
        "power": HALF_IN_BAND,
        "scored": (rep(1, 1, 300, 600), rep(3, 2, 900, 1_200)),
    }
    return endurance_inputs(**(defaults | overrides))  # type: ignore[arg-type]


def test_adherence_is_the_fraction_of_aligned_time_inside_the_band() -> None:
    # 300 s at 250 W is inside 237.5-262.5 W; 300 s at 200 W is not. Half.
    result = axis(adherence_inputs(), ScoringAxis.ADHERENCE)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.5)
    (outcome,) = result.criteria
    assert outcome.passed is False
    assert outcome.observed == pytest.approx(0.5)


def test_the_criteria_are_weighted_by_the_seconds_each_one_covers() -> None:
    # One criterion over both steps (0.5 across 600 s) and one over the second
    # step alone (0.0 across 300 s): (0.5 x 600 + 0.0 x 300) / 900 = 1/3.
    result = axis(
        adherence_inputs(
            criteria=(
                TimeInBand(selector=WORK, band=BAND, min_fraction=0.8),
                TimeInBand(
                    selector=StepSelector.at_index(3), band=BAND, min_fraction=0.8
                ),
            )
        ),
        ScoringAxis.ADHERENCE,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(1 / 3)


def test_a_step_the_confidence_gate_refused_is_not_scored() -> None:
    result = axis(adherence_inputs(scored=(), excluded=(1, 3)), ScoringAxis.ADHERENCE)

    assert isinstance(result.assessment, NotAssessed)
    assert result.assessment.reason == "alignment_low_confidence"
    (outcome,) = result.criteria
    assert outcome.passed is None


def test_a_step_no_effort_matched_is_not_scored_either() -> None:
    # Different from the above, and it says so: "we could not trust the pair"
    # and "nothing in the recording answers this step" are different sentences.
    result = axis(adherence_inputs(scored=(), unmatched=(1, 3)), ScoringAxis.ADHERENCE)

    assert isinstance(result.assessment, NotAssessed)
    assert "no effort in the recording" in result.assessment.reason


def test_adherence_names_the_channel_it_did_not_get() -> None:
    result = axis(adherence_inputs(power=None), ScoringAxis.ADHERENCE)

    assert isinstance(result.assessment, NotAssessed)
    assert result.assessment.reason == "no power was recorded"


def test_a_purpose_with_no_time_in_band_criterion_has_no_adherence() -> None:
    result = axis(adherence_inputs(criteria=()), ScoringAxis.ADHERENCE)

    assert isinstance(result.assessment, NotAssessed)
    assert "no time-in-band criterion" in result.assessment.reason


def test_the_bands_frozen_smoothing_window_is_the_one_applied() -> None:
    # A smart trainer oscillating 200/300 W around a 250 W target: raw, not one
    # sample is inside +-5 %; through the 30 s window the criterion froze, all
    # but the first few are. Scoring the raw series would measure the equipment.
    spiky = watts(*[(200.0 if second % 2 else 300.0, 1) for second in range(600)])
    ridden = (
        ScoredStep(
            step_index=1,
            repetition=(1,),
            block=(1,),
            confidence=0.9,
            start_index=0,
            end_index=600,
            targets={Channel.POWER: 250.0},
        ),
    )

    raw = axis(
        adherence_inputs(
            power=spiky,
            scored=ridden,
            criteria=(TimeInBand(selector=WORK, band=BAND, min_fraction=0.8),),
        ),
        ScoringAxis.ADHERENCE,
    )
    smoothed = axis(
        adherence_inputs(
            power=spiky,
            scored=ridden,
            criteria=(
                TimeInBand(
                    selector=WORK,
                    band=Band(
                        channel=Channel.POWER, low=0.95, high=1.05, smoothing_s=30
                    ),
                    min_fraction=0.8,
                ),
            ),
        ),
        ScoringAxis.ADHERENCE,
    )

    assert isinstance(raw.assessment, Measured)
    assert raw.assessment.value == 0.0
    assert isinstance(smoothed.assessment, Measured)
    assert smoothed.assessment.value > 0.9


# --- discipline ---------------------------------------------------------------


def discipline_inputs(criterion: Ceiling, **overrides: object) -> ScoringInputs:
    """A ride with a ceiling and 120 s spent above 200 W at the end of it."""
    defaults: dict[str, object] = {
        "criteria": (criterion,),
        "axes": (ScoringAxis.COMPLETION, ScoringAxis.DISCIPLINE),
        "power": watts((150.0, 600), (300.0, 120)),
    }
    return endurance_inputs(**(defaults | overrides))  # type: ignore[arg-type]


def test_a_ceiling_inside_its_allowance_holds() -> None:
    result = axis(
        discipline_inputs(
            Ceiling(
                channel=Channel.POWER,
                limit=AbsoluteLimit(value=200.0, unit=ChannelUnit.WATT),
                max_seconds_above=120,
            )
        ),
        ScoringAxis.DISCIPLINE,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0
    (outcome,) = result.criteria
    assert (outcome.passed, outcome.observed) == (True, 120.0)


def test_a_broken_ceiling_decays_over_the_excess_beyond_its_allowance() -> None:
    # 120 s above a cap that allows 60: 60 s of excess out of the 300 s window.
    result = axis(
        discipline_inputs(
            Ceiling(
                channel=Channel.POWER,
                limit=AbsoluteLimit(value=200.0, unit=ChannelUnit.WATT),
                max_seconds_above=60,
            )
        ),
        ScoringAxis.DISCIPLINE,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(1 - 60 / DISCIPLINE_EXCESS_WINDOW_S)
    (outcome,) = result.criteria
    assert outcome.passed is False


def test_a_percentage_ceiling_resolves_against_the_pinned_anchor() -> None:
    # 75 % of a pinned FTP of 200 W is 150 W, and the ride sits exactly on it —
    # a ceiling is exceeded *above* the limit, not at it.
    result = axis(
        discipline_inputs(
            Ceiling(
                channel=Channel.POWER,
                limit=PercentLimit(anchor_type=AnchorType.FTP, pct=0.75),
                max_seconds_above=0,
            ),
            power=watts((150.0, 600)),
            anchors={AnchorType.FTP: 200.0},
        ),
        ScoringAxis.DISCIPLINE,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0


def test_a_percentage_ceiling_with_no_pinned_anchor_cannot_be_checked() -> None:
    result = axis(
        discipline_inputs(
            Ceiling(
                channel=Channel.POWER,
                limit=PercentLimit(anchor_type=AnchorType.FTP, pct=0.75),
                max_seconds_above=0,
            )
        ),
        ScoringAxis.DISCIPLINE,
    )

    assert isinstance(result.assessment, NotAssessed)
    assert "pinned no version" in result.assessment.reason


# --- pacing -------------------------------------------------------------------


def pacing_inputs(second_rep_watts: float) -> ScoringInputs:
    """Two aligned repetitions, the first at 300 W and the second at will."""
    return endurance_inputs(
        axes=(ScoringAxis.COMPLETION, ScoringAxis.PACING),
        power=watts((300.0, 300), (100.0, 300), (second_rep_watts, 300)),
        scored=(rep(1, 1, 0, 300), rep(3, 2, 600, 900)),
    )


def test_a_session_held_to_the_end_paces_perfectly() -> None:
    result = axis(pacing_inputs(300.0), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0


def test_a_fade_inside_the_allowance_costs_nothing() -> None:
    result = axis(pacing_inputs(300.0 * (1 - PACING_ALLOWED_FADE)), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(1.0)


def test_a_twenty_percent_fade_scores_a_quarter() -> None:
    # 240/300 is a 20 % fade: 5 % is free and the score reaches 0 at 25 %, so
    # 15 % of the 20 % span is spent — 1 - 0.15/0.20 = 0.25.
    result = axis(pacing_inputs(240.0), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.25)


def test_a_rider_who_finished_stronger_is_not_penalised() -> None:
    result = axis(pacing_inputs(360.0), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0


def two_block_inputs(scored: tuple[ScoredStep, ...]) -> ScoringInputs:
    """2 × 30 s sprints, then 3 × 5 min at threshold — two repeat blocks.

    The shape the single-key grouping got wrong: `FlatStep.repetition` restarts
    at 1 in the second block, so both blocks emit ``(1,)`` and ``(2,)``.
    """
    steps = flatten(
        EnduranceWorkout(
            steps=(
                RepeatBlock(
                    times=2,
                    children=(
                        step(StepRole.WORK, 30, 600.0),
                        step(StepRole.RECOVERY, 30),
                    ),
                ),
                RepeatBlock(
                    times=3,
                    children=(
                        step(StepRole.WORK, 300, 250.0),
                        step(StepRole.RECOVERY, 60),
                    ),
                ),
            )
        )
    )
    # Sprint 1 and threshold 1 both sit at repetition (1,); only the block
    # tells them apart. Flat indices: sprints at 0 and 2, threshold at 4, 6, 8.
    assert [one.repetition for one in steps if one.role is StepRole.WORK] == [
        (1,),
        (2,),
        (1,),
        (2,),
        (3,),
    ]
    return ScoringInputs(
        purpose=Purpose.THRESHOLD,
        axes=(ScoringAxis.PACING,),
        steps=tuple(steps),
        channels={
            Channel.POWER: watts(
                # Two sprints at 600 W, thirty seconds each, thirty apart.
                (600.0, 30),
                (100.0, 30),
                (600.0, 30),
                (100.0, 30),
                # Three threshold efforts at 250 W, five minutes each.
                (250.0, 300),
                (100.0, 60),
                (250.0, 300),
                (100.0, 60),
                (250.0, 300),
            )
        },
        scored_steps=scored,
    )


#: The five work steps of `two_block_inputs`, each over the rows it was ridden.
TWO_BLOCK_SCORED = (
    ScoredStep(
        step_index=0,
        repetition=(1,),
        block=(0,),
        confidence=0.9,
        start_index=0,
        end_index=30,
        targets={Channel.POWER: 600.0},
    ),
    ScoredStep(
        step_index=2,
        repetition=(2,),
        block=(0,),
        confidence=0.9,
        start_index=60,
        end_index=90,
        targets={Channel.POWER: 600.0},
    ),
    ScoredStep(
        step_index=4,
        repetition=(1,),
        block=(1,),
        confidence=0.9,
        start_index=120,
        end_index=420,
        targets={Channel.POWER: 250.0},
    ),
    ScoredStep(
        step_index=6,
        repetition=(2,),
        block=(1,),
        confidence=0.9,
        start_index=480,
        end_index=780,
        targets={Channel.POWER: 250.0},
    ),
    ScoredStep(
        step_index=8,
        repetition=(3,),
        block=(1,),
        confidence=0.9,
        start_index=840,
        end_index=1_140,
        targets={Channel.POWER: 250.0},
    ),
)


def test_two_repeat_blocks_are_not_one_set_of_repetitions() -> None:
    """Every effort was held exactly; the axis must say so.

    Both blocks are ridden flat — sprint 1 and sprint 2 at 600 W, all three
    threshold efforts at 250 W — so neither faded and pacing is 1.0. Keyed on
    the iteration number alone, "rep 1" would be sprint 1 *concatenated with*
    threshold 1 and "rep 3" would be threshold 3 by itself, so the axis would
    compare a mixed 600/250 W effort against a 250 W one and report a fade of
    about a third for a session that never faded at all.
    """
    result = axis(two_block_inputs(TWO_BLOCK_SCORED), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0
    assert result.assessment.explanation.inputs["repeat blocks measured"] == "2"


def test_the_worst_repeat_block_is_the_axis() -> None:
    # The sprints hold; the last threshold effort is 20 % down on the first —
    # 5 % free, zero at 25 %, so that block scores 0.25. A session is not well
    # paced because one of its two blocks was.
    inputs = two_block_inputs(TWO_BLOCK_SCORED)
    faded = list(inputs.channels[Channel.POWER])
    faded[840:1_140] = [200.0] * 300

    result = axis(replace(inputs, channels={Channel.POWER: faded}), ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.25)
    assert result.assessment.explanation.inputs["worst block"] == "2"
    assert (
        result.assessment.explanation.inputs["score by block"]
        == "block 1 100%, block 2 25%"
    )


def test_a_block_ridden_once_is_not_a_pace_to_fade_across() -> None:
    # One sprint and three threshold efforts: the sprint block has no second
    # repetition to fade to, so it is left out rather than compared against
    # the other block's.
    inputs = two_block_inputs(
        (TWO_BLOCK_SCORED[0], *TWO_BLOCK_SCORED[2:]),
    )

    result = axis(inputs, ScoringAxis.PACING)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.explanation.inputs["repeat blocks measured"] == "1"
    assert result.assessment.value == 1.0


def test_one_repetition_is_not_a_pace_to_fade_across() -> None:
    inputs = endurance_inputs(
        axes=(ScoringAxis.COMPLETION, ScoringAxis.PACING),
        power=watts((300.0, 300)),
        scored=(rep(1, 1, 0, 300),),
    )

    result = axis(inputs, ScoringAxis.PACING)

    assert isinstance(result.assessment, NotAssessed)
    assert "fewer than two repeated work blocks" in result.assessment.reason


# --- sets_load ----------------------------------------------------------------


def strength_inputs(
    *,
    performed_sets: int | None = 6,
    performed_loads: tuple[float | None, ...] = (100.0,) * 6,
    criteria: tuple[SuccessCriterion, ...] = (
        SetsCompleted(min_fraction=1.0),
        LoadWithin(pct_tolerance=0.1),
    ),
) -> ScoringInputs:
    """Six sets prescribed at 100 kg, and whatever was logged against them."""
    return ScoringInputs(
        purpose=Purpose.MAX_STRENGTH,
        axes=(ScoringAxis.COMPLETION, ScoringAxis.SETS_LOAD),
        criteria=criteria,
        planned_sets=6,
        performed_sets=performed_sets,
        prescribed_loads_kg=(100.0,) * 6,
        performed_loads_kg=performed_loads,
    )


def test_sets_load_is_sets_completed_times_loads_within_tolerance() -> None:
    # Every set logged, but three of the six were 20 % light: 1.0 x 0.5.
    result = axis(
        strength_inputs(performed_loads=(100.0, 100.0, 100.0, 80.0, 80.0, 80.0)),
        ScoringAxis.SETS_LOAD,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.5)
    sets_done, load_within = result.criteria
    assert (sets_done.passed, sets_done.observed) == (True, 1.0)
    assert (load_within.passed, load_within.observed) == (False, 0.5)


def test_half_the_sets_at_the_right_weight_is_half_the_axis() -> None:
    result = axis(
        strength_inputs(performed_sets=3, performed_loads=(100.0,) * 3),
        ScoringAxis.SETS_LOAD,
    )

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == pytest.approx(0.5)


def test_a_session_with_no_comparable_weight_keeps_the_sets_term_alone() -> None:
    # Bodyweight work logs no kilograms. Counting the missing load term as zero
    # would score a completed session at nothing.
    result = axis(strength_inputs(performed_loads=(None,) * 6), ScoringAxis.SETS_LOAD)

    assert isinstance(result.assessment, Measured)
    assert result.assessment.value == 1.0
    _, load_within = result.criteria
    assert load_within.passed is None


def test_a_gym_session_with_nothing_logged_has_no_sets_load() -> None:
    result = axis(
        strength_inputs(performed_sets=None, performed_loads=()),
        ScoringAxis.SETS_LOAD,
    )

    assert isinstance(result.assessment, NotAssessed)
    assert "no sets were logged" in result.assessment.reason


# --- the shape of a whole score -------------------------------------------------


def test_the_reserved_axes_are_present_and_deferred() -> None:
    score = score_session(
        endurance_inputs(
            axes=(
                ScoringAxis.COMPLETION,
                ScoringAxis.RESPONSE,
                ScoringAxis.FUELLING,
            )
        )
    )

    for reserved in (ScoringAxis.RESPONSE, ScoringAxis.FUELLING):
        result = score.axis(reserved)
        assert result is not None
        assert result.assessment == NotAssessed("deferred")


def test_only_the_axes_the_template_lists_are_scored() -> None:
    score = score_session(adherence_inputs())

    assert [one.axis for one in score.axes] == [
        ScoringAxis.COMPLETION,
        ScoringAxis.ADHERENCE,
    ]


def test_a_criterion_whose_axis_the_purpose_omits_is_still_reported() -> None:
    # `tempo` is not scored on `discipline`, but a ceiling frozen into its
    # intent is still part of the prescription — dropping it would be a promise
    # nobody could see whether we kept.
    score = score_session(
        endurance_inputs(
            axes=(ScoringAxis.COMPLETION, ScoringAxis.ADHERENCE),
            criteria=(
                Ceiling(
                    channel=Channel.POWER,
                    limit=AbsoluteLimit(value=200.0, unit=ChannelUnit.WATT),
                    max_seconds_above=0,
                ),
            ),
            power=watts((300.0, 600)),
        )
    )

    (outcome,) = score.other_criteria
    assert outcome.passed is False
    assert outcome.observed == 600.0


def test_a_displaced_link_is_scored_standalone_and_compares_nothing() -> None:
    score = score_session(
        adherence_inputs(standalone=True)  # every input present, and unused
    )

    assert score.standalone is True
    assert score.suggested_verdict is Verdict.DIFFERENT_SESSION
    assert all(one.assessment == NotAssessed(STANDALONE_REASON) for one in score.axes)
    assert [one.not_assessed for one in score.other_criteria] == [STANDALONE_REASON]


def test_the_stored_payload_carries_every_axis_and_its_explanation() -> None:
    document = score_to_json(score_session(adherence_inputs()))

    assert document["suggested_verdict"] == Verdict.UNDER.value
    assert document["verdict_rule"] == VerdictRule.OFF_TARGET_UNDER.value
    completion, adherence = document["axes"]
    assert completion["axis"] == "completion"
    assert completion["explanation"]["formula"].startswith("completion =")
    assert completion["not_assessed"] is None
    assert adherence["criteria"][0]["kind"] == "time_in_band"


def test_scoring_a_session_with_nothing_measurable_still_answers() -> None:
    # The whole path is total: an ingest that scored nothing rather than
    # scoring "not assessed" would leave a matched ride with an exception.
    score = score_session(
        endurance_inputs(
            axes=(
                ScoringAxis.COMPLETION,
                ScoringAxis.ADHERENCE,
                ScoringAxis.DISCIPLINE,
                ScoringAxis.PACING,
                ScoringAxis.SETS_LOAD,
            ),
            actual_duration_s=None,
        )
    )

    assert all(isinstance(one.assessment, NotAssessed) for one in score.axes)
    assert score.suggested_verdict is Verdict.AS_INTENDED
    assert score.verdict_rule is VerdictRule.NOTHING_CONTRADICTS


# --- the verdict rule table -----------------------------------------------------


@pytest.mark.parametrize(
    ("evidence", "verdict", "rule"),
    [
        # Row 1: a displaced link short-circuits everything below it, however
        # good the axes look — they compare against a prescription nobody rode.
        (
            VerdictEvidence(standalone=True, completion=1.0, execution=1.0),
            Verdict.DIFFERENT_SESSION,
            VerdictRule.DISPLACED_LINK,
        ),
        # Row 2 beats row 3: a ride abandoned a third of the way in can have
        # been perfectly in band while it lasted.
        (
            VerdictEvidence(completion=ABANDONED_COMPLETION - 0.01, execution=1.0),
            Verdict.ABANDONED,
            VerdictRule.COMPLETION_BELOW_FLOOR,
        ),
        (
            VerdictEvidence(completion=ABANDONED_COMPLETION),
            Verdict.UNDER,
            VerdictRule.COMPLETION_SHORT,
        ),
        # Row 3, at the floor exactly — the band is inclusive at the bottom.
        (
            VerdictEvidence(completion=1.0, execution=EXECUTION_FLOOR),
            Verdict.AS_INTENDED,
            VerdictRule.EXECUTION_AT_OR_ABOVE_FLOOR,
        ),
        # …and an unmeasurable ceiling does not block it. `None` is not `False`.
        (
            VerdictEvidence(completion=1.0, execution=0.95, discipline_ok=None),
            Verdict.AS_INTENDED,
            VerdictRule.EXECUTION_AT_OR_ABOVE_FLOOR,
        ),
        # Row 4: off target, and above it.
        (
            VerdictEvidence(
                completion=1.0,
                execution=EXECUTION_FLOOR - 0.01,
                bias=TargetBias.OVER,
            ),
            Verdict.OVER,
            VerdictRule.OFF_TARGET_OVER,
        ),
        # Row 5: off target below it, and off target evenly both ways — the
        # honest default is that the work asked for was not done.
        (
            VerdictEvidence(completion=1.0, execution=0.4, bias=TargetBias.UNDER),
            Verdict.UNDER,
            VerdictRule.OFF_TARGET_UNDER,
        ),
        (
            VerdictEvidence(completion=1.0, execution=0.4, bias=TargetBias.ON_TARGET),
            Verdict.UNDER,
            VerdictRule.OFF_TARGET_UNDER,
        ),
        # Rows 4-5 beat row 6: how a session missed its targets describes it
        # better than the cap it also broke.
        (
            VerdictEvidence(
                completion=1.0,
                execution=0.4,
                bias=TargetBias.UNDER,
                discipline_ok=False,
            ),
            Verdict.UNDER,
            VerdictRule.OFF_TARGET_UNDER,
        ),
        # Row 6: adherence held and the ceiling did not, so row 3 was refused
        # and this is what catches it.
        (
            VerdictEvidence(completion=1.0, execution=1.0, discipline_ok=False),
            Verdict.OVER,
            VerdictRule.CEILING_EXCEEDED,
        ),
        (
            VerdictEvidence(completion=1.0, discipline_ok=False),
            Verdict.OVER,
            VerdictRule.CEILING_EXCEEDED,
        ),
        # Row 7: nothing to say about execution, and the ride ran long.
        (
            VerdictEvidence(completion=1.0, completion_ratio=OVER_COMPLETION_RATIO),
            Verdict.OVER,
            VerdictRule.COMPLETION_ABOVE_CEILING,
        ),
        (
            VerdictEvidence(
                completion=1.0, completion_ratio=OVER_COMPLETION_RATIO - 0.01
            ),
            Verdict.AS_INTENDED,
            VerdictRule.NOTHING_CONTRADICTS,
        ),
        # Row 8: short of the prescription, but not abandoned.
        (
            VerdictEvidence(completion=SHORT_COMPLETION - 0.01),
            Verdict.UNDER,
            VerdictRule.COMPLETION_SHORT,
        ),
        (
            VerdictEvidence(completion=SHORT_COMPLETION),
            Verdict.AS_INTENDED,
            VerdictRule.NOTHING_CONTRADICTS,
        ),
        # Row 9, both ways into it: everything assessable passed, and nothing
        # was assessable at all.
        (
            VerdictEvidence(completion=1.0, discipline_ok=True),
            Verdict.AS_INTENDED,
            VerdictRule.NOTHING_CONTRADICTS,
        ),
        (
            VerdictEvidence(),
            Verdict.AS_INTENDED,
            VerdictRule.NOTHING_CONTRADICTS,
        ),
    ],
)
def test_the_verdict_table_row_by_row(
    evidence: VerdictEvidence, verdict: Verdict, rule: VerdictRule
) -> None:
    suggestion = suggest_verdict(evidence)

    assert (suggestion.verdict, suggestion.rule) == (verdict, rule)
    assert suggestion.rationale


def test_every_row_of_the_table_is_reachable() -> None:
    """No rule may be unreachable — an unreachable row is a dead branch."""
    cases = [
        VerdictEvidence(standalone=True),
        VerdictEvidence(completion=0.1),
        VerdictEvidence(execution=1.0),
        VerdictEvidence(execution=0.1, bias=TargetBias.OVER),
        VerdictEvidence(execution=0.1, bias=TargetBias.UNDER),
        VerdictEvidence(discipline_ok=False),
        VerdictEvidence(completion_ratio=2.0),
        VerdictEvidence(completion=0.6),
        VerdictEvidence(),
    ]

    assert {suggest_verdict(one).rule for one in cases} == set(VerdictRule)


# --- the week strip's completion state --------------------------------------------


@pytest.mark.parametrize(
    ("status", "verdict", "expected"),
    [
        (SessionStatus.PLANNED, None, CompletionState.PLANNED),
        # A pending proposal leaves the session `planned`, so the strip shows a
        # question rather than a completion (D140).
        (SessionStatus.PLANNED, Verdict.AS_INTENDED, CompletionState.PLANNED),
        (SessionStatus.MISSED, None, CompletionState.MISSED),
        (SessionStatus.DISPLACED, None, CompletionState.DISPLACED),
        # Matched and recorded, and not yet judged. This is why the member the
        # build plan does not name has to exist.
        (SessionStatus.COMPLETED, None, CompletionState.COMPLETED),
        (
            SessionStatus.COMPLETED,
            Verdict.AS_INTENDED,
            CompletionState.COMPLETED_AS_INTENDED,
        ),
        (SessionStatus.COMPLETED, Verdict.UNDER, CompletionState.UNDER),
        (SessionStatus.COMPLETED, Verdict.OVER, CompletionState.OVER),
        (SessionStatus.COMPLETED, Verdict.ABANDONED, CompletionState.ABANDONED),
        (
            SessionStatus.COMPLETED,
            Verdict.DIFFERENT_SESSION,
            CompletionState.DIFFERENT_SESSION,
        ),
    ],
)
def test_the_state_a_card_is_in(
    status: SessionStatus, verdict: Verdict | None, expected: CompletionState
) -> None:
    assert completion_state(status, verdict) is expected


def test_a_day_rolls_up_to_its_worst_session() -> None:
    # A strip that showed the best of a day's outcomes would hide the abandoned
    # session behind the completed one, which is what the strip is for.
    assert (
        worst_state([CompletionState.COMPLETED_AS_INTENDED, CompletionState.ABANDONED])
        is CompletionState.ABANDONED
    )
    assert worst_state([CompletionState.UNPLANNED]) is CompletionState.UNPLANNED
    assert worst_state([]) is None
