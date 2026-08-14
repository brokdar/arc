"""Putting a prescription and a recording on the same timeline.

Two halves, deliberately separate because only one of them needs a plan.

**Detection** (:func:`detect_work_intervals`) reads the recording alone. A
smoothed power series crossing a threshold is a work interval; the result is a
list of ``[start_index, end_index)`` ranges on the 1 Hz grid with their own
statistics. It is deterministic from the stream — no anchors, no prescription,
no athlete input — which is why WP-5 stores it *with the metrics*: the
intervals table renders it, WP-6's structure hint counts it, and WP-7's pacing
axis reads it, and none of the three should re-derive it differently.

**Alignment** (:func:`align`) is the join. Detected intervals are assigned to
the prescription's *work* steps, in order, by dynamic programming over how
well their durations and intensities agree. Every pair carries an
``alignment_confidence`` in ``[0, 1]``; pairs below
:data:`CONFIDENCE_FLOOR` are excluded with the reason
:data:`LOW_CONFIDENCE_REASON`, because a scoring axis fed a wrong pairing
produces a *confident* wrong answer, which is worse than no answer.

**The offset is a real input** (A7.1). The most common alignment failure is a
constant offset — the athlete started recording before starting the workout,
or the warm-up ran long — and it mis-aligns everything after it. ``offset_s``
slides the planned timeline before the assignment is made and travels back out
on the result, so the control WP-7 puts on the screen changes the answer
rather than the picture.

Nothing here persists anything: an alignment describes a *match*, and matches
do not exist until WP-6. The tests are the only consumer today.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.workout import FlatStep, StepRole

#: Window the power series is smoothed over before the threshold is applied,
#: in seconds (build plan WP-5.2). Long enough that a single surge out of a
#: corner is not an interval, short enough that a 30 s effort survives.
SMOOTHING_S = 10

#: Shortest run above the threshold that counts as a work interval.
#: Below it the "interval" is a hill, a sprint out of a junction or a gear
#: change, and an intervals table full of them is unreadable.
MIN_INTERVAL_S = 30

#: Confidence at or above which an aligned pair may be scored (WP-5.2).
CONFIDENCE_FLOOR = 0.5

#: Why a pair was aligned but not kept. The exact string WP-7 reports as the
#: reason an adherence axis is `not_assessed` for that step.
LOW_CONFIDENCE_REASON = "alignment_low_confidence"

#: How confidence splits between the two things that can disagree. Duration
#: leads because it is measured the same way on both sides; intensity is a
#: prescribed midpoint against a recorded average and is the noisier signal.
DURATION_WEIGHT = 0.7
INTENSITY_WEIGHT = 0.3

#: How far apart a planned start and a detected start may be before proximity
#: stops contributing to the assignment, in seconds.
PROXIMITY_TOLERANCE_S = 600

#: Weight of the proximity term in the **assignment** objective. It is not
#: part of the reported confidence — see :func:`align`.
PROXIMITY_WEIGHT = 0.25

#: Percentiles the default detection threshold is taken between.
THRESHOLD_LOW_PERCENTILE = 25
THRESHOLD_HIGH_PERCENTILE = 90

#: The default threshold is never below this multiple of the overall average,
#: so a perfectly steady ride does not report itself as one long interval.
THRESHOLD_FLOOR_FACTOR = 1.05


@dataclass(frozen=True, slots=True)
class WorkInterval:
    """One detected effort, addressed by row on the 1 Hz grid.

    Args:
        start_index: First row of the effort.
        end_index: One past the last — ``[start, end)``, the convention every
            other range in this codebase uses.
        duration_s: ``end_index - start_index``; on a 1 Hz grid the row count
            *is* the duration.
        average_power: Mean of the cleaned power readings inside the range.
        max_power: Largest cleaned power reading inside it.
        average_hr: Mean heart rate inside it, when heart rate was recorded.
    """

    start_index: int
    end_index: int
    duration_s: int
    average_power: float | None
    max_power: float | None
    average_hr: float | None


def smooth(values: Sequence[float | None], window_s: int) -> list[float | None]:
    """Centred moving average over the readings a window actually holds.

    **Centred**, unlike `app.domain.metrics.normalized_power`'s trailing
    window, and for a different purpose: NP's trailing window models a
    physiological lag that really does trail the effort, while a detector that
    trails puts every interval boundary five seconds late and makes the
    intervals table disagree with the chart the athlete is looking at.

    Rows with no reading in the whole window stay ``None`` — a recording stop
    is not a period of low power, and smoothing across one would invent an
    effort ramping down into it.
    """
    if window_s < 1:
        raise ValueError(f"window_s must be at least 1, got {window_s}")
    half = window_s // 2
    smoothed: list[float | None] = []
    for index in range(len(values)):
        window = [
            value
            for value in values[max(0, index - half) : index + half + 1]
            if value is not None
        ]
        smoothed.append(sum(window) / len(window) if window else None)
    return smoothed


def default_threshold(smoothed: Sequence[float | None]) -> float | None:
    """The threshold used when the caller names none.

    The midpoint of the series' 25th and 90th percentiles, floored at
    :data:`THRESHOLD_FLOOR_FACTOR` times its overall average. The percentile
    pair straddles the gap between "riding along" and "working": the 25th sits
    inside recovery, the 90th inside the efforts, and their midpoint lands in
    the empty space between the two modes of an interval session's power
    histogram. The floor is what stops a steady endurance ride — which has no
    such gap — from reporting its whole length as one interval.

    Deterministic and anchor-free on purpose: detection has to give the same
    answer before an FTP exists as after one changes, or the intervals table
    would rewrite itself when an unrelated anchor is appended.

    ``None`` when the series holds fewer than two readings, which is not a
    distribution to take percentiles of.
    """
    present = [value for value in smoothed if value is not None]
    if len(present) < 2:
        return None
    cuts = statistics.quantiles(present, n=100, method="inclusive")
    midpoint = (
        cuts[THRESHOLD_LOW_PERCENTILE - 1] + cuts[THRESHOLD_HIGH_PERCENTILE - 1]
    ) / 2
    floor = THRESHOLD_FLOOR_FACTOR * (sum(present) / len(present))
    return max(midpoint, floor)


def detect_work_intervals(
    power_fixed: Sequence[float | None],
    *,
    hr_fixed: Sequence[float | None] | None = None,
    smoothing_s: int = SMOOTHING_S,
    threshold: float | None = None,
    min_duration_s: int = MIN_INTERVAL_S,
) -> list[WorkInterval]:
    """Find the work intervals in a recording by threshold crossing.

    The cleaned power column is smoothed over ``smoothing_s`` seconds and the
    maximal runs at or above ``threshold`` become intervals; runs shorter than
    ``min_duration_s`` are dropped as noise. Statistics are taken over the
    **unsmoothed** cleaned columns, so an interval's average power is the
    average of what was recorded inside it rather than of a filtered version
    of it.

    Args:
        power_fixed: The cleaned power column, nulls included.
        hr_fixed: The cleaned heart-rate column, for per-interval average HR.
        smoothing_s: Width of the detection filter.
        threshold: Watts at or above which the athlete is working; derived by
            :func:`default_threshold` when omitted.
        min_duration_s: Shortest run kept.

    Returns:
        The intervals in time order. Empty when there is no power column, when
        the series is too short to have a threshold, or when nothing crossed
        it — a steady ride genuinely has no intervals, and inventing one would
        put a row in the table for every ride ever recorded.

    Note:
        With a **derived** threshold the detected duration is not guaranteed
        monotonic in the true duration. ``default_threshold`` reads the smoothed
        series, so a longer effort raises the level it is then measured against,
        and a one-second-longer block can detect one second shorter. On a real
        recording the threshold reflects the whole ride and one interval barely
        moves it; on a short synthetic series where the effort dominates, it
        moves a lot. Pass an explicit ``threshold`` whenever you are comparing
        detected durations across series that differ only in that effort —
        otherwise the detector varies with the thing being measured. See
        ``test_confidence_falls_as_the_duration_drifts``.
    """
    if not any(value is not None for value in power_fixed):
        return []
    smoothed = smooth(power_fixed, smoothing_s)
    level = threshold if threshold is not None else default_threshold(smoothed)
    if level is None:
        return []

    intervals: list[WorkInterval] = []
    start: int | None = None
    for index, value in enumerate([*smoothed, None]):
        working = value is not None and value >= level
        if working and start is None:
            start = index
        elif not working and start is not None:
            if index - start >= min_duration_s:
                intervals.append(_interval(start, index, power_fixed, hr_fixed))
            start = None
    return intervals


def _mean(values: Sequence[float | None]) -> float | None:
    """Mean of the readings present, or ``None`` when there are none."""
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _interval(
    start: int,
    end: int,
    power_fixed: Sequence[float | None],
    hr_fixed: Sequence[float | None] | None,
) -> WorkInterval:
    """Build one interval's statistics over the unsmoothed columns."""
    watts = [value for value in power_fixed[start:end] if value is not None]
    return WorkInterval(
        start_index=start,
        end_index=end,
        duration_s=end - start,
        average_power=sum(watts) / len(watts) if watts else None,
        max_power=max(watts) if watts else None,
        average_hr=_mean(hr_fixed[start:end]) if hr_fixed is not None else None,
    )


# --- alignment ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlignedStep:
    """One planned work step paired with one detected interval.

    Args:
        step_index: `app.domain.workout.FlatStep.index` of the planned step.
        interval_index: Position of the detected interval in the list handed
            to :func:`align`.
        confidence: How well the two agree, in ``[0, 1]``.
    """

    step_index: int
    interval_index: int
    confidence: float


@dataclass(frozen=True, slots=True)
class ExcludedStep:
    """A pair the assignment made and the confidence gate then refused.

    Kept rather than dropped: "we matched this step to that effort and did not
    trust the match" is a different thing to tell the athlete than "this step
    was never performed", and WP-7 reports :attr:`reason` as the reason its
    adherence axis is not assessed.
    """

    step_index: int
    interval_index: int
    confidence: float
    reason: str = LOW_CONFIDENCE_REASON


@dataclass(frozen=True, slots=True)
class Alignment:
    """The assignment of detected intervals to planned work steps.

    Args:
        offset_s: The offset the planned timeline was slid by before the
            assignment was made. Part of the result because WP-6 stores it and
            changing it is what creates a new alignment version (A7.1).
        aligned: Pairs at or above :data:`CONFIDENCE_FLOOR`, in step order.
        excluded: Pairs below it, in step order.
        unmatched_steps: Planned work steps no interval was assigned to.
        unmatched_intervals: Detected intervals no planned step claimed —
            extra efforts, or a warm-up ridden hard enough to cross the
            threshold.
    """

    offset_s: int
    aligned: tuple[AlignedStep, ...]
    excluded: tuple[ExcludedStep, ...]
    unmatched_steps: tuple[int, ...]
    unmatched_intervals: tuple[int, ...]


def alignment_to_json(alignment: Alignment) -> dict[str, object]:
    """Render an alignment for storage and for the API (WP-7 persists it).

    Flat and lossless: every pair the assignment made is here, kept or
    excluded, so the stored version explains a score without the recording
    having to be read again.
    """
    return {
        "offset_s": alignment.offset_s,
        "aligned": [
            {
                "step_index": pair.step_index,
                "interval_index": pair.interval_index,
                "confidence": pair.confidence,
            }
            for pair in alignment.aligned
        ],
        "excluded": [
            {
                "step_index": pair.step_index,
                "interval_index": pair.interval_index,
                "confidence": pair.confidence,
                "reason": pair.reason,
            }
            for pair in alignment.excluded
        ],
        "unmatched_steps": list(alignment.unmatched_steps),
        "unmatched_intervals": list(alignment.unmatched_intervals),
    }


@dataclass(frozen=True, slots=True)
class _Planned:
    """One alignable planned work step, placed on the planned timeline."""

    step_index: int
    start_s: int
    duration_s: int
    target_watts: float | None


def _ratio(left: float, right: float) -> float:
    """``min / max`` of two positive quantities: 1.0 when they agree.

    Monotonically non-increasing as either moves away from the other, which is
    the property the confidence gate depends on and the property tests pin.
    """
    if left <= 0 or right <= 0:
        return 0.0
    return min(left, right) / max(left, right)


def _confidence(planned: _Planned, interval: WorkInterval) -> float:
    """How much a planned step and a detected interval agree, in ``[0, 1]``.

    ::

        duration  = min(planned_s, detected_s) / max(planned_s, detected_s)
        intensity = min(target_W, detected_W) / max(target_W, detected_W)
        confidence = 0.7 × duration + 0.3 × intensity

    When the step prescribes no power target, or the interval recorded no
    power, there is no intensity term and confidence is the duration ratio
    alone — reweighting a missing input to zero would make an unprescribed
    step look 30 % worse than an identical prescribed one.
    """
    duration = _ratio(planned.duration_s, interval.duration_s)
    if planned.target_watts is None or interval.average_power is None:
        return duration
    intensity = _ratio(planned.target_watts, interval.average_power)
    return DURATION_WEIGHT * duration + INTENSITY_WEIGHT * intensity


def _proximity(planned: _Planned, interval: WorkInterval, offset_s: int) -> float:
    """How close the two starts are once the offset is applied, in ``[0, 1]``.

    This is where ``offset_s`` earns its keep. It is **not** part of the
    reported confidence — a step performed on time but at the wrong duration
    should not read as a good match — but it is part of the assignment
    objective, so when several planned steps are interchangeable by duration
    (``5 × 5 min``), the offset decides which detected effort is which.
    """
    apart = abs((planned.start_s + offset_s) - interval.start_index)
    return max(0.0, 1.0 - apart / PROXIMITY_TOLERANCE_S)


def _planned_work_steps(
    planned_steps: Sequence[FlatStep], target_watts: Mapping[int, float] | None
) -> list[_Planned]:
    """Place the work steps on the planned timeline, in order.

    The timeline cumulates **every** step's duration, not just the work ones:
    a warm-up is what puts the first interval where it is. A distance-based
    step has no duration to cumulate and no duration to compare, so it is left
    out entirely rather than counted as zero — which would drag every step
    after it earlier on the timeline.
    """
    elapsed = 0
    work: list[_Planned] = []
    for step in planned_steps:
        duration = step.duration_s
        if duration is None:
            continue
        if step.role is StepRole.WORK:
            work.append(
                _Planned(
                    step_index=step.index,
                    start_s=elapsed,
                    duration_s=duration,
                    target_watts=(target_watts or {}).get(step.index),
                )
            )
        elapsed += duration
    return work


def align(
    planned_steps: Sequence[FlatStep],
    detected: Sequence[WorkInterval],
    *,
    offset_s: int = 0,
    target_watts: Mapping[int, float] | None = None,
) -> Alignment:
    """Assign detected intervals to planned work steps, in order (WP-5.2).

    Order-preserving: if planned step *a* comes before *b*, the interval
    assigned to *a* comes before the one assigned to *b*. That is not a
    simplification but the semantics — a workout is a sequence, and an
    assignment that crosses would be claiming the athlete performed step 4
    before step 3.

    The assignment maximises ``Σ (confidence + 0.25 × proximity)`` over
    order-preserving matchings, by the standard sequence-alignment dynamic
    program: at each cell, pair the two, skip the planned step, or skip the
    detected interval. Pairing is only ever considered when it scores above
    zero, so a step and an interval with nothing in common are left unmatched
    rather than paired for want of an alternative.

    Args:
        planned_steps: The flattened prescription — every step, not only the
            work ones; the others place the work steps in time.
        detected: :func:`detect_work_intervals`' output for the recording.
        offset_s: Seconds to slide the planned timeline by before assigning.
            Positive means the workout began *later* than the recording did,
            which is the ordinary case (A7.1).
        target_watts: Prescribed watts per flat-step index, resolved by the
            caller against the session's pinned anchors. Alignment never
            resolves an anchor itself — that is how the frozen prescription
            stays frozen.

    Returns:
        The assignment, with every pair either kept or excluded by the
        confidence gate, and both sides' leftovers named.
    """
    work = _planned_work_steps(planned_steps, target_watts)
    rows, columns = len(work), len(detected)
    # `best[i][j]`: the best objective over the first i steps and first j
    # intervals. `move` records which of the three transitions produced it so
    # the pairing can be read back out.
    best = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    move = [[""] * (columns + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            confidence = _confidence(work[i - 1], detected[j - 1])
            pair = (
                best[i - 1][j - 1]
                + confidence
                + PROXIMITY_WEIGHT * _proximity(work[i - 1], detected[j - 1], offset_s)
            )
            options = [(best[i - 1][j], "step"), (best[i][j - 1], "interval")]
            if confidence > 0:
                options.append((pair, "pair"))
            best[i][j], move[i][j] = max(options)

    pairs: list[tuple[int, int, float]] = []
    i, j = rows, columns
    while i > 0 and j > 0:
        match move[i][j]:
            case "pair":
                pairs.append((i - 1, j - 1, _confidence(work[i - 1], detected[j - 1])))
                i, j = i - 1, j - 1
            case "step":
                i -= 1
            case _:
                j -= 1
    pairs.reverse()

    aligned = tuple(
        AlignedStep(
            step_index=work[step].step_index,
            interval_index=interval,
            confidence=confidence,
        )
        for step, interval, confidence in pairs
        if confidence >= CONFIDENCE_FLOOR
    )
    excluded = tuple(
        ExcludedStep(
            step_index=work[step].step_index,
            interval_index=interval,
            confidence=confidence,
        )
        for step, interval, confidence in pairs
        if confidence < CONFIDENCE_FLOOR
    )
    claimed_steps = {step for step, _, _ in pairs}
    claimed_intervals = {interval for _, interval, _ in pairs}
    return Alignment(
        offset_s=offset_s,
        aligned=aligned,
        excluded=excluded,
        unmatched_steps=tuple(
            planned.step_index
            for index, planned in enumerate(work)
            if index not in claimed_steps
        ),
        unmatched_intervals=tuple(
            index for index in range(columns) if index not in claimed_intervals
        ),
    )


# --- strength: no timeline, so no dynamic programming -------------------------


@dataclass(frozen=True, slots=True)
class SetPair:
    """One prescribed set paired with the logged set that answered it."""

    prescribed_index: int
    performed_index: int


@dataclass(frozen=True, slots=True)
class StrengthAlignment:
    """Prescribed sets against logged sets, paired by position.

    Args:
        pairs: Positional pairs, in order.
        unmatched_prescribed: Sets prescribed and not logged — the sets that
            were skipped, or the ones the athlete stopped short of.
        unmatched_performed: Sets logged beyond the prescription.
    """

    pairs: tuple[SetPair, ...]
    unmatched_prescribed: tuple[int, ...]
    unmatched_performed: tuple[int, ...]


def align_strength(
    prescribed: Sequence[object], performed: Sequence[object]
) -> StrengthAlignment:
    """Pair logged sets to prescribed sets by index (build plan WP-5.2).

    The alignment unit for strength is the **set list**, not a timeline: the
    logged sets carry no start times worth aligning on, and an athlete who
    does the prescribed work in a different order still did the prescribed
    work. Positional pairing is therefore the whole algorithm, and the
    leftovers on either side are what WP-7's completion axis reads.
    """
    shared = min(len(prescribed), len(performed))
    return StrengthAlignment(
        pairs=tuple(
            SetPair(prescribed_index=index, performed_index=index)
            for index in range(shared)
        ),
        unmatched_prescribed=tuple(range(shared, len(prescribed))),
        unmatched_performed=tuple(range(shared, len(performed))),
    )
