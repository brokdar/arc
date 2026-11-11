"""Properties of suggested weights over random histories (AC-71).

The examples in `test_domain_progression.py` pin each rule's arithmetic;
these pin what must hold for *every* history: no automatic step leaves the
limits, every suggestion can be loaded, a cautious rater is not walked down,
peak / race-week / maintenance phases never see a plateau response, and a
don't-learn or trimmed session never causes a rise.
"""

import datetime as dt
import math
import uuid
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.activity import EffortSimple
from app.domain.loading import Equipment, loadable_weights, nearest_loadable
from app.domain.metrics import PerformedSet
from app.domain.plans import PlanPhase
from app.domain.progression import (
    LineGoal,
    Suggestion,
    SuggestionOutcome,
    SuggestionReason,
    suggest,
)
from app.domain.rules import (
    PLATEAU_OFF_PHASES,
    PROGRESSION_MAX_DECREASE,
    PROGRESSION_MAX_INCREASE,
)
from app.domain.strength import Progression, TargetRange
from app.domain.strength_records import EPLEY_DIVISOR, Exposure

PLATEAU = {
    SuggestionReason.PLATEAU_STEP,
    SuggestionReason.PLATEAU_REPS,
    SuggestionReason.PLATEAU_RESET,
}

#: The equipment progression rounds for, with a realistic working range each.
EQUIPMENT = {
    Equipment.BARBELL: [w for w in loadable_weights(Equipment.BARBELL) if w <= 200],
    Equipment.DUMBBELL: list(loadable_weights(Equipment.DUMBBELL)),
    Equipment.STACK: list(loadable_weights(Equipment.STACK)),
}


def g(kg: float) -> int:
    return round(kg * 1000)


@st.composite
def goals(draw: st.DrawFn) -> LineGoal:
    rule = draw(
        st.sampled_from(
            [
                Progression.RIR_LOAD,
                Progression.DOUBLE_PROGRESSION,
                Progression.SIMPLE,
                Progression.TOP_BACKOFF,
                None,
            ]
        )
    )
    reps = (
        TargetRange(draw(st.integers(3, 8)), draw(st.integers(9, 15)))
        if rule is Progression.DOUBLE_PROGRESSION
        else draw(st.integers(1, 15))
    )
    small = draw(st.booleans())
    return LineGoal(
        rule=rule,
        reps=reps,
        reps_left=draw(st.one_of(st.none(), st.integers(0, 4))),
        main=draw(st.booleans()) and not small,
        small_muscle=small,
        equipment=draw(st.sampled_from(list(EQUIPMENT))),
        sets=draw(st.integers(1, 5)),
    )


@st.composite
def performed(draw: st.DrawFn, equipment: Equipment, load: float) -> PerformedSet:
    rated = draw(st.sampled_from(["rir", "capped", "simple", "none"]))
    return PerformedSet(
        load_kg=load,
        reps=draw(st.integers(1, 20)),
        rir=draw(st.integers(0, 4)) if rated in {"rir", "capped"} else None,
        rir_capped=rated == "capped",
        effort_simple=(
            draw(st.sampled_from(list(EffortSimple))) if rated == "simple" else None
        ),
    )


@st.composite
def histories(draw: st.DrawFn, equipment: Equipment) -> list[Exposure]:
    weights = EQUIPMENT[equipment]
    out: list[Exposure] = []
    for n in range(draw(st.integers(1, 10))):
        load = draw(st.sampled_from(weights))
        sets = draw(st.lists(performed(equipment, load), min_size=1, max_size=4))
        out.append(
            Exposure(
                session_id=uuid.UUID(int=n + 1),
                date=dt.date(2026, 9, 1) + dt.timedelta(days=3 * n),
                sets=sets,
                dont_learn=draw(st.booleans()) and draw(st.booleans()),
                trimmed=draw(st.booleans()) and draw(st.booleans()),
            )
        )
    return out


@st.composite
def cases(draw: st.DrawFn) -> tuple[LineGoal, list[Exposure], PlanPhase | None]:
    goal = draw(goals())
    assert goal.equipment is not None
    history = draw(histories(goal.equipment))
    phase = draw(st.one_of(st.none(), st.sampled_from(list(PlanPhase))))
    return goal, history, phase


def last_load(history: list[Exposure]) -> float | None:
    learn = [e for e in history if not e.dont_learn]
    if not learn:
        return None
    return max(s.load_kg or 0 for s in learn[-1].sets)


# --- limits and loadability -----------------------------------------------------------------


@settings(max_examples=400, deadline=None)
@given(cases())
def test_no_automatic_step_leaves_the_limits(
    case: tuple[LineGoal, list[Exposure], PlanPhase | None],
) -> None:
    goal, history, phase = case
    result = suggest(goal, history, phase=phase)
    load = last_load(history)
    if load is None or result.load_kg is None:
        assert result.outcome is SuggestionOutcome.FIRST_TIME
        return
    assert goal.equipment is not None
    weights = loadable_weights(goal.equipment)
    heavier = [w for w in weights if g(w) > g(nearest_loadable(load, goal.equipment))]
    one_step = heavier[0] if heavier else load
    if goal.small_muscle:
        ceiling = one_step
    elif goal.main:
        ceiling = load * (1 + PROGRESSION_MAX_INCREASE.value)
    else:
        ceiling = max(load * (1 + PROGRESSION_MAX_INCREASE.value), one_step)
    floor = load * (1 - PROGRESSION_MAX_DECREASE.value)

    assert g(result.load_kg) <= g(ceiling), (result, load)
    assert g(result.load_kg) >= g(floor), (result, load)


@settings(max_examples=400, deadline=None)
@given(cases())
def test_every_suggestion_is_loadable(
    case: tuple[LineGoal, list[Exposure], PlanPhase | None],
) -> None:
    goal, history, phase = case
    result = suggest(goal, history, phase=phase)
    assert goal.equipment is not None

    if result.load_kg is not None:
        assert result.load_kg in loadable_weights(goal.equipment)


# --- the cautious rater ----------------------------------------------------------------------


def session(n: int, load: float, true_max: float, reps: int, bias: int) -> Exposure:
    """One session at ``load`` by an athlete whose real max never changes.

    Reps to failure follow Epley from ``true_max``; the athlete does the
    target reps when they can, and reports reps left ``bias`` lower than true.
    """
    to_failure = math.floor(EPLEY_DIVISOR * (true_max / load - 1))
    done = max(1, min(reps, to_failure))
    left = max(to_failure - done, 0)
    return Exposure(
        session_id=uuid.UUID(int=n + 1),
        date=dt.date(2026, 9, 1) + dt.timedelta(days=3 * n),
        sets=[PerformedSet(load_kg=load, reps=done, rir=max(left - bias, 0))],
    )


def twelve_weeks(
    goal: LineGoal, true_max: float, start: float, bias: int
) -> list[float]:
    """The weight before each of 24 sessions (two a week for 12 weeks) and after.

    Plateau responses are off (a maintenance phase): with a real max that
    never moves, the plateau ladder deliberately tries a step, a rep and a
    reset for *every* rater, and that would bury the one thing this measures
    — what a biased effort report does to the effort thresholds.
    """
    history: list[Exposure] = []
    loads = [start]
    for n in range(24):
        history.append(session(n, loads[-1], true_max, 8, bias))
        result: Suggestion = suggest(goal, history, phase=PlanPhase.MAINTENANCE)
        assert result.load_kg is not None
        loads.append(result.load_kg)
    return loads


@settings(max_examples=200, deadline=None)
@given(
    st.sampled_from([w for w in loadable_weights(Equipment.BARBELL) if 40 <= w <= 200]),
    st.floats(0.01, 0.99),
)
def test_a_rater_one_rep_cautious_loses_at_most_one_step_in_twelve_weeks(
    start: float, slack: float
) -> None:
    goal = LineGoal(
        rule=Progression.RIR_LOAD,
        reps=8,
        reps_left=2,
        main=True,
        small_muscle=False,
        equipment=Equipment.BARBELL,
    )
    # A real max at which ``start`` × 8 leaves exactly 2 reps: constant real
    # performance, on target at the starting weight.
    true_max = start * (1 + (10 + slack) / EPLEY_DIVISOR)
    weights = loadable_weights(Equipment.BARBELL)
    # "One step" is one rep's worth of load, the unit the rater is wrong by:
    # the cautious rater's report reaches the target only where the athlete
    # really has one rep more left (8 reps with 3 left), and since only whole
    # reps are reported, the loadable weight that reaches it may sit up to
    # one plate pair below. So the bound is the heaviest loadable weight at
    # or below ``true_max / (1 + (8 + 3) / 30)`` — at 150 kg that is 145 kg,
    # more than one 2.5 kg pair, because one rep there is worth 3.75 kg.
    # The rule's own one-rep adjustment from the start (what it does after a
    # biased report twice, rounded, a tie going down) can land lower still;
    # one step is whichever of the two one-rep moves is lower.
    one_rep_lighter = true_max / (1 + 11 / EPLEY_DIVISOR)
    one_step_down = min(
        max(w for w in weights if w <= one_rep_lighter),
        nearest_loadable(start * (1 + 9 / EPLEY_DIVISOR) / (1 + 10 / EPLEY_DIVISOR)),
    )

    honest = twelve_weeks(goal, true_max, start, bias=0)
    cautious = twelve_weeks(goal, true_max, start, bias=1)

    assert set(honest) == {start}, honest
    # Never more than one adjustment below the truth, at any session: the
    # cautious rater may sit there or swing back (one step lighter, the same
    # biased report reads as easier), but is never walked further down.
    assert min(cautious) >= one_step_down, cautious


# --- phases, don't-learn, trimmed ----------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(cases(), st.sampled_from(PLATEAU_OFF_PHASES.value))
def test_no_plateau_response_fires_in_peak_race_week_or_maintenance(
    case: tuple[LineGoal, list[Exposure], PlanPhase | None], phase: str
) -> None:
    goal, history, _ = case

    result = suggest(goal, history, phase=PlanPhase(phase))

    assert result.explanation.reason not in PLATEAU


def test_the_same_history_in_base_does_fire_so_the_property_is_not_vacuous() -> None:
    goal = LineGoal(
        rule=Progression.RIR_LOAD,
        reps=8,
        reps_left=2,
        main=True,
        small_muscle=False,
        equipment=Equipment.BARBELL,
    )
    history = [session(n, 100.0, 100.0 * (1 + 10.5 / 30), 8, 0) for n in range(5)]

    assert suggest(goal, history, phase=PlanPhase.BASE).explanation.reason in PLATEAU
    assert (
        suggest(goal, history, phase=PlanPhase.PEAK).explanation.reason not in PLATEAU
    )


@settings(max_examples=300, deadline=None)
@given(cases(), st.data())
def test_a_dont_learn_session_never_changes_the_suggestion(
    case: tuple[LineGoal, list[Exposure], PlanPhase | None], data: st.DataObject
) -> None:
    goal, history, phase = case
    assert goal.equipment is not None
    extra = data.draw(histories(goal.equipment))[0]
    at = data.draw(st.integers(0, len(history)))
    marked = [*history[:at], replace(extra, dont_learn=True), *history[at:]]

    with_it = suggest(goal, marked, phase=phase)
    without = suggest(goal, history, phase=phase)

    assert (with_it.load_kg, with_it.reps, with_it.explanation.reason) == (
        without.load_kg,
        without.reps,
        without.explanation.reason,
    )


@settings(max_examples=300, deadline=None)
@given(cases())
def test_a_trimmed_last_session_never_raises(
    case: tuple[LineGoal, list[Exposure], PlanPhase | None],
) -> None:
    goal, history, phase = case
    learn = [e for e in history if not e.dont_learn]
    if not learn:
        return
    marked = [*learn[:-1], replace(learn[-1], trimmed=True)]
    assert goal.equipment is not None
    current = nearest_loadable(last_load(marked) or 0, goal.equipment)

    result = suggest(goal, marked, phase=phase)

    if goal.set_kind is None and result.load_kg is not None:
        assert g(result.load_kg) <= g(current)
        assert result.outcome is not SuggestionOutcome.REPS
