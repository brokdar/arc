"""Scoring: what the session was asked to be, against what it was.

Build plan WP-7. Everything here is pure: it takes a prescription that has
already been frozen, resolved and aligned, plus the cleaned 1 Hz columns, and
answers with numbers and the reasons for them. No I/O, no ORM, no clock.

**Five axes, one shape.** Each axis answers with
`app.domain.metrics.Assessment` — a :class:`~app.domain.metrics.Measured`
value in ``[0, 1]` carrying its `MetricExplanation`, or a
:class:`~app.domain.metrics.NotAssessed` carrying the reason it has no honest
answer. That is the A3.7 pattern the metric set already uses, and it is the
whole reason this module never returns ``0.0`` for "there was no power meter":
a zero is a judgement, and the difference between a bad session and an
unmeasurable one is the product.

Which axes apply comes from the purpose template
(`app.domain.templates.PurposeTemplate.axes`), never from this module: a
scorer that decided for itself that `unstructured` has no adherence would be a
second copy of the templates, and the two would drift.

``response`` and ``fuelling`` are in the vocabulary and out of MVP scope. They
answer ``not_assessed("deferred")`` so the shape exists without the behaviour.

**Criteria are evaluated, not just carried.** Every success criterion frozen
into the intent produces a :class:`CriterionOutcome` — passed, failed, or
unevaluable with its reason — and each one is filed under the axis that owns
its kind (:data:`CRITERION_AXES`). A criterion whose axis this purpose does
not carry is still evaluated and reported, on :attr:`SessionScore.
other_criteria`, because a criterion nobody can see is a promise nobody kept.

**The verdict is suggested, never declared.** :func:`suggest_verdict` is a
deterministic table over the axes (see its docstring for the table itself).
The athlete's declaration lives in persistence and this module never sees it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Any

from app.domain.alignment import LOW_CONFIDENCE_REASON
from app.domain.anchors import AnchorType
from app.domain.criteria import (
    AbsoluteLimit,
    Ceiling,
    CriterionKind,
    DurationFloor,
    LoadWithin,
    PercentLimit,
    SetsCompleted,
    SuccessCriterion,
    TimeInBand,
    kind_of,
)
from app.domain.metrics import (
    Assessment,
    Measured,
    MetricExplanation,
    NotAssessed,
    normalized_power,
    value_of,
)
from app.domain.purpose import Purpose
from app.domain.session_analysis import assessment_to_json
from app.domain.sessions import SessionStatus
from app.domain.templates import DEFERRED_AXES, ScoringAxis
from app.domain.workout import Channel, FlatStep

# --- the vocabulary -----------------------------------------------------------


class Verdict(StrEnum):
    """How a session turned out, in five words the whole product agrees on.

    The machine suggests one (:func:`suggest_verdict`) and the athlete
    declares one. They are the same vocabulary on purpose: an override is the
    athlete picking a different member of this enum, not writing an essay,
    which is what makes "how often does the machine agree with me" a question
    with an answer.
    """

    AS_INTENDED = "as_intended"
    UNDER = "under"
    OVER = "over"
    ABANDONED = "abandoned"
    #: The athlete trained, and it was not this (a `displaced` link, WP-6.4).
    DIFFERENT_SESSION = "different_session"


class Reason(StrEnum):
    """Why a session was not as intended — the controlled list (WP-7.3).

    Controlled rather than free text because the point is to be able to count
    them: "three of your last five threshold sessions were cut short by
    ``time``" is a sentence the coaching agent can only write if the reason is
    a value. The free-text note travels *beside* the list, never instead of it.

    ``NOT_PROVIDED`` is the honest member: it is what an expired evening
    prompt records (WP-7.3) and what an athlete who declines to say picks. It
    is not a null — "we asked and got no answer" is a different fact from "we
    never asked".
    """

    TIME = "time"
    WEATHER = "weather"
    HEAT = "heat"
    TRAFFIC = "traffic"
    TERRAIN = "terrain"
    FATIGUE = "fatigue"
    SLEEP = "sleep"
    FUELLING = "fuelling"
    ILLNESS = "illness"
    EQUIPMENT = "equipment"
    GROUP_RIDE = "group_ride"
    FELT_GOOD = "felt_good"
    NOT_PROVIDED = "not_provided"


#: Fewest and most reasons one declaration may carry (WP-7.3). The list is
#: **ordered by primacy** — first is the main one — so the order is data, and
#: a revision that reorders the same three reasons is a real revision.
MIN_REASONS = 1
MAX_REASONS = 3

#: Longest the optional free-text note beside the reasons may be. Generous —
#: it is the athlete's own words — and bounded because it is stored and shown.
MAX_REASON_NOTE_CHARS = 1_000


class CompletionState(StrEnum):
    """What the calendar's week strip colours one day, or one card, by.

    The build plan's list (WP-7.5) plus one member it does not name and the
    calendar cannot do without: :attr:`COMPLETED`. A session is matched and
    recorded some seconds before it is scored, and every session spends the
    gap between ingest and its first score in a state that is neither
    ``planned`` nor any verdict. Rendering it as ``completed-as_intended``
    would be the machine declaring a verdict nobody computed (D152).
    """

    PLANNED = "planned"
    #: Matched and recorded; no score or declaration yet.
    COMPLETED = "completed"
    COMPLETED_AS_INTENDED = "completed-as_intended"
    UNDER = "under"
    OVER = "over"
    ABANDONED = "abandoned"
    DIFFERENT_SESSION = "different_session"
    MISSED = "missed"
    DISPLACED = "displaced"
    #: A recorded session that answers to nothing on the calendar. Never the
    #: state of a *planned* session — it is what a day carries when something
    #: was ridden and nothing was planned.
    UNPLANNED = "unplanned"


#: Verdict -> the state a day showing that verdict is in.
VERDICT_STATES: Mapping[Verdict, CompletionState] = {
    Verdict.AS_INTENDED: CompletionState.COMPLETED_AS_INTENDED,
    Verdict.UNDER: CompletionState.UNDER,
    Verdict.OVER: CompletionState.OVER,
    Verdict.ABANDONED: CompletionState.ABANDONED,
    Verdict.DIFFERENT_SESSION: CompletionState.DIFFERENT_SESSION,
}

#: How bad each state is, worst first. A day rolls up to the worst state any
#: of its sessions is in: a strip that showed the *best* of a day's outcomes
#: would hide the abandoned session behind the completed one, which is the one
#: thing the strip exists to surface.
STATE_SEVERITY: tuple[CompletionState, ...] = (
    CompletionState.ABANDONED,
    CompletionState.MISSED,
    CompletionState.DIFFERENT_SESSION,
    CompletionState.DISPLACED,
    CompletionState.UNDER,
    CompletionState.OVER,
    CompletionState.PLANNED,
    CompletionState.COMPLETED,
    CompletionState.COMPLETED_AS_INTENDED,
    CompletionState.UNPLANNED,
)


def completion_state(status: SessionStatus, verdict: Verdict | None) -> CompletionState:
    """The state one planned session's card is in.

    The planned session's own status leads, because it is the fact: a session
    the sweep marked ``missed`` is missed whatever anyone later computes, and a
    ``displaced`` one says the athlete trained something else. Only a
    ``completed`` session asks the verdict, and only then does the absence of
    one mean :attr:`CompletionState.COMPLETED`.
    """
    if status is SessionStatus.MISSED:
        return CompletionState.MISSED
    if status is SessionStatus.DISPLACED:
        return CompletionState.DISPLACED
    if status is SessionStatus.PLANNED:
        return CompletionState.PLANNED
    if verdict is None:
        return CompletionState.COMPLETED
    return VERDICT_STATES[verdict]


def worst_state(states: Sequence[CompletionState]) -> CompletionState | None:
    """The state a day rolls up to, or ``None`` when nothing happened on it."""
    ranked = [state for state in STATE_SEVERITY if state in states]
    return ranked[0] if ranked else None


# --- results ------------------------------------------------------------------


class TargetBias(StrEnum):
    """Which side of the prescription the execution missed on."""

    UNDER = "under"
    OVER = "over"
    #: Missed on both sides equally, or by nothing at all.
    ON_TARGET = "on_target"


class VerdictRule(StrEnum):
    """Which row of :func:`suggest_verdict`'s table produced the suggestion.

    Stored with the score so the reason a session reads `under` survives every
    later change to the table: a suggestion whose rule is recorded can be
    explained a year later, and one that is not has to be re-derived against
    whatever the code says today.
    """

    DISPLACED_LINK = "displaced_link"
    COMPLETION_BELOW_FLOOR = "completion_below_floor"
    EXECUTION_AT_OR_ABOVE_FLOOR = "execution_at_or_above_floor"
    OFF_TARGET_OVER = "off_target_over"
    OFF_TARGET_UNDER = "off_target_under"
    CEILING_EXCEEDED = "ceiling_exceeded"
    COMPLETION_ABOVE_CEILING = "completion_above_ceiling"
    COMPLETION_SHORT = "completion_short"
    NOTHING_CONTRADICTS = "nothing_contradicts"


@dataclass(frozen=True, slots=True)
class CriterionOutcome:
    """One success criterion, checked.

    Args:
        index: Position in the intent's frozen ``success_criteria``. The join
            key back onto the prescription the athlete is looking at.
        kind: The criterion's tag, so a client can render it without parsing
            the stored criterion again.
        passed: ``True``/``False``, or ``None`` when it could not be checked —
            which :attr:`not_assessed` then explains. Three states, because
            "we could not tell" is not a failure and rendering it as one would
            make a ride with no power meter look like a ride ridden badly.
        observed: What was measured, in the criterion's own terms (a fraction
            for `time_in_band`, seconds for `ceiling` and `duration_floor`).
        required: What the criterion asked for, same units as `observed`.
        detail: One sentence for the athlete, always present.
        not_assessed: Why it could not be checked; ``None`` when it was.
    """

    index: int
    kind: CriterionKind
    passed: bool | None
    observed: float | None
    required: float | None
    detail: str
    not_assessed: str | None = None


@dataclass(frozen=True, slots=True)
class AxisResult:
    """One scoring axis: its number or its reason, and the criteria under it."""

    axis: ScoringAxis
    assessment: Assessment
    criteria: tuple[CriterionOutcome, ...] = ()

    @property
    def value(self) -> float | None:
        """The score, or ``None`` when the axis was not assessed."""
        return value_of(self.assessment)


@dataclass(frozen=True, slots=True)
class SessionScore:
    """One computed score: every applicable axis, and the suggested verdict."""

    purpose: Purpose
    axes: tuple[AxisResult, ...]
    suggested_verdict: Verdict
    verdict_rule: VerdictRule
    verdict_rationale: str
    #: Criteria whose axis this purpose is not scored on. Evaluated anyway —
    #: they are part of the frozen prescription and the athlete can see them.
    other_criteria: tuple[CriterionOutcome, ...] = ()
    #: True when the link is `displaced`: the athlete trained something else,
    #: so nothing here compares the recording against the prescription.
    standalone: bool = False

    def axis(self, axis: ScoringAxis) -> AxisResult | None:
        """One axis of this score, or ``None`` when the purpose omits it."""
        return next((one for one in self.axes if one.axis is axis), None)


# --- the inputs ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoredStep:
    """One planned work step, paired with the rows it was performed over.

    Built by the caller from `app.domain.alignment.Alignment` and the flattened
    prescription: this module never aligns anything, so the same alignment
    version scores the session every time it is recomputed.

    Args:
        step_index: `app.domain.workout.FlatStep.index`.
        repetition: The step's repeat-block iteration numbers, outermost
            first — what makes "the last rep versus the first" answerable.
        block: `app.domain.workout.FlatStep.block` — which repeat block
            :attr:`repetition` is counting. Carried beside it and **required**,
            because the iteration number alone does not identify a repetition:
            3 × 30 s sprints followed by 3 × 5 min at threshold emit
            ``(1,) (2,) (3,)`` twice, and grouping on the number would put
            sprint 1 and threshold 1 in one bucket.
        confidence: The alignment confidence of the pair.
        start_index: First row of the detected effort, on the 1 Hz grid.
        end_index: One past the last.
        targets: Channel -> the midpoint of the step's own resolved target.
            A :class:`~app.domain.criteria.Band`'s bounds are fractions of
            this, which is why an unresolved channel is simply absent rather
            than zero.
    """

    step_index: int
    repetition: tuple[int, ...]
    block: tuple[int, ...]
    confidence: float
    start_index: int
    end_index: int
    targets: Mapping[Channel, float] = dataclass_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoringInputs:
    """Everything one score is computed from, already resolved.

    Assembled by `app.services.scoring`; nothing here is looked up, so the
    same inputs always produce the same score.
    """

    purpose: Purpose
    #: The axes the purpose template lists, in its own order.
    axes: tuple[ScoringAxis, ...]
    #: The intent's frozen success criteria, in their stored order — the index
    #: of each one is `CriterionOutcome.index`.
    criteria: tuple[SuccessCriterion, ...] = ()
    #: The flattened prescription, for the selectors to pick from.
    steps: tuple[FlatStep, ...] = ()
    #: Endurance: prescribed and recorded seconds.
    planned_duration_s: int | None = None
    actual_duration_s: float | None = None
    #: Strength: prescribed and logged working sets.
    planned_sets: int | None = None
    performed_sets: int | None = None
    #: One entry per **prescribed set**, in execution order: the kilograms it
    #: asked for, or ``None`` when it was prescribed some other way.
    prescribed_loads_kg: tuple[float | None, ...] = ()
    #: One entry per **logged set**, in the order they were logged.
    performed_loads_kg: tuple[float | None, ...] = ()
    #: The cleaned 1 Hz columns, nulls preserved.
    channels: Mapping[Channel, Sequence[float | None]] = dataclass_field(
        default_factory=dict
    )
    #: Work steps the alignment kept, in step order.
    scored_steps: tuple[ScoredStep, ...] = ()
    #: Step indexes the confidence gate refused.
    excluded_steps: tuple[int, ...] = ()
    #: Work steps no detected effort was assigned to.
    unmatched_steps: tuple[int, ...] = ()
    #: The value of each anchor version the intent pinned, by type. A
    #: `percent_of_anchor` ceiling resolves against this and nothing else.
    anchors: Mapping[AnchorType, float] = dataclass_field(default_factory=dict)
    #: True for a `displaced` link — see :attr:`SessionScore.standalone`.
    standalone: bool = False


# --- constants the axes and the verdict table are written in -------------------

#: Excess above a ceiling's own allowance that takes its score from 1 to 0, in
#: seconds. A ceiling already states how long above it is still a pass; five
#: minutes past *that* is a comprehensively broken cap, and grading the tenth
#: minute worse than the sixth would be inventing precision.
DISCIPLINE_EXCESS_WINDOW_S = 300

#: Fade across a repeat block that costs nothing. Five per cent between the
#: first and last effort of a set is execution, not decay.
PACING_ALLOWED_FADE = 0.05

#: Fade at which the pacing axis reaches zero. A quarter of the opening power
#: gone by the last rep is a session that fell apart, however it started.
PACING_ZERO_FADE = 0.25

#: Below this, an axis says the session was not executed as prescribed.
EXECUTION_FLOOR = 0.8

#: Below this fraction of the prescription, the session was abandoned.
ABANDONED_COMPLETION = 0.5

#: At or above this multiple of the prescription, the session ran long enough
#: to be a different session from the one on the calendar.
OVER_COMPLETION_RATIO = 1.25

#: Below this fraction, a session that was otherwise unremarkable came up
#: short. Not 1.0: nobody finishes a two-hour ride to the second.
SHORT_COMPLETION = 0.95

#: What every axis says when the link is `displaced`.
STANDALONE_REASON = (
    "the athlete trained something else, so this session is scored standalone "
    "and nothing is compared against the prescription"
)

#: What the two reserved axes say.
DEFERRED_REASON = "deferred"

#: Which axis owns each criterion kind. A criterion whose axis the purpose
#: does not carry is still evaluated — see the module docstring.
CRITERION_AXES: Mapping[CriterionKind, ScoringAxis] = {
    CriterionKind.DURATION_FLOOR: ScoringAxis.COMPLETION,
    CriterionKind.TIME_IN_BAND: ScoringAxis.ADHERENCE,
    CriterionKind.CEILING: ScoringAxis.DISCIPLINE,
    CriterionKind.SETS_COMPLETED: ScoringAxis.SETS_LOAD,
    CriterionKind.LOAD_WITHIN: ScoringAxis.SETS_LOAD,
}


# --- the smoothing a band and a ceiling are compared through --------------------


def trailing_mean(values: Sequence[float | None], window_s: int) -> list[float | None]:
    """Trailing rolling mean over the readings a window actually holds.

    **Trailing**, unlike `app.domain.alignment.smooth`, because that is what a
    :class:`~app.domain.criteria.Band` and a
    :class:`~app.domain.criteria.Ceiling` declare: their ``smoothing_s`` is
    documented as a trailing window and is frozen into the intent, so scoring
    has to apply the window that was promised rather than the one the interval
    detector happens to use. It is also the window normalized power is defined
    over, which keeps a threshold step's band and its NP talking about the
    same series.

    Rows with no reading contribute nothing and stay ``None`` when the whole
    window is empty: a recording stop is not a period of low power, and
    averaging across one would score a step against samples that do not exist.

    ``window_s`` of 0 or 1 returns the readings unchanged.

    **One pass, not one per row.** The window slides by exactly one row, so the
    sum is carried: the arriving reading is added and the departing one
    subtracted, which makes this O(n) instead of O(n × window). Re-slicing and
    re-summing cost 1.7 s on a five-hour column at
    `app.domain.criteria.MAX_SMOOTHING_S`, per channel and window, and
    :func:`score_session` is a synchronous call awaited on the ingest and match
    paths — that time was the API's event loop, blocked (D163).

    The carried sum counts **only readings**, exactly as the slice did: a row
    with no reading changes neither the total nor the count, and a window
    holding none of them resets the total rather than carrying a rounding
    residue across the gap.
    """
    if window_s < 0:
        raise ValueError(f"window_s must not be negative, got {window_s}")
    if window_s <= 1:
        return list(values)
    smoothed: list[float | None] = []
    total = 0.0
    readings = 0
    for index, value in enumerate(values):
        if value is not None:
            total += value
            readings += 1
        leaving = index - window_s
        if leaving >= 0 and (gone := values[leaving]) is not None:
            total -= gone
            readings -= 1
        if readings == 0:
            total = 0.0
        smoothed.append(total / readings if readings else None)
    return smoothed


def _smoothed(
    inputs: ScoringInputs,
    channel: Channel,
    window_s: int,
    cache: dict[tuple[Channel, int], list[float | None]],
) -> list[float | None] | None:
    """One channel smoothed to one window, computed once per score."""
    column = inputs.channels.get(channel)
    if column is None:
        return None
    key = (channel, window_s)
    if key not in cache:
        cache[key] = trailing_mean(column, window_s)
    return cache[key]


def _no_channel(channel: Channel) -> str:
    """The reason an axis gives when the channel it judges was not recorded."""
    return f"no {channel.value} was recorded"


# --- completion ----------------------------------------------------------------


def score_completion(inputs: ScoringInputs) -> Assessment:
    """Fraction of the prescription that was performed (WP-7.1).

    Seconds for an endurance session, working sets for a strength one — the
    two disciplines measure "did you do it" in the only units they have.

    Clamped at 1.0, because the axis is *completion* and a ride twice as long
    as prescribed did not complete it twice. The unclamped ratio travels in
    the explanation and is what :func:`suggest_verdict` reads to tell an
    over-long session from an exact one.
    """
    if inputs.planned_sets is not None:
        return _completion_from_sets(inputs)
    return _completion_from_duration(inputs)


def _completion_from_sets(inputs: ScoringInputs) -> Assessment:
    """The strength half: logged sets over prescribed sets."""
    planned = inputs.planned_sets or 0
    if planned <= 0:
        return NotAssessed("the prescription asks for no sets")
    if inputs.performed_sets is None:
        return NotAssessed("no sets were logged for this session")
    ratio = inputs.performed_sets / planned
    return Measured(
        value=min(1.0, ratio),
        explanation=MetricExplanation(
            formula="completion = min(1, sets logged / sets prescribed)",
            inputs={
                "sets prescribed": f"{planned}",
                "sets logged": f"{inputs.performed_sets}",
                "ratio": f"{ratio:.3f}",
            },
            assumptions=(
                (
                    "a session that logged more sets than were prescribed is "
                    "100 % complete, not more than complete"
                ),
            ),
        ),
    )


def _completion_from_duration(inputs: ScoringInputs) -> Assessment:
    """The endurance half: recorded seconds over prescribed seconds."""
    planned = inputs.planned_duration_s
    if planned is None or planned <= 0:
        return NotAssessed(
            "this prescription states no duration, so there is nothing to have "
            "completed a fraction of"
        )
    if inputs.actual_duration_s is None:
        return NotAssessed("this session has no recorded duration")
    ratio = inputs.actual_duration_s / planned
    return Measured(
        value=min(1.0, ratio),
        explanation=MetricExplanation(
            formula="completion = min(1, recording time / prescribed duration)",
            inputs={
                "prescribed duration": f"{planned} s",
                "recording time": f"{inputs.actual_duration_s:.0f} s",
                "ratio": f"{ratio:.3f}",
            },
            assumptions=(
                (
                    "recording time is the duration training load is computed "
                    "over (A5.1), not elapsed time"
                ),
                "a ride longer than prescribed is 100 % complete, not more",
            ),
        ),
    )


def _completion_ratio(inputs: ScoringInputs) -> float | None:
    """The unclamped ratio, for the verdict table's over-target row."""
    if inputs.planned_sets is not None:
        if not inputs.planned_sets or inputs.performed_sets is None:
            return None
        return inputs.performed_sets / inputs.planned_sets
    planned = inputs.planned_duration_s
    if not planned or inputs.actual_duration_s is None:
        return None
    return inputs.actual_duration_s / planned


def _duration_floor_outcome(
    index: int, criterion: DurationFloor, inputs: ScoringInputs
) -> CriterionOutcome:
    """Check one ``duration_floor``: did the session last long enough?"""
    actual = inputs.actual_duration_s
    if actual is None:
        return CriterionOutcome(
            index=index,
            kind=CriterionKind.DURATION_FLOOR,
            passed=None,
            observed=None,
            required=float(criterion.min_seconds),
            detail=(
                f"at least {criterion.min_seconds} s were asked for; this "
                "session has no recorded duration"
            ),
            not_assessed="this session has no recorded duration",
        )
    passed = actual >= criterion.min_seconds
    return CriterionOutcome(
        index=index,
        kind=CriterionKind.DURATION_FLOOR,
        passed=passed,
        observed=actual,
        required=float(criterion.min_seconds),
        detail=(
            f"{actual:.0f} s recorded against a floor of {criterion.min_seconds} s"
        ),
    )


# --- adherence -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BandTally:
    """Seconds inside, below and above one band, over the steps it covered."""

    inside: int = 0
    below: int = 0
    above: int = 0

    @property
    def total(self) -> int:
        """Seconds with a reading, whichever side of the band they fell."""
        return self.inside + self.below + self.above

    def plus(self, other: _BandTally) -> _BandTally:
        """Two tallies added."""
        return _BandTally(
            inside=self.inside + other.inside,
            below=self.below + other.below,
            above=self.above + other.above,
        )


@dataclass(frozen=True, slots=True)
class _AdherenceResult:
    """The adherence axis and the direction it missed by."""

    assessment: Assessment
    criteria: tuple[CriterionOutcome, ...]
    bias: TargetBias | None


def _score_adherence(
    inputs: ScoringInputs,
    indexed: Sequence[tuple[int, TimeInBand]],
    cache: dict[tuple[Channel, int], list[float | None]],
) -> _AdherenceResult:
    """Time in band across the aligned work steps, criterion-weighted (WP-7.1).

    Each ``time_in_band`` criterion is checked over the steps its selector
    picks **and the alignment kept**. Steps the confidence gate excluded are
    not scored — reason `alignment_low_confidence` — because a wrong pairing
    produces a confident wrong answer, and steps no effort was assigned to are
    not scored either: a step that was never performed is completion's
    business, not adherence's.

    The axis is the criteria's fractions weighted by the **seconds each one
    covered**. A criterion over a 20-minute threshold block and one over a
    30-second sprint are not two equal opinions about the session.
    """
    if not indexed:
        return _AdherenceResult(
            NotAssessed("this session prescribes no time-in-band criterion"), (), None
        )
    outcomes: list[CriterionOutcome] = []
    weighted = 0.0
    seconds = 0
    overall = _BandTally()
    for index, criterion in indexed:
        outcome, tally = _time_in_band_outcome(index, criterion, inputs, cache)
        outcomes.append(outcome)
        if tally is None or tally.total == 0:
            continue
        weighted += (outcome.observed or 0.0) * tally.total
        seconds += tally.total
        overall = overall.plus(tally)
    if seconds == 0:
        return _AdherenceResult(
            NotAssessed(_first_reason(outcomes, "no work step could be scored")),
            tuple(outcomes),
            None,
        )
    return _AdherenceResult(
        Measured(
            value=weighted / seconds,
            explanation=MetricExplanation(
                formula=(
                    "adherence = Σ(fraction in band × seconds covered) / "
                    "Σ(seconds covered), over the time-in-band criteria"
                ),
                inputs={
                    "criteria evaluated": f"{sum(1 for one in outcomes if one.passed is not None)}",
                    "seconds covered": f"{seconds}",
                    "seconds in band": f"{overall.inside}",
                    "seconds below band": f"{overall.below}",
                    "seconds above band": f"{overall.above}",
                },
                assumptions=(
                    (
                        "only work steps the alignment kept are scored; "
                        f"excluded ({LOW_CONFIDENCE_REASON}) and unmatched "
                        "steps are left out"
                    ),
                    (
                        "each band is compared through the trailing window "
                        "frozen into the criterion, not one chosen by the "
                        "scorer"
                    ),
                ),
            ),
        ),
        tuple(outcomes),
        _bias(overall.below, overall.above),
    )


def _bias(below: float, above: float) -> TargetBias:
    """Which side the out-of-band (or out-of-tolerance) time fell on."""
    if below > above:
        return TargetBias.UNDER
    if above > below:
        return TargetBias.OVER
    return TargetBias.ON_TARGET


def _first_reason(outcomes: Sequence[CriterionOutcome], fallback: str) -> str:
    """The first stated reason among unevaluable criteria, or a fallback."""
    return next(
        (one.not_assessed for one in outcomes if one.not_assessed is not None),
        fallback,
    )


def _time_in_band_outcome(
    index: int,
    criterion: TimeInBand,
    inputs: ScoringInputs,
    cache: dict[tuple[Channel, int], list[float | None]],
) -> tuple[CriterionOutcome, _BandTally | None]:
    """Check one ``time_in_band`` criterion and return what it covered."""
    channel = criterion.band.channel
    selected = {step.index for step in criterion.selector.select(inputs.steps)}
    if not selected:
        return (
            _unevaluable(
                index,
                CriterionKind.TIME_IN_BAND,
                criterion.min_fraction,
                "this criterion's selector picks no step of this prescription",
            ),
            None,
        )
    column = _smoothed(inputs, channel, criterion.band.smoothing_s, cache)
    if column is None:
        return (
            _unevaluable(
                index,
                CriterionKind.TIME_IN_BAND,
                criterion.min_fraction,
                _no_channel(channel),
            ),
            None,
        )

    tally = _BandTally()
    scored = 0
    for step in inputs.scored_steps:
        if step.step_index not in selected:
            continue
        target = step.targets.get(channel)
        if target is None:
            continue
        scored += 1
        tally = tally.plus(
            _tally_step(
                column[step.start_index : step.end_index],
                low=criterion.band.low * target,
                high=criterion.band.high * target,
            )
        )
    if scored == 0 or tally.total == 0:
        return (
            _unevaluable(
                index,
                CriterionKind.TIME_IN_BAND,
                criterion.min_fraction,
                _why_nothing_scored(selected, inputs, channel, scored),
            ),
            None,
        )
    fraction = tally.inside / tally.total
    return (
        CriterionOutcome(
            index=index,
            kind=CriterionKind.TIME_IN_BAND,
            passed=fraction >= criterion.min_fraction,
            observed=fraction,
            required=criterion.min_fraction,
            detail=(
                f"{fraction:.0%} of {tally.total} s inside "
                f"{criterion.band.low:.0%}–{criterion.band.high:.0%} of the "
                f"prescribed {channel.value}, against a floor of "
                f"{criterion.min_fraction:.0%}"
            ),
        ),
        tally,
    )


def _tally_step(
    window: Sequence[float | None], *, low: float, high: float
) -> _BandTally:
    """Seconds inside, below and above one step's band. Nulls are not seconds."""
    inside = below = above = 0
    for value in window:
        if value is None:
            continue
        if value < low:
            below += 1
        elif value > high:
            above += 1
        else:
            inside += 1
    return _BandTally(inside=inside, below=below, above=above)


def _why_nothing_scored(
    selected: set[int], inputs: ScoringInputs, channel: Channel, scored: int
) -> str:
    """Say which of the four ways a criterion ended up with no seconds."""
    if scored:
        return f"no {channel.value} was recorded during the steps this criterion covers"
    if selected & set(inputs.excluded_steps):
        return LOW_CONFIDENCE_REASON
    if selected & set(inputs.unmatched_steps):
        return "no effort in the recording could be matched to these steps"
    return (
        f"the steps this criterion covers prescribe no {channel.value} target "
        "to band around"
    )


def _unevaluable(
    index: int, kind: CriterionKind, required: float | None, reason: str
) -> CriterionOutcome:
    """One criterion that could not be checked, and why."""
    return CriterionOutcome(
        index=index,
        kind=kind,
        passed=None,
        observed=None,
        required=required,
        detail=reason,
        not_assessed=reason,
    )


# --- discipline ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DisciplineResult:
    """The discipline axis and whether every ceiling held."""

    assessment: Assessment
    criteria: tuple[CriterionOutcome, ...]
    ok: bool | None


def _score_discipline(
    inputs: ScoringInputs,
    indexed: Sequence[tuple[int, Ceiling]],
    cache: dict[tuple[Channel, int], list[float | None]],
) -> _DisciplineResult:
    """Time above the ceilings the intent froze (WP-7.1).

    Over the **whole recording**, not the aligned steps: a recovery ride's cap
    is a statement about the ride, and an athlete who stays under it inside
    every prescribed step and sprints for a sign in between has still broken
    it.

    Each ceiling scores 1.0 while its own allowance holds and decays to 0 over
    :data:`DISCIPLINE_EXCESS_WINDOW_S` seconds of excess beyond it.
    """
    if not indexed:
        return _DisciplineResult(
            NotAssessed("this session prescribes no ceiling"), (), None
        )
    outcomes: list[CriterionOutcome] = []
    scores: list[float] = []
    for index, criterion in indexed:
        outcome, score = _ceiling_outcome(index, criterion, inputs, cache)
        outcomes.append(outcome)
        if score is not None:
            scores.append(score)
    if not scores:
        return _DisciplineResult(
            NotAssessed(_first_reason(outcomes, "no ceiling could be checked")),
            tuple(outcomes),
            None,
        )
    return _DisciplineResult(
        Measured(
            value=sum(scores) / len(scores),
            explanation=MetricExplanation(
                formula=(
                    "discipline = mean over ceilings of "
                    "max(0, 1 − (seconds above − allowance) / "
                    f"{DISCIPLINE_EXCESS_WINDOW_S})"
                ),
                inputs={
                    "ceilings checked": f"{len(scores)}",
                    "ceilings held": f"{sum(1 for one in outcomes if one.passed)}",
                },
                assumptions=(
                    (
                        "a ceiling is judged over the whole recording, not "
                        "only over the prescribed work steps"
                    ),
                    ("excess beyond five minutes cannot make a broken cap more broken"),
                ),
            ),
        ),
        tuple(outcomes),
        all(one.passed for one in outcomes if one.passed is not None),
    )


def _ceiling_outcome(
    index: int,
    criterion: Ceiling,
    inputs: ScoringInputs,
    cache: dict[tuple[Channel, int], list[float | None]],
) -> tuple[CriterionOutcome, float | None]:
    """Check one ``ceiling`` and return its contribution to the axis."""
    allowance = float(criterion.max_seconds_above)
    limit = _resolve_limit(criterion, inputs.anchors)
    if limit is None:
        anchor = criterion.limit
        name = anchor.anchor_type.value if isinstance(anchor, PercentLimit) else "?"
        return (
            _unevaluable(
                index,
                CriterionKind.CEILING,
                allowance,
                f"this ceiling is a percentage of {name}, which this session "
                "pinned no version of",
            ),
            None,
        )
    column = _smoothed(inputs, criterion.channel, criterion.smoothing_s, cache)
    if column is None:
        return (
            _unevaluable(
                index,
                CriterionKind.CEILING,
                allowance,
                _no_channel(criterion.channel),
            ),
            None,
        )
    above = sum(1 for value in column if value is not None and value > limit)
    passed = above <= criterion.max_seconds_above
    excess = max(0.0, above - allowance)
    score = 1.0 if passed else max(0.0, 1.0 - excess / DISCIPLINE_EXCESS_WINDOW_S)
    return (
        CriterionOutcome(
            index=index,
            kind=CriterionKind.CEILING,
            passed=passed,
            observed=float(above),
            required=allowance,
            detail=(
                f"{above} s above {limit:.0f} {criterion.channel.value}, against "
                f"an allowance of {criterion.max_seconds_above} s"
            ),
        ),
        score,
    )


def _resolve_limit(
    criterion: Ceiling, anchors: Mapping[AnchorType, float]
) -> float | None:
    """A ceiling's bound in the channel's own unit, or ``None`` unresolvable."""
    limit = criterion.limit
    if isinstance(limit, AbsoluteLimit):
        return limit.value
    anchor = anchors.get(limit.anchor_type)
    return None if anchor is None else anchor * limit.pct


# --- pacing ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BlockFade:
    """One repeat block's fade, measured against itself."""

    #: Position of the block in execution order, 1-based — what the
    #: explanation calls it, because a tree path is not a thing to show anyone.
    position: int
    repetitions: int
    first: float
    last: float
    value: float

    @property
    def ratio(self) -> float:
        """Last repetition's NP over the first's."""
        return self.last / self.first


def _score_pacing(inputs: ScoringInputs) -> Assessment:
    """Fade across a repeat block: last rep's NP against the first's (WP-7.1).

    Efforts are grouped by **which block they belong to and which iteration of
    it they are** — `app.domain.workout.FlatStep.block` beside
    :attr:`~ScoredStep.repetition`. The iteration number alone is not an
    identity: it restarts at 1 in every sibling block, so ``3 × 30 s`` sprints
    followed by ``3 × 5 min`` at threshold would put sprint 1 and threshold 1
    into one "rep 1" and compare that concatenation against a rep 3 made the
    same way — a fade measured between two efforts that were never the same
    effort (D162). Nesting is not a problem: ``5 × (4 min + 1 min)`` is five
    repetitions of one block however its recoveries sit inside it.

    Each block is compared **against itself**, and the axis is the **worst** of
    them. A session is not well paced because one of its two blocks held; the
    block that fell apart is the finding, and averaging it away would hide
    exactly what this axis exists to surface — the same reason
    :func:`worst_state` rolls a day up to its worst outcome rather than its
    best.

    Normalized power is taken over the recording rows the alignment assigned to
    each repetition, concatenated across that repetition's steps.
    :data:`PACING_ALLOWED_FADE` of fade is free — nobody holds the fifth
    interval to the watt — and the score reaches zero at
    :data:`PACING_ZERO_FADE`.
    """
    blocks: dict[tuple[int, ...], dict[int, list[ScoredStep]]] = {}
    for step in inputs.scored_steps:
        if step.repetition:
            blocks.setdefault(step.block, {}).setdefault(step.repetition[0], []).append(
                step
            )
    repeated = [
        (position, reps)
        for position, reps in enumerate(
            (blocks[key] for key in sorted(blocks)), start=1
        )
        if len(reps) >= 2
    ]
    if not repeated:
        return NotAssessed(
            "this session has fewer than two repeated work blocks the "
            "alignment could score, so there is no fade to measure"
        )
    faded = [
        measured
        for position, reps in repeated
        if (measured := _block_fade(inputs, position, reps)) is not None
    ]
    if not faded:
        return NotAssessed(
            "no power was recorded across the first and last repetition, so "
            "their normalized power cannot be compared"
        )
    worst = min(faded, key=lambda one: one.value)
    return Measured(
        value=worst.value,
        explanation=MetricExplanation(
            formula=(
                "pacing = the lowest score of any repeat block, where a "
                "block scores 1 while its fade ≤ 5 %, falling to 0 at 25 % "
                "fade, and fade = 1 − NP(last rep) / NP(first rep) of that "
                "block"
            ),
            inputs={
                "repeat blocks measured": f"{len(faded)}",
                "worst block": f"{worst.position}",
                "repetitions in it": f"{worst.repetitions}",
                "NP of its first repetition": f"{worst.first:.0f} W",
                "NP of its last repetition": f"{worst.last:.0f} W",
                "ratio": f"{worst.ratio:.3f}",
                "score by block": ", ".join(
                    f"block {one.position} {one.value:.0%}" for one in faded
                ),
            },
            assumptions=(
                "a repetition ridden harder than the first is not penalised",
                "only repetitions the alignment kept are compared",
                (
                    "each repeat block is compared against itself: two blocks "
                    "prescribe different efforts, and the first sprint against "
                    "the last threshold interval is not a fade"
                ),
                (
                    "the worst block is the axis; averaging a block that fell "
                    "apart against one that held would hide it"
                ),
            ),
            citation="Allen & Coggan, Training and Racing with a Power Meter",
        ),
    )


def _block_fade(
    inputs: ScoringInputs, position: int, reps: Mapping[int, Sequence[ScoredStep]]
) -> _BlockFade | None:
    """One block's fade score, or ``None`` when its ends cannot be compared."""
    order = sorted(reps)
    first = _rep_normalized_power(inputs, reps[order[0]])
    last = _rep_normalized_power(inputs, reps[order[-1]])
    if first is None or last is None or first <= 0:
        return None
    fade = max(0.0, 1.0 - last / first)
    if fade <= PACING_ALLOWED_FADE:
        value = 1.0
    else:
        span = PACING_ZERO_FADE - PACING_ALLOWED_FADE
        value = max(0.0, 1.0 - (fade - PACING_ALLOWED_FADE) / span)
    return _BlockFade(
        position=position,
        repetitions=len(reps),
        first=first,
        last=last,
        value=value,
    )


def _rep_normalized_power(
    inputs: ScoringInputs, steps: Sequence[ScoredStep]
) -> float | None:
    """NP over one repetition's aligned rows, or ``None`` with no power."""
    column = inputs.channels.get(Channel.POWER)
    if column is None:
        return None
    watts = [
        value
        for step in steps
        for value in column[step.start_index : step.end_index]
        if value is not None
    ]
    return normalized_power(watts) if watts else None


# --- sets_load -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SetsLoadResult:
    """The strength axis, its criteria and the direction the loads missed by."""

    assessment: Assessment
    criteria: tuple[CriterionOutcome, ...]
    bias: TargetBias | None


def _score_sets_load(
    inputs: ScoringInputs,
    sets_criteria: Sequence[tuple[int, SetsCompleted]],
    load_criteria: Sequence[tuple[int, LoadWithin]],
) -> _SetsLoadResult:
    """Sets completed × loads within tolerance (WP-7.1).

    A composite, as the build plan writes it: doing every set at half the
    prescribed weight is not a completed session, and neither is doing two of
    six at exactly the right weight. Sets are paired **positionally**
    (`app.domain.alignment.align_strength`) — a gym session's alignment unit is
    the set list, not a timeline.

    When nothing prescribes kilograms, or the athlete logged none, the load
    term is absent rather than zero and the composite is the sets term alone,
    with the omission stated in the explanation.
    """
    planned = inputs.planned_sets
    if not planned:
        return _SetsLoadResult(NotAssessed("this session prescribes no sets"), (), None)
    if inputs.performed_sets is None:
        return _SetsLoadResult(
            NotAssessed("no sets were logged for this session"), (), None
        )
    sets_fraction = min(1.0, inputs.performed_sets / planned)
    tolerance = load_criteria[0][1].pct_tolerance if load_criteria else None
    within, comparable, light, heavy = _load_agreement(inputs, tolerance)
    load_fraction = within / comparable if comparable else None

    outcomes = [
        _sets_completed_outcome(index, criterion, inputs.performed_sets, planned)
        for index, criterion in sets_criteria
    ]
    outcomes += [
        _load_within_outcome(index, criterion, load_fraction, comparable)
        for index, criterion in load_criteria
    ]
    assumptions = [
        (
            "prescribed and logged sets are paired by position: a gym "
            "session's alignment unit is the set list, not a timeline"
        ),
    ]
    if load_fraction is None:
        assumptions.append(
            "no set could be compared by weight, so the load term is left out "
            "rather than counted as zero"
        )
    return _SetsLoadResult(
        Measured(
            value=sets_fraction * (1.0 if load_fraction is None else load_fraction),
            explanation=MetricExplanation(
                formula=(
                    "sets_load = min(1, sets logged / sets prescribed) × "
                    "fraction of comparable sets within the prescribed tolerance"
                ),
                inputs={
                    "sets prescribed": f"{planned}",
                    "sets logged": f"{inputs.performed_sets}",
                    "sets comparable by weight": f"{comparable}",
                    "sets within tolerance": f"{within}",
                    "tolerance": (
                        "none prescribed" if tolerance is None else f"±{tolerance:.0%}"
                    ),
                },
                assumptions=tuple(assumptions),
            ),
        ),
        tuple(outcomes),
        _bias(light, heavy) if comparable else None,
    )


def _load_agreement(
    inputs: ScoringInputs, tolerance: float | None
) -> tuple[int, int, int, int]:
    """``(within, comparable, lighter, heavier)`` over the positional pairs."""
    if tolerance is None:
        return 0, 0, 0, 0
    within = comparable = light = heavy = 0
    pairs = zip(inputs.prescribed_loads_kg, inputs.performed_loads_kg, strict=False)
    for prescribed, performed in pairs:
        if prescribed is None or performed is None or prescribed <= 0:
            continue
        comparable += 1
        ratio = performed / prescribed
        if abs(ratio - 1.0) <= tolerance:
            within += 1
        elif ratio < 1.0:
            light += 1
        else:
            heavy += 1
    return within, comparable, light, heavy


def _sets_completed_outcome(
    index: int, criterion: SetsCompleted, performed: int, planned: int
) -> CriterionOutcome:
    """Check one ``sets_completed`` criterion."""
    fraction = performed / planned
    return CriterionOutcome(
        index=index,
        kind=CriterionKind.SETS_COMPLETED,
        passed=fraction >= criterion.min_fraction,
        observed=fraction,
        required=criterion.min_fraction,
        detail=(
            f"{performed} of {planned} prescribed sets logged ({fraction:.0%}), "
            f"against a floor of {criterion.min_fraction:.0%}"
        ),
    )


def _load_within_outcome(
    index: int,
    criterion: LoadWithin,
    load_fraction: float | None,
    comparable: int,
) -> CriterionOutcome:
    """Check one ``load_within`` criterion.

    It passes only when **every** comparable set was inside the tolerance:
    the criterion says "the loads used were within this", not "most of them".
    """
    if load_fraction is None:
        return _unevaluable(
            index,
            CriterionKind.LOAD_WITHIN,
            1.0,
            "no set was prescribed and logged in kilograms, so no load could "
            "be compared",
        )
    return CriterionOutcome(
        index=index,
        kind=CriterionKind.LOAD_WITHIN,
        passed=load_fraction >= 1.0,
        observed=load_fraction,
        required=1.0,
        detail=(
            f"{round(load_fraction * comparable)} of {comparable} comparable "
            f"sets within ±{criterion.pct_tolerance:.0%} of the prescribed load"
        ),
    )


# --- the verdict table -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerdictEvidence:
    """The handful of numbers the verdict table reads.

    Args:
        standalone: The link is `displaced` — the athlete trained something
            else (WP-6.4).
        completion: The completion axis, clamped to ``[0, 1]``.
        completion_ratio: The same, unclamped: 1.4 means the session ran 40 %
            long, which the clamped value cannot say.
        execution: How well the session was executed against its targets —
            `adherence` for an endurance session, `sets_load` for a strength
            one. One field because a session is never both, and two fields
            would double every row of the table.
        execution_axis: Which of the two :attr:`execution` came from.
        bias: Which side the execution missed on, from the same axis.
        discipline_ok: Whether every ceiling held. ``None`` when none was
            checkable — which is not the same as one being broken.
    """

    standalone: bool = False
    completion: float | None = None
    completion_ratio: float | None = None
    execution: float | None = None
    execution_axis: ScoringAxis | None = None
    bias: TargetBias | None = None
    discipline_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class VerdictSuggestion:
    """A suggested verdict, the rule that produced it, and the sentence for it."""

    verdict: Verdict
    rule: VerdictRule
    rationale: str


def suggest_verdict(evidence: VerdictEvidence) -> VerdictSuggestion:
    """Suggest a verdict from the axes. Deterministic, total, ordered.

    The table, first matching row wins. ``ε`` in the conditions means "the
    value was assessed"; an axis that was not assessed never fires a row, so a
    session with no power meter falls through to the rows that do not need one
    rather than being judged on a hole.

    ======  ============================================================  ==================
    Row     Condition                                                     Verdict
    ======  ============================================================  ==================
    1       the link is `displaced`                                       `different_session`
    2       completion ε and < :data:`ABANDONED_COMPLETION` (0.50)        `abandoned`
    3       execution ε and ≥ :data:`EXECUTION_FLOOR` (0.80), and no      `as_intended`
            ceiling was broken
    4       execution ε and < 0.80, and it missed **above** target        `over`
    5       execution ε and < 0.80 (below target, or evenly both ways)    `under`
    6       a ceiling was broken                                          `over`
    7       completion ratio ε and ≥                                      `over`
            :data:`OVER_COMPLETION_RATIO` (1.25)
    8       completion ε and < :data:`SHORT_COMPLETION` (0.95)            `under`
    9       nothing above fired                                           `as_intended`
    ======  ============================================================  ==================

    Why the order is what it is.

    * **Row 1 first** because a displaced link says the prescription was not
      what was executed; every axis below it is comparing against something
      the athlete did not attempt.
    * **Row 2 before row 3** because a session that stopped a third of the way
      in can still have been perfectly in band while it lasted, and
      `as_intended` for a ride that was abandoned is the single worst answer
      this table could give.
    * **Row 3 requires the ceilings** because "exactly as prescribed" cannot
      be true of a recovery ride that spent ten minutes over its cap.
      ``discipline_ok is None`` — nothing checkable — does not block it: an
      unmeasurable ceiling is not a broken one.
    * **Rows 4 and 5 before row 6** because a session that missed its targets
      *and* broke a ceiling is better described by how it missed than by the
      cap.
    * **Row 9** is the honest bottom of the table for two different sessions:
      one where everything assessable passed, and one where nothing could be
      assessed at all. Both get `as_intended` because the suggestion is only
      ever a suggestion — the athlete's declaration is what stands — and a
      table that suggested `abandoned` for every session with no power meter
      would train the athlete to override it without reading it.
    """
    if evidence.standalone:
        return VerdictSuggestion(
            Verdict.DIFFERENT_SESSION,
            VerdictRule.DISPLACED_LINK,
            "this recording is linked as the session the athlete did instead, "
            "so it is scored standalone",
        )
    completion = evidence.completion
    if completion is not None and completion < ABANDONED_COMPLETION:
        return VerdictSuggestion(
            Verdict.ABANDONED,
            VerdictRule.COMPLETION_BELOW_FLOOR,
            f"only {completion:.0%} of the prescription was completed, below "
            f"the {ABANDONED_COMPLETION:.0%} floor",
        )
    execution = evidence.execution
    axis = evidence.execution_axis.value if evidence.execution_axis else "execution"
    if execution is not None and execution >= EXECUTION_FLOOR:
        if evidence.discipline_ok is not False:
            return VerdictSuggestion(
                Verdict.AS_INTENDED,
                VerdictRule.EXECUTION_AT_OR_ABOVE_FLOOR,
                f"{axis} scored {execution:.0%}, at or above the "
                f"{EXECUTION_FLOOR:.0%} floor, and no ceiling was exceeded",
            )
    elif execution is not None:
        if evidence.bias is TargetBias.OVER:
            return VerdictSuggestion(
                Verdict.OVER,
                VerdictRule.OFF_TARGET_OVER,
                f"{axis} scored {execution:.0%} and the session spent more time "
                "above its targets than below them",
            )
        return VerdictSuggestion(
            Verdict.UNDER,
            VerdictRule.OFF_TARGET_UNDER,
            f"{axis} scored {execution:.0%}, below the {EXECUTION_FLOOR:.0%} floor",
        )
    if evidence.discipline_ok is False:
        return VerdictSuggestion(
            Verdict.OVER,
            VerdictRule.CEILING_EXCEEDED,
            "the session spent longer above a prescribed ceiling than the "
            "ceiling allows",
        )
    ratio = evidence.completion_ratio
    if ratio is not None and ratio >= OVER_COMPLETION_RATIO:
        return VerdictSuggestion(
            Verdict.OVER,
            VerdictRule.COMPLETION_ABOVE_CEILING,
            f"the session ran to {ratio:.0%} of what was prescribed",
        )
    if completion is not None and completion < SHORT_COMPLETION:
        return VerdictSuggestion(
            Verdict.UNDER,
            VerdictRule.COMPLETION_SHORT,
            f"{completion:.0%} of the prescription was completed",
        )
    return VerdictSuggestion(
        Verdict.AS_INTENDED,
        VerdictRule.NOTHING_CONTRADICTS,
        "nothing that could be assessed contradicts the prescription",
    )


# --- the whole score ---------------------------------------------------------------


def score_session(inputs: ScoringInputs) -> SessionScore:
    """Score one session on every axis its purpose template lists.

    Total by construction: an axis that cannot be computed answers
    `not_assessed` with its reason, and nothing here raises. Scoring runs on
    the ingest path behind a match, and a scorer that could throw would leave
    an athlete with a matched session and no artefact.
    """
    cache: dict[tuple[Channel, int], list[float | None]] = {}
    indexed = list(enumerate(inputs.criteria))
    if inputs.standalone:
        return _standalone_score(inputs, indexed)

    adherence = _score_adherence(inputs, _of(indexed, TimeInBand), cache)
    discipline = _score_discipline(inputs, _of(indexed, Ceiling), cache)
    sets_load = _score_sets_load(
        inputs, _of(indexed, SetsCompleted), _of(indexed, LoadWithin)
    )
    completion = score_completion(inputs)
    floors = tuple(
        _duration_floor_outcome(index, criterion, inputs)
        for index, criterion in _of(indexed, DurationFloor)
    )
    computed: dict[ScoringAxis, AxisResult] = {
        ScoringAxis.COMPLETION: AxisResult(ScoringAxis.COMPLETION, completion, floors),
        ScoringAxis.ADHERENCE: AxisResult(
            ScoringAxis.ADHERENCE, adherence.assessment, adherence.criteria
        ),
        ScoringAxis.DISCIPLINE: AxisResult(
            ScoringAxis.DISCIPLINE, discipline.assessment, discipline.criteria
        ),
        ScoringAxis.PACING: AxisResult(ScoringAxis.PACING, _score_pacing(inputs)),
        ScoringAxis.SETS_LOAD: AxisResult(
            ScoringAxis.SETS_LOAD, sets_load.assessment, sets_load.criteria
        ),
    }
    axes = tuple(_axis_result(axis, computed) for axis in inputs.axes)

    execution, execution_axis, bias = _execution(inputs.axes, adherence, sets_load)
    suggestion = suggest_verdict(
        VerdictEvidence(
            completion=(
                value_of(completion) if ScoringAxis.COMPLETION in inputs.axes else None
            ),
            completion_ratio=(
                _completion_ratio(inputs)
                if ScoringAxis.COMPLETION in inputs.axes
                else None
            ),
            execution=execution,
            execution_axis=execution_axis,
            bias=bias,
            discipline_ok=(
                discipline.ok if ScoringAxis.DISCIPLINE in inputs.axes else None
            ),
        )
    )
    return SessionScore(
        purpose=inputs.purpose,
        axes=axes,
        suggested_verdict=suggestion.verdict,
        verdict_rule=suggestion.rule,
        verdict_rationale=suggestion.rationale,
        other_criteria=_other_criteria(inputs.axes, computed, indexed),
    )


def _axis_result(
    axis: ScoringAxis, computed: Mapping[ScoringAxis, AxisResult]
) -> AxisResult:
    """One axis of the template, computed or explicitly deferred."""
    if axis in DEFERRED_AXES:
        return AxisResult(axis, NotAssessed(DEFERRED_REASON))
    return computed[axis]


def _of[T](
    indexed: Sequence[tuple[int, SuccessCriterion]], kind: type[T]
) -> list[tuple[int, T]]:
    """The criteria of one concrete type, with their positions kept."""
    return [(index, one) for index, one in indexed if isinstance(one, kind)]


def _execution(
    axes: Sequence[ScoringAxis],
    adherence: _AdherenceResult,
    sets_load: _SetsLoadResult,
) -> tuple[float | None, ScoringAxis | None, TargetBias | None]:
    """Which axis speaks for "was it executed as prescribed", and what it said."""
    if ScoringAxis.ADHERENCE in axes and isinstance(adherence.assessment, Measured):
        return adherence.assessment.value, ScoringAxis.ADHERENCE, adherence.bias
    if ScoringAxis.SETS_LOAD in axes and isinstance(sets_load.assessment, Measured):
        return sets_load.assessment.value, ScoringAxis.SETS_LOAD, sets_load.bias
    return None, None, None


def _other_criteria(
    axes: Sequence[ScoringAxis],
    computed: Mapping[ScoringAxis, AxisResult],
    indexed: Sequence[tuple[int, SuccessCriterion]],
) -> tuple[CriterionOutcome, ...]:
    """The evaluated criteria whose own axis this purpose does not carry."""
    orphaned = {
        index
        for index, criterion in indexed
        if CRITERION_AXES[kind_of(criterion)] not in axes
    }
    return tuple(
        outcome
        for axis, result in computed.items()
        if axis not in axes
        for outcome in result.criteria
        if outcome.index in orphaned
    )


def _standalone_score(
    inputs: ScoringInputs, indexed: Sequence[tuple[int, SuccessCriterion]]
) -> SessionScore:
    """The score of a `displaced` link: every axis refused, and why (WP-6.4).

    A displaced link says the athlete trained and it was *not* this. Every
    axis here compares a recording against a prescription, so every one of
    them would be answering a question nobody asked — and the criteria are
    reported unevaluated for the same reason.
    """
    suggestion = suggest_verdict(VerdictEvidence(standalone=True))
    return SessionScore(
        purpose=inputs.purpose,
        axes=tuple(
            AxisResult(
                axis,
                NotAssessed(
                    DEFERRED_REASON if axis in DEFERRED_AXES else STANDALONE_REASON
                ),
            )
            for axis in inputs.axes
        ),
        suggested_verdict=suggestion.verdict,
        verdict_rule=suggestion.rule,
        verdict_rationale=suggestion.rationale,
        other_criteria=tuple(
            _unevaluable(index, kind_of(criterion), None, STANDALONE_REASON)
            for index, criterion in indexed
        ),
        standalone=True,
    )


# --- serialization ------------------------------------------------------------------


def outcome_to_json(outcome: CriterionOutcome) -> dict[str, Any]:
    """Render one criterion check."""
    return {
        "index": outcome.index,
        "kind": outcome.kind.value,
        "passed": outcome.passed,
        "observed": outcome.observed,
        "required": outcome.required,
        "detail": outcome.detail,
        "not_assessed": outcome.not_assessed,
    }


def axis_to_json(result: AxisResult) -> dict[str, Any]:
    """Render one axis: its value with its explanation, or its reason."""
    return {
        "axis": result.axis.value,
        **assessment_to_json(result.assessment),
        "criteria": [outcome_to_json(one) for one in result.criteria],
    }


def score_to_json(score: SessionScore) -> dict[str, Any]:
    """Render a whole score as the artefact's stored payload.

    The field names are what `app.api.schemas.scoring` validates, so a stored
    payload reads straight back into the response model — the same contract
    the metric artefact keeps.
    """
    return {
        "purpose": score.purpose.value,
        "standalone": score.standalone,
        "suggested_verdict": score.suggested_verdict.value,
        "verdict_rule": score.verdict_rule.value,
        "verdict_rationale": score.verdict_rationale,
        "axes": [axis_to_json(one) for one in score.axes],
        "other_criteria": [outcome_to_json(one) for one in score.other_criteria],
    }
