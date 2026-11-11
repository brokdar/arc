"""Suggested weights: each rule's arithmetic and its limits (AC-68 to AC-70).

Pure domain tests over `app.domain.progression.suggest`. Every history is
written oldest first, as the repository returns it; every expected weight is
the one the plan's acceptance criterion states, so a test fails when the rule
moves rather than agreeing with whatever it computes.
"""

import datetime as dt
import uuid
from dataclasses import replace
from typing import Any

import pytest

from app.domain.activity import EffortSimple, LoggedSetKind, SetSide
from app.domain.loading import Equipment
from app.domain.metrics import PerformedSet
from app.domain.plans import PlanPhase
from app.domain.progression import (
    LineGoal,
    SuggestionOutcome,
    SuggestionReason,
    resolve_rule,
    suggest,
    trimmed,
)
from app.domain.rules import (
    BACKOFF_DROP,
    BIG_STEP,
    PLATEAU_RESET,
    PROGRESSION_HOLD,
    PROGRESSION_MAX_DECREASE,
    PROGRESSION_MAX_INCREASE,
    REPS_THEN_WEIGHT,
    SIMPLE_PROGRESSION,
    SMALL_MUSCLE_MAX_STEPS,
    WEIGHT_FROM_EFFORT,
)
from app.domain.strength import (
    Count,
    Load,
    LoadKind,
    Progression,
    SetKind,
    StrengthSet,
    TargetRange,
)
from app.domain.strength_records import Exposure
from app.domain.training_mode import TrainingMode

FIRST_DAY = dt.date(2026, 9, 1)


def ex(
    n: int, *sets: PerformedSet, dont_learn: bool = False, cut: bool = False
) -> Exposure:
    """Session ``n`` holding ``sets``; ``cut`` marks it trimmed."""
    return Exposure(
        session_id=uuid.UUID(int=n),
        date=FIRST_DAY + dt.timedelta(days=3 * n),
        sets=sets,
        dont_learn=dont_learn,
        trimmed=cut,
    )


def s(
    load: float | None,
    reps: int,
    left: int | None = None,
    *,
    capped: bool = False,
    simple: EffortSimple | None = None,
    kind: LoggedSetKind = LoggedSetKind.WORKING,
    side: SetSide = SetSide.BOTH,
) -> PerformedSet:
    return PerformedSet(
        load_kg=load,
        reps=reps,
        rir=left,
        rir_capped=capped,
        effort_simple=simple,
        kind=kind,
        side=side,
    )


def effort(reps: Count = 8, left: Count | None = 2, **overrides: Any) -> LineGoal:
    """A main barbell lift progressing by weight from effort."""
    base = LineGoal(
        rule=Progression.RIR_LOAD,
        reps=reps,
        reps_left=left,
        main=True,
        small_muscle=False,
        equipment=Equipment.BARBELL,
        sets=1,
    )
    return replace(base, **overrides)


RANGE = effort(rule=Progression.DOUBLE_PROGRESSION, reps=TargetRange(8, 12), sets=3)
SIMPLE = effort(rule=Progression.SIMPLE, reps_left=None)


# --- AC-68: weight from effort ----------------------------------------------------------


def test_harder_by_two_reps_left_once_goes_lighter_by_the_arithmetic() -> None:
    # Target 8 with 2 left; 100 × 8 with 0 left → 100 × (1 + 8/30) / (1 + 10/30).
    result = suggest(effort(8, 2), [ex(1, s(100.0, 8, 0))])

    assert result.load_kg == 95.0
    assert result.outcome is SuggestionOutcome.DOWN
    assert result.explanation.rule == WEIGHT_FROM_EFFORT.id
    assert result.explanation.reason is SuggestionReason.HARDER_ONCE
    assert result.explanation.computed_kg == pytest.approx(95.0)
    assert result.explanation.limit is None


def test_four_plus_reps_left_rises_to_the_five_percent_cap() -> None:
    result = suggest(effort(6, 2), [ex(1, s(100.0, 6, 4, capped=True))])

    assert result.load_kg == 105.0
    assert result.outcome is SuggestionOutcome.UP
    assert result.explanation.reason is SuggestionReason.EASIER_CAPPED
    assert result.explanation.limit == PROGRESSION_MAX_INCREASE.id


def test_a_big_miss_is_capped_at_minus_ten_percent() -> None:
    result = suggest(effort(8, 2), [ex(1, s(100.0, 5, 0))])

    assert result.load_kg == 90.0
    assert result.explanation.computed_kg == pytest.approx(87.5)
    assert result.explanation.limit == PROGRESSION_MAX_DECREASE.id


def test_one_rep_easier_once_holds_the_weight() -> None:
    result = suggest(effort(6, 2), [ex(1, s(100.0, 6, 3))])

    assert result.load_kg == 100.0
    assert result.outcome is SuggestionOutcome.SAME
    assert result.explanation.reason is SuggestionReason.ON_TARGET


def test_one_rep_easier_twice_in_a_row_rises_by_the_arithmetic() -> None:
    result = suggest(effort(6, 2), [ex(1, s(100.0, 6, 3)), ex(2, s(100.0, 6, 3))])

    assert result.load_kg == 102.5
    assert round(result.explanation.computed_kg or 0, 1) == 102.6
    assert result.explanation.reason is SuggestionReason.EASIER_TWICE
    assert result.change_kg == 2.5
    # Both sessions are the inputs the Why sheet reads.
    assert [(i.load_kg, i.reps, i.reps_left) for i in result.explanation.inputs] == [
        (100.0, 6, 3),
        (100.0, 6, 3),
    ]
    assert result.explanation.target_reps == 6
    assert result.explanation.target_reps_left == 2


def test_one_rep_harder_twice_in_a_row_goes_lighter() -> None:
    result = suggest(effort(8, 2), [ex(1, s(100.0, 8, 1)), ex(2, s(100.0, 8, 1))])

    assert result.load_kg == 97.5
    assert result.explanation.reason is SuggestionReason.HARDER_TWICE


def test_a_streak_needs_the_same_weight_both_times() -> None:
    # The earlier session was at another weight: it says nothing about this one.
    result = suggest(effort(6, 2), [ex(1, s(97.5, 6, 3)), ex(2, s(100.0, 6, 3))])

    assert result.load_kg == 100.0


def test_a_small_muscle_exercise_rises_by_one_dumbbell_step_not_five_percent() -> None:
    curl = effort(
        10,
        2,
        main=False,
        small_muscle=True,
        equipment=Equipment.DUMBBELL,
    )

    result = suggest(curl, [ex(1, s(12.0, 10, 4, capped=True))])

    assert result.load_kg == 14.0
    assert result.explanation.limit == SMALL_MUSCLE_MAX_STEPS.id


def test_a_main_lift_whose_smallest_step_is_over_five_percent_adds_reps_first() -> None:
    press = effort(8, 2, equipment=Equipment.DUMBBELL)

    result = suggest(press, [ex(1, s(20.0, 8, 4, capped=True))])

    assert (result.load_kg, result.reps) == (20.0, 9)
    assert result.outcome is SuggestionOutcome.REPS
    assert result.explanation.limit == BIG_STEP.id


def test_a_range_of_reps_left_counts_only_what_falls_outside_it() -> None:
    goal = effort(8, 2, reps_left=TargetRange(1, 3))

    inside = suggest(goal, [ex(1, s(100.0, 8, 3))])
    below = suggest(goal, [ex(1, s(100.0, 8, 0)), ex(2, s(100.0, 8, 0))])

    assert inside.load_kg == 100.0
    assert below.outcome is SuggestionOutcome.DOWN


def test_a_set_rpe_is_read_as_reps_left() -> None:
    rated = PerformedSet(load_kg=100.0, reps=8, rpe=10.0)

    result = suggest(effort(8, 2), [ex(1, rated)])

    assert result.load_kg == 95.0


# --- AC-69: unrated never raises; reps first, then weight -------------------------------


def test_the_same_sets_unrated_do_not_rise() -> None:
    once = suggest(effort(6, 2), [ex(1, s(100.0, 6))])
    twice = suggest(effort(6, 2), [ex(1, s(100.0, 6)), ex(2, s(100.0, 6))])

    for result in (once, twice):
        assert result.load_kg == 100.0
        assert result.outcome is SuggestionOutcome.SAME
        assert result.explanation.reason is SuggestionReason.UNRATED


def test_an_unrated_session_breaks_an_easier_streak() -> None:
    result = suggest(effort(6, 2), [ex(1, s(100.0, 6)), ex(2, s(100.0, 6, 3))])

    assert result.load_kg == 100.0


def test_the_top_of_the_range_unrated_adds_a_step_and_aims_for_the_bottom() -> None:
    sets = [s(100.0, 12), s(100.0, 12), s(100.0, 12)]

    result = suggest(RANGE, [ex(1, *sets)])

    assert (result.load_kg, result.reps) == (102.5, 8)
    assert result.explanation.rule == REPS_THEN_WEIGHT.id
    assert result.explanation.reason is SuggestionReason.TOP_OF_RANGE


def test_the_top_of_the_range_rated_one_left_adds_a_step() -> None:
    sets = [s(100.0, 12, 1), s(100.0, 12, 1), s(100.0, 12, 2)]

    assert suggest(RANGE, [ex(1, *sets)]).load_kg == 102.5


def test_any_set_below_the_range_means_no_rise() -> None:
    sets = [s(100.0, 12), s(100.0, 12), s(100.0, 7)]

    result = suggest(RANGE, [ex(1, *sets)])

    assert (result.load_kg, result.reps) == (100.0, 8)
    assert result.explanation.reason is SuggestionReason.RANGE_MISSED


def test_inside_the_range_the_weight_holds_and_the_reps_climb() -> None:
    sets = [s(100.0, 10, 2), s(100.0, 9, 1), s(100.0, 9, 1)]

    result = suggest(RANGE, [ex(1, *sets)])

    assert (result.load_kg, result.reps) == (100.0, 10)
    assert result.explanation.reason is SuggestionReason.RANGE_BUILDING


def test_the_top_of_the_range_rated_zero_once_does_not_rise() -> None:
    sets = [s(100.0, 12, 0)] * 3

    result = suggest(RANGE, [ex(1, *sets)])

    assert result.load_kg == 100.0
    assert result.explanation.reason is SuggestionReason.TOP_AT_LIMIT


def test_the_top_of_the_range_rated_zero_twice_in_a_row_rises() -> None:
    sets = [s(100.0, 12, 0)] * 3

    result = suggest(RANGE, [ex(1, *sets), ex(2, *sets)])

    assert (result.load_kg, result.reps) == (102.5, 8)
    assert result.explanation.reason is SuggestionReason.TOP_OF_RANGE_TWICE


# --- AC-70: Simple progression ---------------------------------------------------------


def easy_good_hard(*answers: EffortSimple, load: float = 100.0):
    return [ex(n, s(load, 8, simple=a)) for n, a in enumerate(answers, start=1)]


E, G, H = EffortSimple.EASY, EffortSimple.GOOD, EffortSimple.HARD


def test_simple_easy_adds_one_step() -> None:
    result = suggest(SIMPLE, easy_good_hard(E))

    assert result.load_kg == 102.5
    assert result.explanation.rule == SIMPLE_PROGRESSION.id
    assert result.explanation.reason is SuggestionReason.SIMPLE_EASY


def test_simple_good_twice_adds_one_step() -> None:
    once = suggest(SIMPLE, easy_good_hard(G))
    twice = suggest(SIMPLE, easy_good_hard(G, G))

    assert once.load_kg == 100.0
    assert once.explanation.reason is SuggestionReason.SIMPLE_GOOD
    assert twice.load_kg == 102.5
    assert twice.explanation.reason is SuggestionReason.SIMPLE_GOOD_TWICE


def test_simple_hard_once_holds_and_twice_drops_ten_percent() -> None:
    once = suggest(SIMPLE, easy_good_hard(H))
    twice = suggest(SIMPLE, easy_good_hard(H, H))

    assert once.load_kg == 100.0
    assert once.explanation.reason is SuggestionReason.SIMPLE_HARD
    assert twice.load_kg == 90.0
    assert twice.explanation.reason is SuggestionReason.SIMPLE_HARD_TWICE


def test_simple_good_then_easy_counts_the_easy_and_restarts_the_good_streak() -> None:
    after_easy = suggest(SIMPLE, easy_good_hard(G, E))
    history = [*easy_good_hard(G, E), ex(3, s(102.5, 8, simple=G))]
    next_good = suggest(SIMPLE, history)

    assert after_easy.load_kg == 102.5
    assert after_easy.explanation.reason is SuggestionReason.SIMPLE_EASY
    # The Good before the Easy is not half of a streak with this one.
    assert next_good.load_kg == 102.5
    assert next_good.explanation.reason is SuggestionReason.SIMPLE_GOOD


# --- heavy set, then lighter sets --------------------------------------------------------


def test_lighter_sets_sit_seven_and_a_half_percent_below_the_logged_heavy_set() -> None:
    backoff = effort(
        5,
        2,
        rule=Progression.TOP_BACKOFF,
        set_kind=SetKind.BACKOFF,
        sets=3,
    )
    sets = [
        s(100.0, 3, 2, kind=LoggedSetKind.TOP),
        s(90.0, 5, 3, kind=LoggedSetKind.BACKOFF),
    ]

    result = suggest(backoff, [ex(1, *sets)])

    assert result.load_kg == 92.5
    assert result.explanation.rule == BACKOFF_DROP.id
    assert result.explanation.computed_kg == pytest.approx(92.5)


def test_the_heavy_set_follows_weight_from_effort_on_the_top_set() -> None:
    top = effort(3, 2, rule=Progression.TOP_BACKOFF, set_kind=SetKind.TOP)
    sets = [
        s(100.0, 3, 0, kind=LoggedSetKind.TOP),
        s(92.5, 5, 4, capped=True, kind=LoggedSetKind.BACKOFF),
    ]

    result = suggest(top, [ex(1, *sets)])

    assert result.explanation.rule == WEIGHT_FROM_EFFORT.id
    assert result.outcome is SuggestionOutcome.DOWN


# --- what is never learned from ----------------------------------------------------------


def test_a_dont_learn_session_is_skipped() -> None:
    history = [
        ex(1, s(100.0, 6, 2)),
        ex(2, s(100.0, 6, 4, capped=True), dont_learn=True),
    ]

    result = suggest(effort(6, 2), history)

    assert result.load_kg == 100.0
    assert [i.session_id for i in result.explanation.inputs] == [uuid.UUID(int=1)]


def test_a_trimmed_session_never_raises() -> None:
    result = suggest(effort(6, 2), [ex(1, s(100.0, 6, 4, capped=True), cut=True)])

    assert result.load_kg == 100.0
    assert result.explanation.reason is SuggestionReason.TRIMMED


def test_a_trimmed_session_may_still_lower_the_weight() -> None:
    result = suggest(effort(8, 2), [ex(1, s(100.0, 5, 0), cut=True)])

    assert result.load_kg == 90.0


def test_a_trimmed_earlier_session_cannot_complete_an_easier_streak() -> None:
    history = [ex(1, s(100.0, 6, 3), cut=True), ex(2, s(100.0, 6, 3))]

    assert suggest(effort(6, 2), history).load_kg == 100.0


def test_no_history_is_a_first_time_with_no_weight() -> None:
    result = suggest(effort(), [])

    assert result.outcome is SuggestionOutcome.FIRST_TIME
    assert result.load_kg is None
    assert result.explanation.rule is None
    assert result.explanation.reason is SuggestionReason.FIRST_TIME


def test_only_dont_learn_history_is_a_first_time() -> None:
    result = suggest(effort(), [ex(1, s(100.0, 8, 2), dont_learn=True)])

    assert result.outcome is SuggestionOutcome.FIRST_TIME


def test_a_line_with_no_rule_holds_the_last_weight() -> None:
    result = suggest(effort(rule=None), [ex(1, s(61.0, 5, 2))])

    assert result.load_kg == 60.0  # always loadable
    assert result.explanation.rule == PROGRESSION_HOLD.id


def test_warm_ups_are_never_read() -> None:
    sets = [s(60.0, 5, 4, capped=True, kind=LoggedSetKind.WARMUP), s(100.0, 8, 2)]

    assert suggest(effort(8, 2), [ex(1, *sets)]).load_kg == 100.0


# --- plateau: holding steady -------------------------------------------------------------


def flat(count: int) -> list:
    """``count`` sessions of 100 × 8 with 2 left: on target, no new best."""
    return [ex(n, s(100.0, 8, 2)) for n in range(1, count + 1)]


def test_four_sessions_without_a_best_take_one_small_step() -> None:
    # The first session is the baseline; four more without a best follow it.
    result = suggest(effort(8, 2), flat(5), phase=PlanPhase.BASE)

    assert result.load_kg == 102.5
    assert result.explanation.reason is SuggestionReason.PLATEAU_STEP


def test_the_next_rung_adds_a_rep_and_the_third_resets() -> None:
    reps = suggest(effort(8, 2), flat(9), phase=PlanPhase.BASE)
    reset = suggest(effort(8, 2), flat(13), phase=PlanPhase.BASE)

    assert (reps.load_kg, reps.reps) == (100.0, 9)
    assert reps.explanation.reason is SuggestionReason.PLATEAU_REPS
    assert reset.load_kg == 92.5
    assert reset.explanation.rule == PLATEAU_RESET.id


def test_between_rungs_the_weight_holds() -> None:
    result = suggest(effort(8, 2), flat(6), phase=PlanPhase.BASE)

    assert result.load_kg == 100.0
    assert result.explanation.reason is SuggestionReason.ON_TARGET


@pytest.mark.parametrize(
    "phase", [PlanPhase.PEAK, PlanPhase.TAPER, PlanPhase.MAINTENANCE]
)
def test_no_plateau_response_in_peak_race_week_or_maintenance(phase: PlanPhase) -> None:
    result = suggest(effort(8, 2), flat(5), phase=phase)

    assert result.load_kg == 100.0
    assert result.explanation.reason is SuggestionReason.ON_TARGET


def test_a_single_joint_exercise_waits_six_sessions() -> None:
    curl = effort(8, 2, main=False, small_muscle=True, equipment=Equipment.DUMBBELL)
    history = [ex(n, s(12.0, 8, 2)) for n in range(1, 6)]
    longer = [ex(n, s(12.0, 8, 2)) for n in range(1, 8)]

    assert suggest(curl, history, phase=PlanPhase.BASE).load_kg == 12.0
    assert suggest(curl, longer, phase=PlanPhase.BASE).load_kg == 14.0


def test_a_new_best_restarts_the_count() -> None:
    history = [*flat(4), ex(5, s(100.0, 9, 1))]

    result = suggest(
        effort(8, 2), [*history, ex(6, s(100.0, 8, 2))], phase=PlanPhase.BASE
    )

    assert result.explanation.reason is SuggestionReason.ON_TARGET


# --- choosing the rule, and what counts as trimmed --------------------------------------


def line(**overrides: Any) -> StrengthSet:
    base = StrengthSet(
        exercise_id="back_squat", sets=3, reps=8, load=Load(LoadKind.KG, 100.0)
    )
    return replace(base, **overrides)


@pytest.mark.parametrize(
    ("overrides", "mode", "expected"),
    [
        (
            {"progression": Progression.SIMPLE, "rir": 2},
            TrainingMode.STANDARD,
            Progression.SIMPLE,
        ),
        ({"rir": 2}, TrainingMode.SIMPLE, Progression.SIMPLE),
        (
            {"set_kind": SetKind.TOP, "rir": 2},
            TrainingMode.STANDARD,
            Progression.TOP_BACKOFF,
        ),
        ({"set_kind": SetKind.BACKOFF}, TrainingMode.STANDARD, Progression.TOP_BACKOFF),
        (
            {"reps": TargetRange(8, 12)},
            TrainingMode.STANDARD,
            Progression.DOUBLE_PROGRESSION,
        ),
        ({"rir": 2}, TrainingMode.FULL, Progression.RIR_LOAD),
        ({}, TrainingMode.STANDARD, None),
    ],
)
def test_the_rule_comes_from_the_line_then_the_mode(
    overrides: dict[str, Any], mode: TrainingMode, expected: Progression | None
) -> None:
    assert resolve_rule(line(**overrides), mode) is expected


def test_fewer_working_sets_than_prescribed_is_trimmed() -> None:
    logged = [s(60.0, 5, kind=LoggedSetKind.WARMUP), s(100.0, 8), s(100.0, 8)]
    paired = [s(20.0, 8, side=SetSide.LEFT), s(20.0, 8, side=SetSide.RIGHT)]

    assert trimmed(logged, prescribed_sets=3) is True
    assert trimmed(logged, prescribed_sets=2) is False
    assert trimmed(paired, prescribed_sets=1) is False


def test_the_plateau_off_phases_are_plan_phases() -> None:
    from app.domain.rules import PLATEAU_OFF_PHASES

    assert {PlanPhase(p) for p in PLATEAU_OFF_PHASES.value} == {
        PlanPhase.PEAK,
        PlanPhase.TAPER,
        PlanPhase.MAINTENANCE,
    }


def test_a_blocked_increase_never_raises() -> None:
    # Sharp pain in the latest session (`app.domain.safety.blocks_increase`).
    goal = effort(6, 2, increase_blocked=True)

    result = suggest(goal, [ex(1, s(100.0, 6, 4, capped=True))])

    assert result.load_kg == 100.0
    assert result.explanation.reason is SuggestionReason.INCREASE_BLOCKED


def test_a_blocked_increase_may_still_lower() -> None:
    goal = effort(8, 2, increase_blocked=True)

    assert suggest(goal, [ex(1, s(100.0, 5, 0))]).load_kg == 90.0


def test_a_range_target_explains_the_end_it_held_the_report_to() -> None:
    goal = effort(8, 2, reps_left=TargetRange(1, 3))

    result = suggest(goal, [ex(1, s(100.0, 8, 0)), ex(2, s(100.0, 8, 0))])

    # 100 × (1 + 8/30) / (1 + (8 + 1)/30): held to the range's low end, 1.
    assert result.explanation.target_reps_left == 1
    assert result.explanation.computed_kg == pytest.approx(100 * (38 / 30) / (39 / 30))
