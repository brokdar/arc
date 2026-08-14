"""What a planned session is expected to cost, from the frozen intent alone.

Predicted load is a **pure function of the prescription and the anchor
versions it pinned**, so it is computed on read and never stored — the same
reasoning as `app.domain.zones` ("zones are always computed, never stored").
There is no column, no migration and no cache to invalidate.

**The 1 Hz expansion.** The endurance prediction builds a second-by-second
list of prescribed watts and feeds it to
:func:`app.domain.metrics.normalized_power`, rather than integrating over step
midpoints in closed form. That looks like the long way round and is the whole
point: the planned number then comes out of the *same function* the recorded
number will at WP-5. A closed-form integral skips the 30 s rolling mean, which
for short intervals inflates NP — a systematic "you did less than planned"
bias in every interval session's adherence score, and very hard to find later.

**Ranges reduce to their midpoint.** ``85-95 % FTP`` is predicted as 90 % FTP.
A prescription is a range because the athlete has latitude, not because the
expected cost is a range; picking either end would bias every prediction in
one direction.

**Strength is not a load.** :attr:`PredictedVolume.volume_load_kg` is
kilograms; :attr:`PredictedLoad.load` is TSS. They are different quantities on
different axes (spec v2 §5.4, §8.3) and must never be added, totalled, or
rendered in the same column — so they are separate types with differently
named fields, and neither carries the other's. A caller that wants "the week's
work" has to say which axis it means.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.anchors import AnchorType, AnchorVersion, describe_anchor
from app.domain.metrics import (
    NP_WINDOW_S,
    MetricExplanation,
    intensity_factor,
    normalized_power,
    training_load,
)
from app.domain.strength import LoadKind, StrengthWorkout
from app.domain.workout import (
    AbsoluteRange,
    Channel,
    EnduranceWorkout,
    PercentOfAnchor,
    Target,
    flatten,
)

#: Longest prescription the 1 Hz expansion will materialise, in seconds. The
#: workout model bounds steps and step counts, not their product: 1 000 steps
#: of 12 hours each is a legal tree and a 43-million-element list. A day is far
#: past anything the MVP plans, so beyond it the answer is "not predictable"
#: rather than an out-of-memory read path.
MAX_PREDICTABLE_DURATION_S = 86_400

_CITATION = "Allen & Coggan, Training and Racing with a Power Meter"


@dataclass(frozen=True, slots=True)
class PinnedAnchor:
    """An anchor version together with the id it was pinned by.

    The domain's :class:`~app.domain.anchors.AnchorVersion` is deliberately
    id-free — it is a value, and identity lives in persistence. A prediction
    has to report *which* version it resolved against
    (:attr:`PredictedLoad.anchor_version_id`), because that is what makes the
    number reproducible later, so the caller hands the two over together.

    Args:
        version_id: The id the planned session pinned (see
            :attr:`app.domain.sessions.SessionIntent.pinned_anchor_versions`).
        version: The version itself.
    """

    version_id: uuid.UUID
    version: AnchorVersion


@dataclass(frozen=True, slots=True)
class PredictedLoad:
    """What an endurance prescription is expected to cost.

    Args:
        load: TSS-equivalent. Never add this to a
            :attr:`PredictedVolume.volume_load_kg`.
        intensity_factor: Planned NP over the pinned FTP.
        duration_s: Total prescribed duration the load covers.
        anchor_version_id: The FTP version it resolved against.
        coverage: Fraction of :attr:`duration_s` that carried a power target.
            Below 1.0 the load is an **under**-estimate: the uncovered part
            counted as zero watts.
        explanation: How the number was arrived at, for rendering next to it.
    """

    load: float
    intensity_factor: float
    duration_s: int
    anchor_version_id: uuid.UUID
    coverage: float
    explanation: MetricExplanation


@dataclass(frozen=True, slots=True)
class PredictedVolume:
    """What a strength prescription is expected to cost. **Not** a load.

    Args:
        volume_load_kg: Σ ``working_sets × reps × kg`` over the rep-based sets
            whose load is in kilograms, or ``None`` when none of them is (a
            session of bodyweight, RPE or %e1RM work has no volume load until
            it is performed, and a hold has no reps to multiply).
        total_sets: Prescribed **working** sets across the whole workout,
            whatever their load kind — the honest denominator, and the measure
            the week rail shows for strength. A per-side round counts twice
            (:attr:`~app.domain.strength.StrengthSet.working_sets`).
        total_hold_s: Σ ``working_sets × duration_s`` over the timed sets, or
            ``None`` when the workout prescribes no holds. Seconds, beside the
            kilograms and never summed with them.
        coverage: Fraction of :attr:`total_sets` that contributed kilograms.
    """

    volume_load_kg: float | None
    total_sets: int
    total_hold_s: int | None
    coverage: float


def _midpoint(
    target: Target, anchors: Mapping[AnchorType, PinnedAnchor]
) -> float | None:
    """Resolve one power target to a single watt value, or ``None``.

    Ranges reduce to their midpoint (see the module docstring). ``None`` when
    the target is a percentage of an anchor that was not pinned — the step is
    then treated as carrying no power target rather than raising, because a
    prediction is a best effort over a prescription the caller already stored.
    """
    if isinstance(target, AbsoluteRange):
        return (target.low + target.high) / 2
    if isinstance(target, PercentOfAnchor):
        pinned = anchors.get(target.anchor_type)
        if pinned is None:
            return None
        return (target.pct_low + target.pct_high) / 2 * pinned.version.value
    return None


#: Coverage this close to whole is rendered as an inequality rather than
#: rounded, in either direction. One decimal place is as fine as the phrasing
#: goes, and past it a partial coverage would round to a flat ``100%`` or
#: ``0%`` — printing a number that contradicts the assumption standing beside
#: it ("steps with no power target counted as 0 W").
_COVERAGE_EPSILON = 0.001


def _coverage_phrase(coverage: float) -> str:
    """Say what fraction of the duration carried a power target.

    Full coverage is said in words, because ``100%`` is the one percentage a
    reader will take as "all of it" — and it must therefore never appear for a
    prescription that had a second uncovered. Everything short of whole is a
    one-decimal percentage, clamped to ``>99.9%`` and ``<0.1%`` at the edges
    so no partial coverage can round its way to a whole number.
    """
    if coverage >= 1.0:
        return "the full duration carried a power target"
    if coverage > 1.0 - _COVERAGE_EPSILON:
        rendered = ">99.9%"
    elif 0.0 < coverage < _COVERAGE_EPSILON:
        rendered = "<0.1%"
    else:
        rendered = f"{coverage:.1%}"
    return f"{rendered} of the duration carried a power target"


def predict_endurance_load(
    workout: EnduranceWorkout,
    anchors: Mapping[AnchorType, PinnedAnchor],
) -> PredictedLoad | None:
    """Predict the TSS-equivalent cost of an endurance prescription.

    The prescription is expanded to a 1 Hz series of prescribed watts — a
    steady step contributes its duration at its power-target midpoint, a ramp
    linearly interpolates from its start midpoint to its end midpoint — and
    that series goes through the same NP → IF → TSS chain the recorded ride
    will (see the module docstring for why the long way round is the point).
    A step with **no** power target contributes its duration as zero watts and
    does not count toward coverage.

    Args:
        workout: The prescription, exactly as frozen on the planned session.
        anchors: The anchor versions the session pinned, by type, each with
            the id it was pinned by.

    Returns:
        The prediction, or ``None`` when there is nothing honest to say:
        no FTP anchor is pinned, no step carries a power target
        (``coverage == 0``), any step is distance-based (nothing to integrate
        over), or the prescription is longer than
        :data:`MAX_PREDICTABLE_DURATION_S`.
    """
    ftp = anchors.get(AnchorType.FTP)
    if ftp is None or ftp.version.value <= 0:
        return None

    flat_steps = flatten(workout)
    seconds_per_step: list[int] = []
    for flat in flat_steps:
        if flat.duration_s is None:
            return None
        seconds_per_step.append(flat.duration_s)
    total_duration_s = sum(seconds_per_step)
    if total_duration_s > MAX_PREDICTABLE_DURATION_S:
        return None

    watts: list[float] = []
    covered_s = 0
    for flat, seconds in zip(flat_steps, seconds_per_step, strict=True):
        start_target = flat.start_targets.get(Channel.POWER)
        end_target = flat.end_targets.get(Channel.POWER)
        start = None if start_target is None else _midpoint(start_target, anchors)
        end = None if end_target is None else _midpoint(end_target, anchors)
        if start is None or end is None:
            watts.extend([0.0] * seconds)
            continue
        covered_s += seconds
        if start == end:
            watts.extend([start] * seconds)
        elif seconds == 1:
            watts.append((start + end) / 2)
        else:
            span = seconds - 1
            watts.extend(
                start + (end - start) * index / span for index in range(seconds)
            )

    if covered_s == 0:
        return None

    planned_np = normalized_power(watts)
    planned_if = intensity_factor(planned_np, ftp.version.value)
    load = training_load(total_duration_s, planned_if)
    coverage = covered_s / total_duration_s

    assumptions = ["target ranges reduced to their midpoint"]
    if coverage < 1.0:
        assumptions.append(
            "steps with no power target counted as 0 W and left out of coverage"
        )
    explanation = MetricExplanation(
        formula=(
            f"NP = mean(rolling_mean_{NP_WINDOW_S}s(P)^4)^(1/4); "
            "IF = NP / FTP; TSS = duration_s × IF² / 36"
        ),
        inputs=MappingProxyType(
            {
                "FTP": describe_anchor(ftp.version),
                "planned NP": f"{planned_np:.0f} W over the prescribed watts",
                "duration": f"{total_duration_s} s prescribed",
                "coverage": _coverage_phrase(coverage),
            }
        ),
        assumptions=tuple(assumptions),
        citation=_CITATION,
    )
    return PredictedLoad(
        load=load,
        intensity_factor=planned_if,
        duration_s=total_duration_s,
        anchor_version_id=ftp.version_id,
        coverage=coverage,
        explanation=explanation,
    )


def predict_strength_volume(workout: StrengthWorkout) -> PredictedVolume:
    """Sum the prescribed volume load of a strength workout.

    Volume load is ``working_sets × reps × kg``, so only rep-based sets
    prescribed in kilograms contribute: a %e1RM line has no kilograms until
    the e1RM is known, an RPE line has none until it is performed, a
    bodyweight line has none at all, and a **timed hold has no reps to
    multiply** — it contributes to :attr:`PredictedVolume.total_hold_s`
    instead, on the same reasoning that keeps kilograms off the endurance load
    axis. :attr:`PredictedVolume.coverage` says how much of the session that
    leaves out, and :attr:`PredictedVolume.total_sets` counts every working
    set regardless — a strength session with no volume load is still work.

    **Working sets, not rounds.** Issue #25's case, by number: three rounds of
    eleven single-arm reps at 15 kg is six working sets and 990 kg, not three
    and 495.

    This is kilograms and it is **not** a load: see the module docstring.
    """
    total_sets = 0
    counted_sets = 0
    volume = 0.0
    hold_s = 0
    timed = False
    for prescription in workout.prescriptions:
        working = prescription.working_sets
        total_sets += working
        if prescription.duration_s is not None:
            timed = True
            hold_s += working * prescription.duration_s
            continue
        load = prescription.load
        if load.kind is LoadKind.KG and load.value is not None and prescription.reps:
            counted_sets += working
            volume += working * prescription.reps * load.value
    return PredictedVolume(
        volume_load_kg=volume if counted_sets else None,
        total_sets=total_sets,
        total_hold_s=hold_s if timed else None,
        coverage=counted_sets / total_sets if total_sets else 0.0,
    )
