"""How well one recording answers one prescription, and when that is a match.

Build plan WP-6. Everything here is a pure function of two things the caller
has already loaded — what was planned and what was recorded — so the whole
matching rulebook is testable without a database, a file or a clock.

**Three components, constant weights.** Duration (:data:`WEIGHT_DURATION`),
intensity (:data:`WEIGHT_INTENSITY`) and structure (:data:`WEIGHT_STRUCTURE`),
each a ``min/max`` agreement ratio in ``[0, 1]``, combined by a weighted mean.
The weights are the build plan's and they are constants rather than settings:
a similarity number whose weighting can be tuned per install is a number no
two athletes could ever compare, and the thresholds below are stated against
*these* weights.

**A component with no inputs is not scored zero, and not scored one.** A gym
session typed in by hand has no duration to compare against a prescription
that gives none, and a ride with no power meter has no intensity to compare
against a watts target. Either default is a lie in a different direction —
1.0 invents agreement, 0.0 invents disagreement — so an unassessable component
is **left out and the remaining weights renormalise over what is left**, and
the components that were and were not assessed both travel on the result
(D138). A candidate where *nothing* can be assessed scores ``None``, not 0.0:
the date and the discipline still agree, so the honest outcome is to ask the
athlete rather than to refuse silently.

**Against the pins, never against "now".** The planned intensity comes from
`app.domain.prediction.predict_endurance_load` over the anchor versions the
intent **froze** (invariant 4). Re-deriving it against today's FTP would make
last month's match score change when a test is entered today.

**The thresholds** (:data:`AUTO_LINK_SIMILARITY`, :data:`PROPOSAL_SIMILARITY`)
are the build plan's, and :func:`classify` is the only place they are applied.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.anchors import AnchorType
from app.domain.prediction import PinnedAnchor, predict_endurance_load
from app.domain.resolution import resolve_target
from app.domain.workout import (
    Channel,
    EnduranceWorkout,
    StepRole,
    Target,
    flatten,
)

# --- the constants the whole work package is stated in ------------------------

#: Weight of the duration term. The heaviest because it is the one quantity
#: measured the same way on both sides — prescribed seconds against recorded
#: seconds — with no anchor, no detector and no model in between.
WEIGHT_DURATION = 0.4

#: Weight of the intensity term: planned normalized power against recorded
#: normalized power, or a prescribed heart-rate target against the recorded
#: average. Lighter than duration because both sides are modelled.
WEIGHT_INTENSITY = 0.3

#: Weight of the structure term: how many work efforts were prescribed against
#: how many were detected. A *hint*, as the build plan calls it — interval
#: detection is a threshold crossing on a smoothed trace, not a transcript.
WEIGHT_STRUCTURE = 0.3

#: Similarity at or above which a link is created without asking (build plan
#: WP-6.3). Still revocable: the athlete can unlink it like any other.
AUTO_LINK_SIMILARITY = 0.75

#: Similarity at or above which a **proposal** is created for the athlete to
#: confirm. Below it nothing is proposed and the activity stands unplanned.
PROPOSAL_SIMILARITY = 0.4

#: How many days either side of a session's athlete-local date a planned
#: session may sit and still be a candidate (build plan WP-6.1).
CANDIDATE_WINDOW_DAYS = 1

#: Days of grace after its date before a planned session with no link is
#: marked missed (build plan WP-6.7: "end of day+1"). A session dated Monday
#: is missed once the athlete's local clock has passed the end of Tuesday.
MISSED_GRACE_DAYS = 1

#: Fewest prescribed work units (efforts, sets) that make the structure term
#: mean anything. One work step is not a structure: a steady endurance ride is
#: prescribed as a single work step and correctly detects **no** intervals,
#: which as a ratio reads 0.0 and would put a perfectly executed ride below the
#: auto-link threshold for having been ridden steadily (D139).
MIN_STRUCTURE_UNITS = 2


class MatchLinkStatus(StrEnum):
    """What one link between a recording and a planned session claims.

    ``AUTO_HIGH`` and ``PENDING`` are machine verdicts and re-running matching
    may replace either. ``CONFIRMED`` and ``DISPLACED`` are the athlete's and
    are **sticky**: :data:`STICKY_STATUSES` is the set matching never touches,
    which is what makes "I already told you what this was" hold across every
    later re-run (build plan WP-6.6).

    ``DISPLACED`` is the executed-instead-of link (WP-6.4): the athlete trained
    and it was not this, so the planned session is neither missed nor
    completed, and the activity is scored standalone with no adherence axes.
    """

    AUTO_HIGH = "auto_high"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISPLACED = "displaced"


#: Link statuses a re-run of matching may never overwrite or remove.
STICKY_STATUSES: frozenset[MatchLinkStatus] = frozenset(
    {MatchLinkStatus.CONFIRMED, MatchLinkStatus.DISPLACED}
)

#: Link statuses that mean the athlete has not yet ruled on the link.
OPEN_LINK_STATUSES: frozenset[MatchLinkStatus] = frozenset(
    {MatchLinkStatus.AUTO_HIGH, MatchLinkStatus.PENDING}
)


class MatchComponent(StrEnum):
    """The three things a similarity score is made of."""

    DURATION = "duration"
    INTENSITY = "intensity"
    STRUCTURE = "structure"


#: Nominal weight of each component, before renormalisation.
COMPONENT_WEIGHTS: Mapping[MatchComponent, float] = {
    MatchComponent.DURATION: WEIGHT_DURATION,
    MatchComponent.INTENSITY: WEIGHT_INTENSITY,
    MatchComponent.STRUCTURE: WEIGHT_STRUCTURE,
}


class IntensityBasis(StrEnum):
    """Which channel the intensity term compared.

    ``POWER`` is planned normalized power (`predict_endurance_load` over the
    pinned anchors) against the recorded normalized power. ``HR`` is the
    duration-weighted midpoint of the prescribed heart-rate targets against the
    recorded average heart rate — the fallback the build plan names for a ride
    with no power meter.
    """

    POWER = "power"
    HR = "hr"


class StructureBasis(StrEnum):
    """Which units the structure term counted.

    ``INTERVALS`` is prescribed work steps against
    `app.domain.alignment.detect_work_intervals`' output. ``SETS`` is the
    strength equivalent: prescribed working sets against logged ones, because
    a gym session's structure is its set list and not a timeline (WP-5.2).
    """

    INTERVALS = "intervals"
    SETS = "sets"


class EveningPromptKind(StrEnum):
    """Why an evening prompt was raised.

    WP-6 raises exactly one kind — a planned session went past its grace with
    nothing linked to it — and WP-7 consumes the record and adds its own.
    """

    MISSED_SESSION = "missed_session"


class EveningPromptStatus(StrEnum):
    """Where an evening prompt stands.

    WP-6 writes ``PENDING`` and nothing else; the two terminal members are
    WP-7's, which owns answering a prompt and expiring one after
    :data:`PROMPT_TTL_HOURS`.
    """

    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"


#: How long an evening prompt stands before WP-7 expires it into an
#: auto-reason of ``not_provided`` (build plan WP-7.3). Stored as the prompt's
#: ``expires_at`` when the record is written, so the deadline is a fact about
#: the prompt rather than a constant the expiry job has to agree with later.
PROMPT_TTL_HOURS = 72


# --- the score ----------------------------------------------------------------


def ratio(left: float | None, right: float | None) -> float | None:
    """``min / max`` of two positive quantities: 1.0 when they agree.

    ``None`` when either side is missing or non-positive — which is a
    *component that cannot be assessed*, not a component that scored zero. The
    same shape as `app.domain.alignment`'s confidence ratio and for the same
    reason: monotonically non-increasing as either value moves away from the
    other, and symmetric, so "half as long" and "twice as long" agree equally
    badly.
    """
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return min(left, right) / max(left, right)


@dataclass(frozen=True, slots=True)
class ScoredComponent:
    """One component that could be assessed, and what it was assessed from.

    Args:
        component: Which of the three.
        score: Its agreement ratio, in ``[0, 1]``.
        weight: The weight actually applied — the nominal weight renormalised
            over the components that were assessed, so the applied weights
            always sum to 1.
        nominal_weight: The weight the build plan gives this component.
        planned: The prescribed quantity it compared.
        actual: The recorded quantity it compared.
        basis: Which channel or unit the two are in, when the component has a
            choice of them; ``None`` for duration, which has only seconds.
    """

    component: MatchComponent
    score: float
    weight: float
    nominal_weight: float
    planned: float
    actual: float
    basis: str | None = None


@dataclass(frozen=True, slots=True)
class UnassessedComponent:
    """One component that had nothing to compare, and why.

    Kept rather than dropped, and carried all the way to the stored breakdown:
    a similarity of 0.9 over two components is a different claim from a
    similarity of 0.9 over three, and an athlete looking at a proposal is
    entitled to know which inputs the number had.
    """

    component: MatchComponent
    nominal_weight: float
    reason: str


@dataclass(frozen=True, slots=True)
class Similarity:
    """How well one recording answers one prescription.

    Args:
        score: The weighted mean of :attr:`components`, in ``[0, 1]`` — or
            ``None`` when no component could be assessed at all. ``None`` is
            not zero: it says the comparison was unavailable, and
            :func:`classify` turns it into a question for the athlete rather
            than a refusal.
        components: What was assessed, in :class:`MatchComponent` order.
        not_assessed: What was not, with the reason.
    """

    score: float | None
    components: tuple[ScoredComponent, ...]
    not_assessed: tuple[UnassessedComponent, ...]

    @property
    def assessed(self) -> bool:
        """Whether anything at all could be compared."""
        return bool(self.components)


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """Everything :func:`similarity` compares. Already loaded, already pure.

    Every field is optional because every one of them is genuinely absent for
    some real session: a strength prescription has no seconds, a ride with a
    flat power meter has no normalized power, a device-recorded gym session has
    no logged sets. The absences are what :class:`UnassessedComponent` reports.

    Args:
        planned_duration_s: Prescribed seconds; ``None`` for a strength
            prescription and for a distance-based ride.
        actual_duration_s: Recording time for a device session (A4.4 — the
            pauses already taken out), wall-clock for a typed-in one.
        planned_intensity: Planned normalized power in watts, or the
            duration-weighted prescribed heart rate, per ``intensity_basis``.
        actual_intensity: The recorded counterpart of it.
        intensity_basis: Which of the two the pair is in; ``None`` when no
            pair could be formed.
        planned_units: Prescribed work steps, or prescribed working sets.
        performed_units: Detected work intervals, or logged sets.
        structure_basis: Which of the two the pair counts; ``None`` when
            neither could be counted.
    """

    planned_duration_s: float | None = None
    actual_duration_s: float | None = None
    planned_intensity: float | None = None
    actual_intensity: float | None = None
    intensity_basis: IntensityBasis | None = None
    planned_units: int | None = None
    performed_units: int | None = None
    structure_basis: StructureBasis | None = None


#: What one component's evaluation produced: its score with the two numbers it
#: compared, or the sentence saying why it could not be evaluated.
type Term = tuple[float, float, float] | str


def _duration_term(evidence: MatchEvidence) -> Term:
    """The duration component's ``(score, planned, actual)``, or its reason."""
    planned, actual = evidence.planned_duration_s, evidence.actual_duration_s
    score = ratio(planned, actual)
    if score is None or planned is None or actual is None:
        if planned is None:
            return "the prescription states no duration to compare against"
        return "the session records no duration"
    return score, float(planned), float(actual)


def _intensity_term(evidence: MatchEvidence) -> Term:
    """The intensity component's ``(score, planned, actual)``, or its reason."""
    if evidence.intensity_basis is None:
        return (
            "no channel is prescribed and recorded on both sides: the "
            "prescription and the recording share neither power nor heart rate"
        )
    planned, actual = evidence.planned_intensity, evidence.actual_intensity
    score = ratio(planned, actual)
    if score is None or planned is None or actual is None:
        return "the prescribed and recorded intensities are not both available"
    return score, float(planned), float(actual)


def _structure_term(evidence: MatchEvidence) -> Term:
    """The structure component's ``(score, planned, actual)``, or its reason."""
    planned, performed = evidence.planned_units, evidence.performed_units
    if evidence.structure_basis is None or planned is None or performed is None:
        return "the prescribed and performed work units are not both countable"
    if planned < MIN_STRUCTURE_UNITS:
        return (
            f"the prescription has {planned} work unit(s), fewer than the "
            f"{MIN_STRUCTURE_UNITS} a structure hint needs to mean anything"
        )
    if performed <= 0:
        # Not `ratio`, which refuses a zero: nothing detected against a
        # structured prescription is real disagreement, and the honest score
        # for it is 0.0 rather than an unassessed component.
        return 0.0, float(planned), 0.0
    agreement = min(planned, performed) / max(planned, performed)
    return agreement, float(planned), float(performed)


def similarity(evidence: MatchEvidence) -> Similarity:
    """Score how well a recording answers a prescription, in ``[0, 1]``.

    ::

        duration  = min(planned_s, actual_s) / max(planned_s, actual_s)
        intensity = min(planned_I, actual_I) / max(planned_I, actual_I)
        structure = min(planned_n, actual_n) / max(planned_n, actual_n)
        score     = Σ weightᵢ × componentᵢ / Σ weightᵢ   over the assessed ones

    with nominal weights 0.4 / 0.3 / 0.3. The denominator is what makes the
    renormalisation: when every component is assessed it is 1.0 and the formula
    is the plain weighted mean the build plan states; when one is missing the
    other two are scaled up between them rather than the missing one being
    invented as agreement or disagreement (D138).

    Returns:
        The score with its full breakdown. :attr:`Similarity.score` is ``None``
        exactly when no component could be assessed.
    """
    scored: list[ScoredComponent] = []
    absent: list[UnassessedComponent] = []
    bases: Mapping[MatchComponent, str | None] = {
        MatchComponent.DURATION: None,
        MatchComponent.INTENSITY: (
            evidence.intensity_basis.value if evidence.intensity_basis else None
        ),
        MatchComponent.STRUCTURE: (
            evidence.structure_basis.value if evidence.structure_basis else None
        ),
    }
    terms = {
        MatchComponent.DURATION: _duration_term(evidence),
        MatchComponent.INTENSITY: _intensity_term(evidence),
        MatchComponent.STRUCTURE: _structure_term(evidence),
    }
    total_weight = sum(
        COMPONENT_WEIGHTS[component]
        for component, term in terms.items()
        if not isinstance(term, str)
    )
    for component, term in terms.items():
        nominal = COMPONENT_WEIGHTS[component]
        if isinstance(term, str):
            absent.append(
                UnassessedComponent(
                    component=component, nominal_weight=nominal, reason=term
                )
            )
            continue
        value, planned, actual = term
        scored.append(
            ScoredComponent(
                component=component,
                score=value,
                weight=nominal / total_weight,
                nominal_weight=nominal,
                planned=planned,
                actual=actual,
                basis=bases[component],
            )
        )
    if not scored:
        return Similarity(score=None, components=(), not_assessed=tuple(absent))
    total = sum(part.score * part.weight for part in scored)
    # Clamped rather than trusted: the weights are renormalised floats and the
    # thresholds are compared against this number, so a 1.0000000000000002 must
    # not exist for anyone downstream to have to think about.
    return Similarity(
        score=min(1.0, max(0.0, total)),
        components=tuple(scored),
        not_assessed=tuple(absent),
    )


def classify(score: float | None) -> MatchLinkStatus | None:
    """Turn a similarity into what to do about it (build plan WP-6.3).

    * at or above :data:`AUTO_LINK_SIMILARITY` -> ``AUTO_HIGH``, linked without
      being asked (and revocable);
    * at or above :data:`PROPOSAL_SIMILARITY` -> ``PENDING``, a proposal;
    * below it -> ``None``: nothing is proposed and the activity stands
      unplanned;
    * ``None`` (nothing could be compared) -> ``PENDING``. The date and the
      discipline agree — that is why this candidate exists at all — and the
      *comparison* is what is unavailable. Refusing on absent evidence would
      quietly drop every hand-entered gym session that logged no sets.
    """
    if score is None:
        return MatchLinkStatus.PENDING
    if score >= AUTO_LINK_SIMILARITY:
        return MatchLinkStatus.AUTO_HIGH
    if score >= PROPOSAL_SIMILARITY:
        return MatchLinkStatus.PENDING
    return None


def better(left: Similarity, right: Similarity) -> bool:
    """Whether ``left`` is the stronger candidate of two (WP-6, two-planned case).

    A scored candidate always beats an unscored one, however low it scores:
    "we compared them and they disagree" is evidence, and "we could not
    compare them" is not. Two unscored candidates are equally good, and the
    caller's ordering — nearest date first — decides between them.
    """
    if left.score is None:
        return False
    if right.score is None:
        return True
    return left.score > right.score


# --- the candidate window and the missed rule ---------------------------------


def candidate_window(local_date: dt.date) -> tuple[dt.date, dt.date]:
    """The inclusive date range a session's candidates may sit in (WP-6.1).

    ``±CANDIDATE_WINDOW_DAYS`` around the session's **athlete-local** date,
    which is why both sides of the comparison are dates rather than instants:
    a planned session belongs to a day, and a recording is assigned to the day
    it started in the athlete's own timezone (WP-4.4).
    """
    span = dt.timedelta(days=CANDIDATE_WINDOW_DAYS)
    return local_date - span, local_date + span


def in_candidate_window(local_date: dt.date, planned_date: dt.date) -> bool:
    """Whether a planned date is inside a session's candidate window."""
    earliest, latest = candidate_window(local_date)
    return earliest <= planned_date <= latest


def date_distance(local_date: dt.date, planned_date: dt.date) -> int:
    """Whole days between a session's date and a planned session's.

    The tie-breaker when two candidates score the same: the one planned for
    the day the session actually happened on wins over the one either side.
    """
    return abs((planned_date - local_date).days)


def missed_on_or_before(today_local: dt.date) -> dt.date:
    """The latest planned date whose grace has run out by ``today_local``.

    "End of day+1" (WP-6.7) means a session dated *D* is still answerable for
    the whole of *D+1*, so it is missed from *D+2* onward — i.e. once today is
    at least two days past it. Returned as a date rather than applied to one
    so the sweep can express itself as a single ``date <= cutoff`` query.
    """
    return today_local - dt.timedelta(days=MISSED_GRACE_DAYS + 1)


def is_missed(planned_date: dt.date, today_local: dt.date) -> bool:
    """Whether an unanswered planned session's grace has run out (WP-6.7)."""
    return planned_date <= missed_on_or_before(today_local)


# --- the planned side of the intensity term -----------------------------------


def planned_power_intensity(
    workout: EnduranceWorkout, anchors: Mapping[AnchorType, PinnedAnchor]
) -> float | None:
    """The prescription's normalized power in watts, from its **frozen** pins.

    Recovered from `app.domain.prediction.predict_endurance_load` rather than
    computed again: ``IF = NP / FTP`` by definition, so multiplying the
    predicted intensity factor by the pinned FTP gives back exactly the NP the
    1 Hz expansion produced — the same number the session sheet shows, and
    resolved against the anchor version the intent pinned rather than against
    whatever is in force today (invariant 4).

    ``None`` whenever there is nothing honest to say: no FTP pinned, no power
    target anywhere, a distance-based step. Note that a **partially** covered
    prescription (`PredictedLoad.coverage` below 1) yields an under-estimate,
    exactly as the predicted load does, because the uncovered seconds counted
    as zero watts.
    """
    prediction = predict_endurance_load(workout, anchors)
    pinned = anchors.get(AnchorType.FTP)
    if prediction is None or pinned is None:
        return None
    return prediction.intensity_factor * pinned.version.value


def planned_hr_intensity(
    workout: EnduranceWorkout, anchors: Mapping[AnchorType, PinnedAnchor]
) -> float | None:
    """The prescription's duration-weighted heart-rate target, in bpm.

    The fallback for a ride with no power. Weighted by each step's duration so
    a ten-minute prescribed effort does not count as much as an hour of
    endurance, and taken over the step's start and end targets so a ramp
    contributes its own midpoint. Steps that prescribe no heart rate — or
    prescribe it as a percentage of an anchor this session did not pin — are
    left out of both the numerator and the denominator, so what comes back is
    the average of what *was* prescribed rather than an average diluted by
    what was not.

    ``None`` when no step prescribes a resolvable heart rate.
    """
    weighted = 0.0
    seconds = 0
    for flat in flatten(workout):
        duration = flat.duration_s
        if duration is None:
            continue
        midpoints = [
            midpoint
            for targets in (flat.start_targets, flat.end_targets)
            if (target := targets.get(Channel.HR)) is not None
            and (midpoint := _resolved_midpoint(target, anchors)) is not None
        ]
        if not midpoints:
            continue
        weighted += sum(midpoints) / len(midpoints) * duration
        seconds += duration
    return weighted / seconds if seconds else None


def _resolved_midpoint(
    target: Target, anchors: Mapping[AnchorType, PinnedAnchor]
) -> float | None:
    """One heart-rate target's midpoint in bpm, or ``None`` if it does not resolve."""
    resolved = resolve_target(Channel.HR, target, anchors)
    if resolved.resolved_low is None or resolved.resolved_high is None:
        return None
    return (resolved.resolved_low + resolved.resolved_high) / 2


def planned_work_steps(workout: EnduranceWorkout) -> int:
    """How many work steps the prescription flattens to (the structure hint).

    Work steps only: a warm-up crossing the detector's threshold is not a
    prescribed effort, and counting it would make every session with a hard
    warm-up look under-structured.
    """
    return sum(1 for flat in flatten(workout) if flat.role is StepRole.WORK)


# --- the stored wire form -----------------------------------------------------
#
# Written out rather than derived from the dataclasses, for the reason
# `app.domain.session_analysis` gives for its own: the breakdown is stored on
# every link ever created and read back by the UI, so its shape is a contract
# and must not change because a field was renamed for readability.


def similarity_to_json(result: Similarity) -> dict[str, Any]:
    """Render a similarity and its whole breakdown for storage and the API."""
    return {
        "score": result.score,
        "weights": {
            component.value: weight for component, weight in COMPONENT_WEIGHTS.items()
        },
        "components": [
            {
                "component": part.component.value,
                "score": part.score,
                "weight": part.weight,
                "nominal_weight": part.nominal_weight,
                "planned": part.planned,
                "actual": part.actual,
                "basis": part.basis,
            }
            for part in result.components
        ],
        "not_assessed": [
            {
                "component": part.component.value,
                "nominal_weight": part.nominal_weight,
                "reason": part.reason,
            }
            for part in result.not_assessed
        ],
    }


def assessed_components(result: Similarity) -> Sequence[str]:
    """The names of the components a score was actually made of."""
    return [part.component.value for part in result.components]
