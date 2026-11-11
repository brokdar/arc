"""Suggested weights: next session's numbers for one line, from what was lifted.

After every session arc works out each line's next weight from the sets the
athlete actually logged — never from the plan's target — and says why in a
form the gym screen can show in three layers: a reason chip, three plain
lines, and the arithmetic. :func:`suggest` is the whole rule; the packet and
the coach's ``get_next_targets`` both call it through the packet service, so
the phone and the coach read one computation.

**The rules** (the line's ``progression`` picks one; :func:`resolve_rule` says
what an unset one means):

* *Weight from effort* (``rir_load``). The gap is how far reps plus reps left
  landed from the target's reps plus reps left. Two or more away once, or one
  away in two sessions in a row at the same weight, moves the weight by
  Epley's arithmetic — ``kg × (1 + (reps + left) / 30) / (1 + (target reps +
  target left) / 30)`` — and anything less holds it. "4+" is at least four
  left, so an easier 4+ goes straight to the rise cap.
* *Reps first, then weight* (``double_progression``): every working set at the
  top of the range rated 1+ left, unrated, or rated 0 in two sessions in a row
  adds the smallest step and aims for the bottom; any set below the range
  means no rise.
* *Heavy set, then lighter sets* (``top_backoff``): the heavy set follows
  weight from effort on the logged top set; the lighter sets sit 7.5 % below
  the logged heavy set.
* *Simple* (Simple mode, or ``simple``): Easy adds one step; Good twice in a
  row adds one; Hard holds; Hard twice in a row drops 10 %.

**Symmetric thresholds, so cautious raters do not drift down.** "Harder" and
"easier" use the same gaps — two once, one twice — in both directions. This
displaces asymmetric damping (quicker to drop than to raise), the usual
"safety" instinct: athletes under-report reps left far more often than they
over-report, and with a quicker drop a rater one rep cautious would be walked
down session after session at a weight they lift perfectly well. With the
same threshold both ways that rater loses one step and then reads as on
target. Pinned by the simulated-rater property in
``tests/unit/test_domain_progression_properties.py`` (AC-71).

**Unrated counts as at target and never raises from effort.** A set logged
without a rating says nothing about effort, so weight from effort holds the
weight — it is neither a reason to go up nor, as treating "unrated" as
"hard" would make it, a reason to go down. That displaces reading unrated as
hard, which would punish an athlete for skipping a question. The one rule
that may raise unrated sets is reps-then-weight, where hitting the top of the
range is itself the evidence. Pinned by AC-69 in
``tests/unit/test_domain_progression.py``.

**Limits** (``app.domain.rules``): a main lift rises at most 5 %, a
small-muscle (single-joint) exercise at most one loadable step, any other at
most the larger of the two; nothing drops more than 10 %; a main lift whose
smallest step is more than 5 % adds a rep instead. Results are rounded to the
nearest loadable weight (`app.domain.loading.nearest_loadable`) and then held
inside the limits, so rounding can never undo a cap.

**What is never learned from.** A don't-learn session is skipped entirely.
Sharp pain in the latest session (`app.domain.safety.blocks_increase`) blocks
any rise, as it already blocks one on the gym screen. A trimmed session (fewer working sets than prescribed) never raises the weight,
and cannot be half of an "easier twice" streak, since effort after fewer sets
reads easier than it was; it may still lower it.

**Plateaus.** When a rule would hold the weight on an on-target session and
four exposures (six for single-joint) have passed without a personal best or
a rise in the estimate's median, the next rung of the ladder is offered — one
smallest step, then one more rep, then a 7.5 % reset — one rung per window,
and never in a peak, race-week (taper) or maintenance phase.

Plain logbook and follow-exactly bypass all of this; that is the packet
service's decision, made before this module is called.

Every number here comes from `app.domain.rules`; ``tests/unit/test_domain_rules.py``
fails the build on a bare training literal.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.activity import EffortSimple, LoggedSetKind
from app.domain.loading import (
    DEFAULT_GYM,
    GRAMS_PER_KG,
    Equipment,
    Gym,
    loadable_weights,
    nearest_loadable,
)
from app.domain.metrics import PerformedSet
from app.domain.plans import PlanPhase
from app.domain.rules import (
    BACKOFF_DROP,
    BIG_STEP,
    EFFORT_GAP_TWICE,
    PLATEAU_OFF_PHASES,
    PLATEAU_RESET,
    PLATEAU_WINDOW_LONG,
    PLATEAU_WINDOW_SHORT,
    PROGRESSION_HOLD,
    PROGRESSION_MAX_DECREASE,
    PROGRESSION_MAX_INCREASE,
    REGISTRY,
    REPS_THEN_WEIGHT,
    SIMPLE_PROGRESSION,
    SMALL_MUSCLE_MAX_STEPS,
    STREAK_SESSIONS,
    WEIGHT_FROM_EFFORT,
    Evidence,
)
from app.domain.strength import (
    Count,
    Exercise,
    Mechanic,
    Progression,
    SetKind,
    StrengthSet,
    TargetRange,
)
from app.domain.strength_records import (
    EPLEY_DIVISOR,
    RPE_AT_FAILURE,
    Exposure,
    estimate_line,
    record_sets,
)
from app.domain.training_mode import TrainingMode


class SuggestionOutcome(StrEnum):
    """What the suggestion does to the last weight."""

    UP = "up"
    DOWN = "down"
    SAME = "same"
    #: Same weight, one more rep (a big step, or the plateau's second rung).
    REPS = "reps"
    #: Nothing to learn from yet; the starting flow or the plan decides.
    FIRST_TIME = "first_time"


class SuggestionReason(StrEnum):
    """Why, as a code the phone turns into the chip and the Why sheet."""

    HARDER_ONCE = "harder_once"
    HARDER_TWICE = "harder_twice"
    EASIER_ONCE = "easier_once"
    EASIER_TWICE = "easier_twice"
    EASIER_CAPPED = "easier_capped"
    ON_TARGET = "on_target"
    UNRATED = "unrated"
    TOP_OF_RANGE = "top_of_range"
    TOP_OF_RANGE_TWICE = "top_of_range_twice"
    TOP_AT_LIMIT = "top_at_limit"
    RANGE_BUILDING = "range_building"
    RANGE_MISSED = "range_missed"
    SIMPLE_EASY = "simple_easy"
    SIMPLE_GOOD = "simple_good"
    SIMPLE_GOOD_TWICE = "simple_good_twice"
    SIMPLE_HARD = "simple_hard"
    SIMPLE_HARD_TWICE = "simple_hard_twice"
    BACKOFF = "backoff"
    BIG_STEP = "big_step"
    TRIMMED = "trimmed"
    INCREASE_BLOCKED = "increase_blocked"
    PLATEAU_STEP = "plateau_step"
    PLATEAU_REPS = "plateau_reps"
    PLATEAU_RESET = "plateau_reset"
    NO_RULE = "no_rule"
    FIRST_TIME = "first_time"


@dataclass(frozen=True, slots=True)
class LineGoal:
    """What one prescription line asks for, and how its exercise progresses.

    Attributes:
        rule: The resolved rule (:func:`resolve_rule`); ``None`` holds.
        reps: The prescribed reps — a number or a range.
        reps_left: The prescribed reps in reserve, if the line has one.
        main: The line is a main lift (`app.domain.strength.resolve_main_lines`).
        small_muscle: A single-joint exercise (:func:`is_small_muscle`).
        equipment: How the weight is made up; ``None`` cannot be rounded.
        sets: Working sets the line prescribes, for :func:`trimmed`.
        set_kind: ``top`` / ``backoff`` decide the heavy-then-lighter split.
        increase_blocked: The latest session had sharp pain
            (`app.domain.safety.blocks_increase`): nothing may rise.
    """

    rule: Progression | None
    reps: Count | None
    reps_left: Count | None
    main: bool
    small_muscle: bool
    equipment: Equipment | None
    sets: int = 1
    set_kind: SetKind | None = None
    increase_blocked: bool = False


@dataclass(frozen=True, slots=True)
class ExposureInput:
    """One session the suggestion read, as its reference set."""

    session_id: uuid.UUID
    date: dt.date
    load_kg: float | None
    reps: int | None
    reps_left: float | None
    reps_left_capped: bool
    effort_simple: EffortSimple | None
    trimmed: bool


@dataclass(frozen=True, slots=True)
class Explanation:
    """Everything the Why sheet and its Details show, and the coach reads.

    Attributes:
        rule: The registry id of the rule that set the value; ``None`` only
            for a first time, when no rule ran.
        evidence: The rule's evidence tag, for the Details line.
        reason: The plain-language reason code.
        inputs: The sessions read, oldest first (the last one or two).
        target_reps: The prescribed reps (a range's low end).
        target_reps_left: The reps left the effort was held to: the
            prescription's, or for a range the end the report fell beyond.
        computed_kg: The rule's result before limits and rounding.
        limit: The registry id of the limit that held the result back.
    """

    rule: str | None
    evidence: Evidence | None
    reason: SuggestionReason
    inputs: tuple[ExposureInput, ...]
    target_reps: int | None
    target_reps_left: float | None
    computed_kg: float | None
    limit: str | None


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Next session's numbers for one line, and why."""

    outcome: SuggestionOutcome
    load_kg: float | None
    reps: int | None
    #: ``load_kg`` minus the last weight (rounded to loadable); ``None``
    #: without both.
    change_kg: float | None
    explanation: Explanation


# --- choosing a rule --------------------------------------------------------------------


def is_small_muscle(exercise: Exercise | None) -> bool:
    """Whether an exercise progresses in single steps: a single-joint movement.

    Single-joint is the library's own word for the exercises one dumbbell
    step already moves by more than 5 % — curls, raises, extensions — and the
    direction counts their plateaus over six exposures for the same reason.
    An exercise the library does not know is not assumed small.
    """
    return exercise is not None and exercise.mechanic is Mechanic.SINGLE_JOINT


def resolve_rule(line: StrengthSet, mode: TrainingMode) -> Progression | None:
    """The rule a line progresses by.

    Simple mode always uses Simple progression: its athlete answers Easy /
    Good / Hard and no other rule has anything to read. Otherwise an explicit
    ``progression`` wins; then a ``top``/``backoff`` line is heavy-then-
    lighter, a rep range is reps-then-weight, a reps-left target is weight
    from effort, and a line with none of these has no rule and holds.
    """
    if mode is TrainingMode.SIMPLE:
        return Progression.SIMPLE
    if line.progression is not None:
        return line.progression
    if line.set_kind in (SetKind.TOP, SetKind.BACKOFF):
        return Progression.TOP_BACKOFF
    if isinstance(line.reps, TargetRange):
        return Progression.DOUBLE_PROGRESSION
    if line.rir is not None:
        return Progression.RIR_LOAD
    return None


def trimmed(sets: Sequence[PerformedSet], *, prescribed_sets: int) -> bool:
    """Whether fewer working sets were logged than ``prescribed_sets``.

    Counted as records count them (`record_sets`): warm-ups and holds out,
    a left-right pair one set.
    """
    return len(record_sets(sets)) < prescribed_sets


# --- arithmetic helpers -----------------------------------------------------------------


def _g(kg: float) -> int:
    """Whole grams, so float noise cannot move a weight across a limit."""
    return round(kg * GRAMS_PER_KG)


def _low(count: Count | None) -> int | None:
    return count.low if isinstance(count, TargetRange) else count


def _bounds(count: Count) -> tuple[int, int]:
    if isinstance(count, TargetRange):
        return count.low, count.high
    return count, count


def _left(entry: PerformedSet) -> tuple[float | None, bool]:
    """Reps left as reported, and whether it was "4+" (a floor)."""
    if entry.rir is not None:
        return float(entry.rir), entry.rir_capped
    if entry.rpe is not None:
        return RPE_AT_FAILURE - entry.rpe, False
    return None, False


def _sets(exposure: Exposure) -> list[PerformedSet]:
    return [x for x in record_sets(exposure.sets) if x.reps is not None]


def _reference(exposure: Exposure, goal: LineGoal) -> PerformedSet | None:
    """The set a session is judged by: its heaviest, last of equals.

    A heavy-then-lighter line reads the logged top set; any other line
    ignores lighter backoff sets, which say nothing about its own weight.
    """
    loaded = [x for x in _sets(exposure) if x.load_kg is not None]
    if goal.rule is Progression.TOP_BACKOFF:
        tops = [x for x in loaded if x.kind is LoggedSetKind.TOP]
        loaded = tops or loaded
    else:
        working = [x for x in loaded if x.kind is not LoggedSetKind.BACKOFF]
        loaded = working or loaded
    if not loaded:
        return None
    heaviest = max(_g(x.load_kg or 0) for x in loaded)
    return [x for x in loaded if _g(x.load_kg or 0) == heaviest][-1]


def _answer(exposure: Exposure) -> EffortSimple | None:
    answers = [x.effort_simple for x in _sets(exposure) if x.effort_simple]
    return answers[-1] if answers else None


def _input(exposure: Exposure, goal: LineGoal) -> ExposureInput:
    ref = _reference(exposure, goal)
    left, capped = (None, False) if ref is None else _left(ref)
    return ExposureInput(
        session_id=exposure.session_id,
        date=exposure.date,
        load_kg=None if ref is None else ref.load_kg,
        reps=None if ref is None else ref.reps,
        reps_left=left,
        reps_left_capped=capped,
        effort_simple=_answer(exposure),
        trimmed=exposure.trimmed,
    )


def _step_above(kg: float, equipment: Equipment, gym: Gym) -> float:
    current = nearest_loadable(kg, equipment, gym)
    heavier = [w for w in loadable_weights(equipment, gym) if _g(w) > _g(current)]
    return heavier[0] if heavier else current


@dataclass(frozen=True, slots=True)
class _Limits:
    low: float
    high: float
    high_rule: str


def _limits(load: float, goal: LineGoal, equipment: Equipment, gym: Gym) -> _Limits:
    """The automatic-progression window around the last weight (module doc)."""
    low = load * (1 - PROGRESSION_MAX_DECREASE.value)
    capped = load * (1 + PROGRESSION_MAX_INCREASE.value)
    step = _step_above(load, equipment, gym)
    if goal.small_muscle:
        return _Limits(low, step, SMALL_MUSCLE_MAX_STEPS.id)
    if goal.main:
        return _Limits(low, capped, PROGRESSION_MAX_INCREASE.id)
    if _g(step) > _g(capped):
        return _Limits(low, step, SMALL_MUSCLE_MAX_STEPS.id)
    return _Limits(low, capped, PROGRESSION_MAX_INCREASE.id)


def _land(
    target: float, load: float, goal: LineGoal, equipment: Equipment, gym: Gym
) -> tuple[float, str | None]:
    """``target`` held inside the limits and made loadable, and the limit hit."""
    limits = _limits(load, goal, equipment, gym)
    limit: str | None = None
    if _g(target) > _g(limits.high):
        target, limit = limits.high, limits.high_rule
    elif _g(target) < _g(limits.low):
        target, limit = limits.low, PROGRESSION_MAX_DECREASE.id
    kg = nearest_loadable(target, equipment, gym)
    weights = loadable_weights(equipment, gym)
    if _g(kg) > _g(limits.high):
        below = [w for w in weights if _g(w) <= _g(limits.high)]
        kg = below[-1] if below else kg
    if _g(kg) < _g(limits.low):
        above = [w for w in weights if _g(w) >= _g(limits.low)]
        kg = above[0] if above else kg
    return kg, limit


def _big_step(load: float, goal: LineGoal, equipment: Equipment, gym: Gym) -> bool:
    """A main lift whose smallest step is more than 5 % of the weight."""
    if not goal.main or goal.small_muscle:
        return False
    current = nearest_loadable(load, equipment, gym)
    step = _step_above(load, equipment, gym) - current
    return _g(step) > _g(load * BIG_STEP.value)


# --- the decision -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Decision:
    """A rule's verdict before trimming and plateaus are applied."""

    rule: str
    reason: SuggestionReason
    target_kg: float  # before limits and rounding
    reps: int | None
    raise_: bool = False
    forced_limit: bool = False
    computed_kg: float | None = None
    #: The reps-left target the arithmetic used (a range clamped to the report).
    target_left: float | None = None


def _prev_same_weight(
    learn: Sequence[Exposure], goal: LineGoal, ref: PerformedSet
) -> Exposure | None:
    """The session before the last, when it was at the same weight."""
    if len(learn) < STREAK_SESSIONS.value:
        return None
    prev = learn[-STREAK_SESSIONS.value]
    prev_ref = _reference(prev, goal)
    if prev_ref is None or prev_ref.load_kg is None or ref.load_kg is None:
        return None
    return prev if _g(prev_ref.load_kg) == _g(ref.load_kg) else None


def _gap(ref: PerformedSet, goal: LineGoal) -> tuple[float, float, bool] | None:
    """(gap, target reps left used, capped) for a rated set, else ``None``."""
    left, capped = _left(ref)
    if left is None or goal.reps_left is None or ref.reps is None:
        return None
    low, high = _bounds(goal.reps_left)
    target_left = min(max(left, low), high)
    target_reps = _low(goal.reps) or ref.reps
    return (ref.reps - target_reps) + (left - target_left), target_left, capped


def _by_effort(
    learn: Sequence[Exposure], goal: LineGoal, ref: PerformedSet
) -> _Decision:
    load = ref.load_kg or 0.0
    target_reps = _low(goal.reps) or ref.reps
    rated = _gap(ref, goal)
    if rated is None:
        reason = (
            SuggestionReason.UNRATED
            if goal.reps_left is not None
            else SuggestionReason.NO_RULE
        )
        return _Decision(WEIGHT_FROM_EFFORT.id, reason, load, target_reps)
    gap, target_left, capped = rated
    left, _ = _left(ref)
    computed = (
        load
        * (1 + ((ref.reps or 0) + (left or 0)) / EPLEY_DIVISOR)
        / (1 + ((target_reps or 0) + target_left) / EPLEY_DIVISOR)
    )
    prev = _prev_same_weight(learn, goal, ref)
    prev_gap = None
    if prev is not None:
        prev_ref = _reference(prev, goal)
        prev_rated = None if prev_ref is None else _gap(prev_ref, goal)
        prev_gap = None if prev_rated is None else prev_rated[0]
    once, twice = WEIGHT_FROM_EFFORT.value, EFFORT_GAP_TWICE.value
    easier_prev = (
        prev is not None
        and not prev.trimmed
        and prev_gap is not None
        and prev_gap >= twice
    )
    harder_prev = prev_gap is not None and prev_gap <= -twice

    def decided(
        reason: SuggestionReason, *, raise_: bool = False, forced: bool = False
    ) -> _Decision:
        return _Decision(
            WEIGHT_FROM_EFFORT.id,
            reason,
            computed,
            target_reps,
            raise_=raise_,
            forced_limit=forced,
            computed_kg=computed,
            target_left=target_left,
        )

    if capped and gap >= twice:
        return decided(SuggestionReason.EASIER_CAPPED, raise_=True, forced=True)
    if gap >= once:
        return decided(SuggestionReason.EASIER_ONCE, raise_=True)
    if gap >= twice and easier_prev:
        return decided(SuggestionReason.EASIER_TWICE, raise_=True)
    if gap <= -once:
        return decided(SuggestionReason.HARDER_ONCE)
    if gap <= -twice and harder_prev:
        return decided(SuggestionReason.HARDER_TWICE)
    return _Decision(
        WEIGHT_FROM_EFFORT.id, SuggestionReason.ON_TARGET, load, target_reps
    )


def _topped(exposure: Exposure, high: int) -> bool:
    sets = _sets(exposure)
    return bool(sets) and all((x.reps or 0) >= high for x in sets)


def _by_range(
    learn: Sequence[Exposure],
    goal: LineGoal,
    ref: PerformedSet,
    equipment: Equipment,
    gym: Gym,
) -> _Decision:
    load = ref.load_kg or 0.0
    low, high = _bounds(goal.reps) if goal.reps is not None else (ref.reps, ref.reps)
    low, high = low or 0, high or 0
    sets = _sets(learn[-1])
    rule = REPS_THEN_WEIGHT.id
    if any((x.reps or 0) < low for x in sets):
        return _Decision(rule, SuggestionReason.RANGE_MISSED, load, low)
    if not _topped(learn[-1], high):
        fewest = min(x.reps or 0 for x in sets)
        return _Decision(
            rule, SuggestionReason.RANGE_BUILDING, load, min(fewest + 1, high)
        )
    step = _step_above(load, equipment, gym)
    lefts = [left for left, _ in map(_left, sets) if left is not None]
    if not lefts or min(lefts) >= REPS_THEN_WEIGHT.value:
        return _Decision(
            rule,
            SuggestionReason.TOP_OF_RANGE,
            step,
            low,
            raise_=True,
            computed_kg=step,
        )
    prev = _prev_same_weight(learn, goal, ref)
    if prev is not None and not prev.trimmed and _topped(prev, high):
        return _Decision(
            rule,
            SuggestionReason.TOP_OF_RANGE_TWICE,
            step,
            low,
            raise_=True,
            computed_kg=step,
        )
    return _Decision(rule, SuggestionReason.TOP_AT_LIMIT, load, high)


def _by_simple(
    learn: Sequence[Exposure],
    goal: LineGoal,
    ref: PerformedSet,
    equipment: Equipment,
    gym: Gym,
) -> _Decision:
    load = ref.load_kg or 0.0
    reps = _low(goal.reps) or ref.reps
    rule = SIMPLE_PROGRESSION.id
    answer = _answer(learn[-1])
    prev = _prev_same_weight(learn, goal, ref)
    prev_answer = None if prev is None else _answer(prev)
    step = _step_above(load, equipment, gym)
    if answer is EffortSimple.EASY:
        return _Decision(
            rule,
            SuggestionReason.SIMPLE_EASY,
            step,
            reps,
            raise_=True,
            computed_kg=step,
        )
    if answer is EffortSimple.GOOD:
        if prev_answer is EffortSimple.GOOD and prev is not None and not prev.trimmed:
            return _Decision(
                rule,
                SuggestionReason.SIMPLE_GOOD_TWICE,
                step,
                reps,
                raise_=True,
                computed_kg=step,
            )
        return _Decision(rule, SuggestionReason.SIMPLE_GOOD, load, reps)
    if answer is EffortSimple.HARD:
        if prev_answer is EffortSimple.HARD:
            lower = load * (1 - PROGRESSION_MAX_DECREASE.value)
            return _Decision(
                rule, SuggestionReason.SIMPLE_HARD_TWICE, lower, reps, computed_kg=lower
            )
        return _Decision(rule, SuggestionReason.SIMPLE_HARD, load, reps)
    return _Decision(rule, SuggestionReason.UNRATED, load, reps)


# --- plateaus ---------------------------------------------------------------------------

_RUNGS = (
    SuggestionReason.PLATEAU_STEP,
    SuggestionReason.PLATEAU_REPS,
    SuggestionReason.PLATEAU_RESET,
)
#: The decisions a plateau rung may replace: on target, nothing else to do.
_STEADY = frozenset({SuggestionReason.ON_TARGET, SuggestionReason.SIMPLE_GOOD})


def _since_best(learn: Sequence[Exposure]) -> int:
    """Sessions since the last one with a personal best; the first is a best.

    Dominance as `app.domain.strength_records` defines it — a set is a best
    when no earlier set was at least as heavy for at least as many reps —
    kept as the heaviest weight seen per rep count, one forward pass.
    """
    heaviest: dict[int, int] = {}
    last_best = 0
    for index, exposure in enumerate(learn):
        best = False
        for entry in _sets(exposure):
            if entry.load_kg is None or entry.reps is None:
                continue
            grams = _g(entry.load_kg)
            if not any(r >= entry.reps and g >= grams for r, g in heaviest.items()):
                best = True
        for entry in _sets(exposure):
            if entry.load_kg is None or entry.reps is None:
                continue
            heaviest[entry.reps] = max(heaviest.get(entry.reps, 0), _g(entry.load_kg))
        if best:
            last_best = index
    return len(learn) - 1 - last_best


def _plateau_rung(learn: Sequence[Exposure], goal: LineGoal) -> SuggestionReason | None:
    window = (PLATEAU_WINDOW_LONG if goal.small_muscle else PLATEAU_WINDOW_SHORT).value
    since = _since_best(learn)
    if since < window or (since - window) % window:
        return None
    recent = {e.session_id for e in learn[-(since + 1) :]}
    medians = [
        p.median_kg
        for p in estimate_line(learn).points
        if p.session_id in recent and p.median_kg is not None
    ]
    if len(medians) > 1 and medians[-1] > medians[0]:
        return None
    return _RUNGS[min((since - window) // window, len(_RUNGS) - 1)]


# --- the suggestion ---------------------------------------------------------------------


def _first_time(goal: LineGoal) -> Suggestion:
    return Suggestion(
        outcome=SuggestionOutcome.FIRST_TIME,
        load_kg=None,
        reps=_low(goal.reps),
        change_kg=None,
        explanation=Explanation(
            rule=None,
            evidence=None,
            reason=SuggestionReason.FIRST_TIME,
            inputs=(),
            target_reps=_low(goal.reps),
            target_reps_left=_low(goal.reps_left),
            computed_kg=None,
            limit=None,
        ),
    )


def suggest(
    goal: LineGoal,
    history: Sequence[Exposure],
    *,
    phase: PlanPhase | None = None,
    gym: Gym = DEFAULT_GYM,
) -> Suggestion:
    """Next session's weight and reps for one line, from ``history``.

    ``history`` is the exercise's finished sessions, oldest first, with
    ``trimmed`` already marked (:func:`trimmed`). See the module docstring
    for the rules, limits and what is never learned from.
    """
    learn = [e for e in history if not e.dont_learn]
    if not learn:
        return _first_time(goal)
    last = learn[-1]
    ref = _reference(last, goal)
    inputs = tuple(_input(e, goal) for e in learn[-STREAK_SESSIONS.value :])
    if ref is None or ref.load_kg is None or goal.equipment is None:
        reps = _low(goal.reps) or (None if ref is None else ref.reps)
        load = None if ref is None else ref.load_kg
        return _build(
            goal,
            inputs,
            PROGRESSION_HOLD.id,
            SuggestionReason.NO_RULE,
            load,
            load,
            reps,
            None,
            None,
        )
    equipment = goal.equipment
    load = ref.load_kg
    current = nearest_loadable(load, equipment, gym)

    if goal.rule is Progression.TOP_BACKOFF and goal.set_kind is SetKind.BACKOFF:
        lighter = load * (1 - BACKOFF_DROP.value)
        kg = nearest_loadable(lighter, equipment, gym)
        return _build(
            goal,
            inputs,
            BACKOFF_DROP.id,
            SuggestionReason.BACKOFF,
            current,
            kg,
            _low(goal.reps) or ref.reps,
            lighter,
            None,
        )

    match goal.rule:
        case Progression.RIR_LOAD | Progression.TOP_BACKOFF:
            decision = _by_effort(learn, goal, ref)
        case Progression.DOUBLE_PROGRESSION:
            decision = _by_range(learn, goal, ref, equipment, gym)
        case Progression.SIMPLE:
            decision = _by_simple(learn, goal, ref, equipment, gym)
        case None:
            decision = _Decision(
                PROGRESSION_HOLD.id,
                SuggestionReason.NO_RULE,
                load,
                _low(goal.reps) or ref.reps,
            )

    if decision.raise_ and (last.trimmed or goal.increase_blocked):
        return _build(
            goal,
            inputs,
            decision.rule,
            SuggestionReason.INCREASE_BLOCKED
            if goal.increase_blocked
            else SuggestionReason.TRIMMED,
            current,
            current,
            _low(goal.reps) or ref.reps,
            decision.computed_kg,
            None,
        )

    if (
        decision.reason in _STEADY
        and not last.trimmed
        and not goal.increase_blocked
        and (phase is None or phase.value not in PLATEAU_OFF_PHASES.value)
    ):
        rung = _plateau_rung(learn, goal)
        if rung is SuggestionReason.PLATEAU_STEP:
            step = _step_above(load, equipment, gym)
            decision = _Decision(
                PLATEAU_WINDOW_SHORT.id,
                rung,
                step,
                decision.reps,
                raise_=True,
                computed_kg=step,
            )
        elif rung is SuggestionReason.PLATEAU_REPS:
            return _build(
                goal,
                inputs,
                PLATEAU_WINDOW_SHORT.id,
                rung,
                current,
                current,
                (decision.reps or 0) + 1,
                None,
                None,
            )
        elif rung is SuggestionReason.PLATEAU_RESET:
            lower = load * (1 - PLATEAU_RESET.value)
            decision = _Decision(
                PLATEAU_RESET.id, rung, lower, decision.reps, computed_kg=lower
            )

    if decision.raise_ and _big_step(load, goal, equipment, gym):
        return _build(
            goal,
            inputs,
            decision.rule,
            decision.reason,
            current,
            current,
            (decision.reps or ref.reps or 0) + 1,
            decision.computed_kg,
            BIG_STEP.id,
        )

    target = decision.target_kg
    if decision.forced_limit:
        target = max(target, _limits(load, goal, equipment, gym).high)
    kg, limit = _land(target, load, goal, equipment, gym)
    if decision.forced_limit:
        limit = _limits(load, goal, equipment, gym).high_rule
    if decision.raise_ and _g(kg) < _g(current):
        kg = current
    return _build(
        goal,
        inputs,
        decision.rule,
        decision.reason,
        current,
        kg,
        decision.reps,
        decision.computed_kg,
        limit,
        target_left=decision.target_left,
    )


def _build(
    goal: LineGoal,
    inputs: tuple[ExposureInput, ...],
    rule: str,
    reason: SuggestionReason,
    current: float | None,
    kg: float | None,
    reps: int | None,
    computed: float | None,
    limit: str | None,
    *,
    target_left: float | None = None,
) -> Suggestion:
    if kg is None or current is None:
        outcome, change = SuggestionOutcome.SAME, None
    else:
        change = (_g(kg) - _g(current)) / GRAMS_PER_KG
        if _g(kg) > _g(current):
            outcome = SuggestionOutcome.UP
        elif _g(kg) < _g(current):
            outcome = SuggestionOutcome.DOWN
        elif limit == BIG_STEP.id or reason is SuggestionReason.PLATEAU_REPS:
            outcome = SuggestionOutcome.REPS
        else:
            outcome = SuggestionOutcome.SAME
    return Suggestion(
        outcome=outcome,
        load_kg=kg,
        reps=reps,
        change_kg=change,
        explanation=Explanation(
            rule=rule,
            evidence=REGISTRY[rule].evidence,
            reason=reason,
            inputs=inputs,
            target_reps=_low(goal.reps),
            target_reps_left=(
                _low(goal.reps_left) if target_left is None else target_left
            ),
            computed_kg=computed,
            limit=limit,
        ),
    )
