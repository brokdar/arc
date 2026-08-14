"""What is normal *for this athlete*, and how much the readings can bear.

`resting_hr: 54` is not a fact anyone can act on: fifty-four is alarming for
one athlete and a Tuesday for another. This module turns a stored series into
the one thing that makes a reading interpretable — the athlete's own rolling
baseline, its normal band, and today's distance from it in standard deviations
— together with an honest statement of whether the series is long enough to
support any of that.

**Baselines are computed per read and never stored.** A stored baseline is a
cache, and `app.domain.wellness` makes a day *corrigible* — the athlete who
typed 6.5 h of sleep as 65 can fix it — so a cached baseline is stale the
moment a day is corrected, and every correction path would have to remember to
invalidate it. A 60-day window over a unique, indexed ``local_date`` for one
athlete is a single index scan; nothing here is worth a table, an invalidation
rule or a migration. **This is why the PR that introduced this module owns no
Alembic revision.**

**Nothing here is a verdict.** The readiness projection counts how many markers
sit outside their own band and names them with a direction; it does not add
them up, weight them, or say whether today is a day to train. That line is the
same one `app.domain.wellness` abstains at and is enforced from outside by an
import-linter contract forbidding the proposal and guardrail modules from
importing either module.

**Abstention beats a caveat.** A baseline that is not mature reports *why*, in
counts, with its own unlock condition — and carries no ``mean``, no ``band``
and no ``deviation_sd`` at all rather than a number with a warning attached. A
caveat is advice a model under pressure to be helpful will drop; a missing key
is not.

Two conventions from `.claude/rules/backend-domain-units.md` bind every number
below: a percentage is a fraction (SpO2 is ``0.97``), and every range is
half-open ``[start, end)``.
"""

import datetime as dt
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.wellness import (
    PREFERRED_HRV_CONTEXT,
    HrvContext,
    HrvMetric,
    MarkerStanding,
    WellnessDay,
)

#: How far back a baseline looks. Sixty days is long enough for a training
#: block's worth of variation and short enough that last season's fitness is
#: not in it — the window the HRV-monitoring literature builds rolling
#: references over.
BASELINE_WINDOW_DAYS = 60

#: Fewest readings a baseline may be computed from. The failure this exists to
#: prevent is a coach saying "your HRV is trending down" from nine readings.
MIN_BASELINE_READINGS = 14

#: Fewest days the readings must *span*. The count alone is not enough: twenty
#: readings crammed into ten days describe ten days, and a mean over them will
#: move the moment the athlete's week changes shape. Four weeks is the shortest
#: span that has seen a full training microcycle.
MIN_BASELINE_SPAN_DAYS = 28

#: The rolling window today is judged against. Seven days rather than one,
#: because a single bad night read as a trend is the specific over-claim this
#: whole surface exists to prevent — a 3 SD spike today can move a seven-day
#: mean by three sevenths of an SD and no more.
ROLLING_MEAN_DAYS = 7

#: The smallest-worthwhile-change multiplier. See :class:`Band`.
SWC_FRACTION = 0.5


class MarkerKind(StrEnum):
    """Whether a marker is a device reading or the athlete's own report.

    The split decides what a late entry means: a watch measured the objective
    markers on the day, whatever day they were typed in, while nobody
    accurately recalls last month's Tuesday motivation
    (`app.domain.wellness.is_late_entry`).
    """

    OBJECTIVE = "objective"
    SUBJECTIVE = "subjective"


class Space(StrEnum):
    """The space a marker's statistics are computed and reported in.

    **Only HRV is log-transformed.** RMSSD is right-skewed and multiplicative —
    a 10 ms drop from 90 and from 30 are not the same event — so its mean, SD,
    band and deviation all live in ``ln`` space, which is where the HRV
    literature states them. Everything else is linear.

    The space is *reported*, and every statistic on an object is in it, so
    ``deviation_sd == (rolling_mean_7d - mean) / sd`` holds on the numbers a
    reader actually has. Native-unit twins (``mean_native``, ``low_native``)
    are carried beside them for the chart, and raw readings are always native.
    """

    LINEAR = "linear"
    LN = "ln"


class Direction(StrEnum):
    """Where the rolling mean sits relative to the band. Never a valence.

    ``above`` and ``below`` are geometry, not judgement: a resting heart rate
    above its band and an HRV above its band are opposite news, and a reader
    that assumed one direction was good would be wrong half the time. The
    polarity table in `app.domain.wellness` is where direction acquires
    meaning, and it does so in the reader, not here.
    """

    BELOW = "below"
    WITHIN = "within"
    ABOVE = "above"


class WellnessMetric(StrEnum):
    """Every metric a trend read may ask for, as a closed vocabulary.

    An enum rather than a free string because it is a **query parameter**: a
    published contract that admits any string advertises answers that do not
    exist, and the caller discovers the real list by submitting guesses and
    reading the refusals — the failure `get_wellness_inputs` exists to prevent
    on the write side.

    Mostly one member per model column. HRV is the exception and has two, one
    per statistic: RMSSD and SDNN are not on one scale, so a metric name is
    allowed to assert the statistic where the *column* name is not.
    """

    RESTING_HR_BPM = "resting_hr_bpm"
    HRV_RMSSD_MS = "hrv_rmssd_ms"
    HRV_SDNN_MS = "hrv_sdnn_ms"
    RESPIRATORY_RATE_BRPM = "respiratory_rate_brpm"
    WRIST_TEMPERATURE_DELTA_C = "wrist_temperature_delta_c"
    SPO2 = "spo2"
    SLEEP_DURATION_S = "sleep_duration_s"
    WEIGHT_KG = "weight_kg"
    SLEEP_QUALITY = "sleep_quality"
    FATIGUE = "fatigue"
    SORENESS = "soreness"
    STRESS = "stress"
    MOTIVATION = "motivation"


@dataclass(frozen=True, slots=True)
class Marker:
    """One thing a baseline can be computed over, as **table data**.

    The baseline function is generic over the marker and the per-marker
    differences live in :data:`MARKERS` rather than in five code paths: the
    alternative is five copies of the maturity rule that drift the first time
    one of them is corrected. Adding a column to the model without an entry
    here fails :func:`unmarked_fields`.
    """

    #: The name this marker is asked for and answered under. Equal to the
    #: model's column except for HRV, where the statistic is part of what the
    #: number *is* and the metric name says which one it is.
    metric: WellnessMetric
    #: The attribute on :class:`app.domain.wellness.WellnessDay`.
    field: str
    kind: MarkerKind
    #: The unit the raw readings are in, for a reader that has only this.
    unit: str
    #: Whether a normal band and an SD deviation are meaningful for it. False
    #: for body weight, which moves on a scale of weeks — a daily SD deviation
    #: from a weight baseline is a statement nobody should make — and for the
    #: subjective 1-5 ratings, where an SD over five ordinal points is
    #: arithmetic dressed as precision.
    banded: bool = True
    space: Space = Space.LINEAR
    #: For HRV only: the statistic this metric reports on. RMSSD and SDNN are
    #: different statistics over the same beat intervals and are not on one
    #: scale, so they are two metrics rather than one.
    hrv_metric: HrvMetric | None = None

    @property
    def by_context(self) -> bool:
        """Whether baselines split by :class:`HrvContext`. HRV only."""
        return self.hrv_metric is not None


#: Every marker, in read order. Objective markers first, weight after them, the
#: subjective ratings last — the order the wellness form asks in.
#:
#: `hrv_rmssd_ms` and `hrv_sdnn_ms` are two metrics over one column
#: (`wellness_days.hrv_ms` plus its `hrv_metric` discriminator), because a mean
#: pooling RMSSD and SDNN is not noisy, it is meaningless. The metric name is
#: allowed to assert the statistic where the *column* name is not: a metric is
#: a selection over the discriminator, so `hrv_rmssd_ms` returns RMSSD readings
#: or it returns nothing.
MARKERS: tuple[Marker, ...] = (
    Marker(
        metric=WellnessMetric.RESTING_HR_BPM,
        field="resting_hr_bpm",
        kind=MarkerKind.OBJECTIVE,
        unit="bpm",
    ),
    Marker(
        metric=WellnessMetric.HRV_RMSSD_MS,
        field="hrv_ms",
        kind=MarkerKind.OBJECTIVE,
        unit="ms",
        space=Space.LN,
        hrv_metric=HrvMetric.RMSSD,
    ),
    Marker(
        metric=WellnessMetric.HRV_SDNN_MS,
        field="hrv_ms",
        kind=MarkerKind.OBJECTIVE,
        unit="ms",
        space=Space.LN,
        hrv_metric=HrvMetric.SDNN,
    ),
    Marker(
        metric=WellnessMetric.RESPIRATORY_RATE_BRPM,
        field="respiratory_rate_brpm",
        kind=MarkerKind.OBJECTIVE,
        unit="brpm",
    ),
    Marker(
        metric=WellnessMetric.WRIST_TEMPERATURE_DELTA_C,
        field="wrist_temperature_delta_c",
        kind=MarkerKind.OBJECTIVE,
        unit="C",
    ),
    #: A fraction, never a percentage — see the units rule.
    Marker(
        metric=WellnessMetric.SPO2,
        field="spo2",
        kind=MarkerKind.OBJECTIVE,
        unit="fraction",
    ),
    Marker(
        metric=WellnessMetric.SLEEP_DURATION_S,
        field="sleep_duration_s",
        kind=MarkerKind.OBJECTIVE,
        unit="s",
    ),
    Marker(
        metric=WellnessMetric.WEIGHT_KG,
        field="weight_kg",
        kind=MarkerKind.OBJECTIVE,
        unit="kg",
        banded=False,
    ),
    *(
        Marker(
            metric=member,
            field=member.value,
            kind=MarkerKind.SUBJECTIVE,
            unit="1-5",
            banded=False,
        )
        for member in (
            WellnessMetric.SLEEP_QUALITY,
            WellnessMetric.FATIGUE,
            WellnessMetric.SORENESS,
            WellnessMetric.STRESS,
            WellnessMetric.MOTIVATION,
        )
    ),
)

#: Keyed by the metric's **string** value, so a caller holding a query
#: parameter and a caller holding an enum member reach the same marker.
MARKERS_BY_METRIC: Mapping[str, Marker] = {
    marker.metric.value: marker for marker in MARKERS
}

#: The markers the readiness projection counts. Exactly the five objective
#: markers a watch measures overnight and whose deviation from an athlete's own
#: normal is the morning question — not sleep duration (a behaviour, not a
#: physiological deviation) and not weight (which has no daily band).
READINESS_MARKERS: tuple[WellnessMetric, ...] = (
    WellnessMetric.RESTING_HR_BPM,
    WellnessMetric.HRV_RMSSD_MS,
    WellnessMetric.RESPIRATORY_RATE_BRPM,
    WellnessMetric.WRIST_TEMPERATURE_DELTA_C,
    WellnessMetric.SPO2,
)


def unmarked_fields(fields: Iterable[str]) -> tuple[str, ...]:
    """Which of ``fields`` no marker in :data:`MARKERS` describes.

    The completeness check `test_baseline_marker_completeness` runs, taking the
    fields as an argument so the test can prove the check itself fails on an
    unmarked one. A column added to the model without a marker would otherwise
    simply never appear on any read — a silent omission, which is the failure
    mode a table of data has that five code paths do not.
    """
    described = {marker.field for marker in MARKERS}
    return tuple(name for name in fields if name not in described)


@dataclass(frozen=True, slots=True)
class DaySample:
    """One recorded day, with the one fact the row itself cannot answer.

    ``subjective_recalled`` is resolved by the service from ``local_date`` and
    ``created_at`` (`app.domain.wellness.is_late_entry`) because the domain has
    no clock and no timezone. It gates the *subjective* markers only: the watch
    measured the objective ones on the day, whatever day they were typed in, so
    an imported watch history matures HRV and resting HR at full weight while
    the same import matures no subjective baseline at all.
    """

    day: WellnessDay
    subjective_recalled: bool = False


@dataclass(frozen=True, slots=True)
class Reading:
    """One marker's value on one day, with everything that gates it."""

    local_date: dt.date
    #: The reading in its native unit, exactly as stored.
    value: float
    #: The value in the marker's analysis space — ``ln(value)`` for HRV.
    analysed: float
    standing: MarkerStanding
    subjective_recalled: bool
    hrv_context: HrvContext | None


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One date of the requested range, reading or gap.

    A date with no reading carries ``value=None`` and ``standing=None``. It is
    never zero and never interpolated from its neighbours: the gap is the
    honest picture, the same rule the session stream charts hold to.
    """

    local_date: dt.date
    value: float | None
    #: The day's marker standing, or None when nothing was recorded. It rides
    #: on the **same object as the reading** — a coach that has to look
    #: elsewhere for last night's beer will one day not look.
    standing: MarkerStanding | None


@dataclass(frozen=True, slots=True)
class Count:
    """A count against the bar it has to clear: ``11 of 14``."""

    have: int
    need: int

    def __str__(self) -> str:
        """``11 of 14`` — the phrasing an abstention's reason is built from."""
        return f"{self.have} of {self.need}"

    @property
    def met(self) -> bool:
        """Whether the bar is cleared."""
        return self.have >= self.need


@dataclass(frozen=True, slots=True)
class Abstention:
    """No baseline yet, and exactly what it would take to have one.

    Deliberately **not** a :class:`Baseline` with null statistics: it carries
    no ``mean``, no ``sd``, no ``cv``, no ``band`` and no ``deviation_sd``
    attribute at all. A null mean is a number somebody eventually reads as
    zero, and a caveat beside a number is advice a model under pressure to be
    helpful will drop. What it does carry is its own unlock condition, in both
    counts, so "not enough data" can be acted on rather than merely regretted.
    """

    metric: WellnessMetric
    #: Named when this abstention is about one HRV context.
    hrv_context: HrvContext | None
    readings: Count
    span_days: Count

    @property
    def reason(self) -> str:
        """One line naming both counts, for a reader that has only prose."""
        return (
            f"{self.readings} readings over {self.span_days} days: a baseline "
            f"needs {self.readings.need} readings spanning "
            f"{self.span_days.need} days"
        )


@dataclass(frozen=True, slots=True)
class Band:
    """The athlete's own normal range for a marker: the smallest worthwhile change.

    **The band is ``0.5 x CV``, not ``+-1 SD``.** One SD is a description of
    the spread; the smallest worthwhile change is the smallest movement that
    means anything, and for HRV monitoring the literature settles on half the
    within-athlete coefficient of variation of **ln(RMSSD)** (Plews, Laursen,
    Stanley, Kilding & Buchheit, *Sports Medicine* 43(9), 2013; Plews et al.,
    *IJSPP* 8(6), 2013). A +-1 SD band was invented, is roughly twice as wide,
    and would call a genuine parasympathetic withdrawal "normal".

    ``0.5 x CV`` of the mean and ``0.5 x SD`` are the same number — ``CV`` is
    ``SD / mean`` — and it is computed the first way here so the arithmetic
    matches the literature it is quoted from. ``low``/``high`` are in the
    marker's analysis space; ``low_native``/``high_native`` are the same edges
    in the unit a chart draws.
    """

    low: float
    high: float
    half_width: float
    low_native: float
    high_native: float


@dataclass(frozen=True, slots=True)
class Slope:
    """A least-squares trend through the baseline window."""

    per_day: float
    per_week: float
    n: int


@dataclass(frozen=True, slots=True)
class RollingMean:
    """The trailing seven-day mean, with the ``n`` it was computed over.

    The ``n`` is not decoration. A seven-day mean over three readings and one
    over seven are different objects, and a reader comparing them without
    knowing which is which is being misled by arithmetic that looks identical.
    ``mean`` is None exactly when ``n`` is zero.
    """

    mean: float | None
    mean_native: float | None
    n: int


@dataclass(frozen=True, slots=True)
class Baseline:
    """A mature baseline: what normal is, how wide it is, and where today sits.

    Every statistic is in :attr:`space`, so
    ``deviation_sd == (rolling_mean_7d - mean) / sd`` holds on the numbers as
    reported. ``*_native`` twins carry the same figures in the marker's own
    unit for a chart; for a linear marker the two are identical.
    """

    metric: WellnessMetric
    hrv_context: HrvContext | None
    space: Space
    unit: str
    #: Readings the baseline was computed over, **after** exclusions. A
    #: confounder-invalidated day and a recalled subjective rating are not in
    #: it, which is what makes a thin ``n`` a visible reason rather than a
    #: mystery.
    n: int
    #: First to last reading, inclusive, in days.
    span_days: int
    mean: float
    mean_native: float
    sd: float
    #: ``sd / mean``. None when the mean is zero, where a coefficient of
    #: variation is undefined rather than infinite.
    cv: float | None
    trend: Slope
    #: None for an unbanded marker (body weight, the subjective ratings).
    band: Band | None
    #: The trailing seven-day mean's distance from :attr:`mean`, in SDs.
    #: **Never today against yesterday.** None for an unbanded marker, when
    #: there is no reading in the seven-day window, and in the degenerate case
    #: of a zero SD with a non-zero difference, where a distance in SD units is
    #: undefined rather than infinite.
    deviation_sd: float | None
    direction: Direction | None


@dataclass(frozen=True, slots=True)
class MetricTrend:
    """One metric over the requested range: the readings, and what they mean."""

    metric: WellnessMetric
    unit: str
    space: Space
    #: One entry per date in the requested range, oldest first, gaps included
    #: as explicit nulls.
    series: tuple[SeriesPoint, ...]
    #: The reading on the anchor date in its **native** unit, or None. Distinct
    #: from :attr:`rolling_mean_7d` on purpose: conflating them is how one
    #: night becomes a trend.
    today: float | None
    rolling_mean_7d: RollingMean
    baseline: Baseline | Abstention
    #: HRV only: one baseline or abstention per context that has readings.
    #: Contexts with no readings are simply absent — never a pooled mean, which
    #: would belong to neither distribution and would shift under the athlete
    #: the day they enable AFib History in an unrelated app.
    by_context: Mapping[HrvContext, Baseline | Abstention]


class JointStateKey(StrEnum):
    """The HRV x resting-HR quadrant, as a label and nothing more.

    **Read together or not at all.** HRV down on its own has at least three
    readings and they are not distinguishable from one number: genuine
    parasympathetic withdrawal, *parasympathetic saturation* (in well-trained
    athletes HRV falls as resting HR falls, and the pair moving the same way is
    the tell), and the alcohol artefact, where peripheral vasodilation produces
    the same picture as illness onset for a reason that has nothing to do with
    training. Resting HR moving with or against HRV is what separates them, so
    this is served as one named quadrant instead of two independent deviations
    a reader has to remember to cross.

    It is a **label with no verdict attached**. Which quadrant is bad depends
    on the athlete, the block and yesterday's session, and encoding that here
    would be the readiness score this surface exists not to emit.
    """

    HRV_LOW_RHR_LOW = "hrv_low_rhr_low"
    HRV_LOW_RHR_HIGH = "hrv_low_rhr_high"
    HRV_HIGH_RHR_LOW = "hrv_high_rhr_low"
    HRV_HIGH_RHR_HIGH = "hrv_high_rhr_high"


#: The plain-English label for each quadrant. Below and above are relative to
#: the athlete's own baseline mean, and neither word is a valence.
JOINT_STATE_LABELS: Mapping[JointStateKey, str] = {
    JointStateKey.HRV_LOW_RHR_LOW: "HRV below baseline, resting HR below baseline",
    JointStateKey.HRV_LOW_RHR_HIGH: "HRV below baseline, resting HR above baseline",
    JointStateKey.HRV_HIGH_RHR_LOW: "HRV above baseline, resting HR below baseline",
    JointStateKey.HRV_HIGH_RHR_HIGH: "HRV above baseline, resting HR above baseline",
}


@dataclass(frozen=True, slots=True)
class JointState:
    """The named quadrant, with the two deviations it was read from."""

    key: JointStateKey
    label: str
    hrv_deviation_sd: float
    resting_hr_deviation_sd: float


@dataclass(frozen=True, slots=True)
class OutsideMarker:
    """One marker sitting outside its own band, named and directed."""

    metric: WellnessMetric
    direction: Direction
    deviation_sd: float


@dataclass(frozen=True, slots=True)
class MarkersOutsideBand:
    """How many markers are outside their band, of how many that could say.

    **A count, never a score.** The denominator excludes markers whose baseline
    is immature, and it says so — ``2 of 4`` and not ``2 of 5`` — because a
    denominator that silently counts markers with no baseline makes two of five
    look calmer than two of two.
    """

    count: int
    of: int
    markers: tuple[OutsideMarker, ...]

    def __str__(self) -> str:
        """``2 of 4`` — count and denominator, never one without the other."""
        return f"{self.count} of {self.of}"


@dataclass(frozen=True, slots=True)
class Readiness:
    """What the markers say about today. No score, no recommendation.

    The key set is closed and pinned by `test_readiness_field_inventory`, which
    fails the moment a key named ``readiness_score``, ``recommendation``,
    ``verdict`` or ``score`` appears anywhere in the served object. Arc counts
    and names; whether today is a day to train is the coach's call, made out
    loud, with the confounders and the gaps visible.
    """

    as_of: dt.date
    markers_outside_band: MarkersOutsideBand
    #: None when either half of the pair is missing or immature. A quadrant
    #: drawn over an unmatured marker is a verdict wearing a label.
    joint_state: JointState | None


# --- computation --------------------------------------------------------------


def _analyse(marker: Marker, value: float) -> float | None:
    """Project a native reading into the marker's analysis space.

    None when the value cannot be projected — a non-positive HRV has no
    logarithm, and the domain's own bounds make one impossible, so this is a
    guard rather than a path.
    """
    if marker.space is Space.LN:
        return math.log(value) if value > 0 else None
    return value


def _native(marker: Marker, value: float) -> float:
    """Project an analysis-space figure back into the marker's own unit."""
    return math.exp(value) if marker.space is Space.LN else value


def readings_for(marker: Marker, days: Iterable[DaySample]) -> tuple[Reading, ...]:
    """Every reading of ``marker`` in ``days``, oldest first, ungated.

    Gating happens in :func:`eligible` — the raw list is what the series is
    rendered from, and an invalidated day's values are still returned there.
    """
    found: list[Reading] = []
    for entry in days:
        value = getattr(entry.day, marker.field, None)
        if value is None:
            continue
        if (
            marker.hrv_metric is not None
            and entry.day.hrv_metric is not marker.hrv_metric
        ):
            continue
        analysed = _analyse(marker, float(value))
        if analysed is None:
            continue
        found.append(
            Reading(
                local_date=entry.day.local_date,
                value=float(value),
                analysed=analysed,
                standing=entry.day.standing,
                subjective_recalled=entry.subjective_recalled,
                hrv_context=entry.day.hrv_context,
            )
        )
    return tuple(sorted(found, key=lambda reading: reading.local_date))


def eligible(marker: Marker, readings: Iterable[Reading]) -> tuple[Reading, ...]:
    """The readings that may enter a statistic, and why the others may not.

    Two exclusions, asymmetric on purpose:

    * an **objective** reading is excluded when the athlete declared a
      confounder in `app.domain.wellness.INVALIDATES_MARKERS`. A mean built
      partly out of alcohol artefacts is worse than a shorter honest one, and
      the deload week that was once triggered by two beers is why this gate
      exists at all. Lateness never excludes one: the watch measured it on the
      day.
    * a **subjective** rating is excluded when it was recalled rather than
      reported. A confounder never excludes one — a hot room makes a resting
      heart rate say nothing about readiness, and does not make "I felt tired"
      untrue.

    A day that is both recalled and invalidated is therefore excluded once from
    each population, not twice from either.
    """
    if marker.kind is MarkerKind.OBJECTIVE:
        return tuple(reading for reading in readings if reading.standing.actionable)
    return tuple(reading for reading in readings if not reading.subjective_recalled)


def _span_days(readings: Sequence[Reading]) -> int:
    """First to last reading, inclusive. Zero for an empty series."""
    if not readings:
        return 0
    return (readings[-1].local_date - readings[0].local_date).days + 1


def _slope(readings: Sequence[Reading]) -> Slope:
    """Least-squares trend per day through the analysed values."""
    if len(readings) < 2:
        return Slope(per_day=0.0, per_week=0.0, n=len(readings))
    origin = readings[0].local_date
    xs = [float((reading.local_date - origin).days) for reading in readings]
    ys = [reading.analysed for reading in readings]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    per_day = 0.0 if denominator == 0 else numerator / denominator
    return Slope(per_day=per_day, per_week=per_day * 7, n=len(readings))


def _rolling_mean(
    marker: Marker, readings: Sequence[Reading], *, on: dt.date
) -> RollingMean:
    """The mean over the half-open window ``[on - 6, on + 1)``, with its n."""
    first = on - dt.timedelta(days=ROLLING_MEAN_DAYS - 1)
    window = [reading for reading in readings if first <= reading.local_date <= on]
    if not window:
        return RollingMean(mean=None, mean_native=None, n=0)
    mean = statistics.fmean(reading.analysed for reading in window)
    return RollingMean(mean=mean, mean_native=_native(marker, mean), n=len(window))


def baseline_for(
    marker: Marker,
    readings: Sequence[Reading],
    *,
    on: dt.date,
    hrv_context: HrvContext | None = None,
) -> Baseline | Abstention:
    """The baseline for one marker over the 60 days ending ``on``, or an abstention.

    Generic over the marker: everything that differs between resting heart
    rate, HRV, body weight and a subjective rating is read off :class:`Marker`
    — the analysis space, whether a band is meaningful, and which population a
    day belongs to. Five copies of this function would be five copies of the
    maturity rule, and the first correction to one of them would be the last
    time they agreed.

    ``readings`` is the **ungated** list for this marker (and, for HRV, for one
    context): the exclusions in :func:`eligible` are applied here so that the
    ``n`` this returns is the number that survived them, which is what makes a
    thin baseline legible instead of mysterious.

    Maturity is two independent bars, :data:`MIN_BASELINE_READINGS` and
    :data:`MIN_BASELINE_SPAN_DAYS`, and failing either abstains. Both counts
    are reported either way, so the answer to "when will this be usable" is in
    the object rather than in someone's head.
    """
    first = on - dt.timedelta(days=BASELINE_WINDOW_DAYS - 1)
    window = [reading for reading in readings if first <= reading.local_date <= on]
    usable = eligible(marker, window)
    counts = Count(have=len(usable), need=MIN_BASELINE_READINGS)
    span = Count(have=_span_days(usable), need=MIN_BASELINE_SPAN_DAYS)
    if not counts.met or not span.met:
        return Abstention(
            metric=marker.metric,
            hrv_context=hrv_context,
            readings=counts,
            span_days=span,
        )

    values = [reading.analysed for reading in usable]
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    cv = None if mean == 0 else sd / mean
    rolling = _rolling_mean(marker, usable, on=on)

    band: Band | None = None
    deviation: float | None = None
    direction: Direction | None = None
    if marker.banded:
        # `0.5 x CV x mean` rather than `0.5 x sd` — the same number, written
        # the way the literature states it (see `Band`). The `cv is None`
        # branch is a mean of exactly zero, where the two stop agreeing and the
        # spread is the only thing left to scale by.
        half_width = SWC_FRACTION * (cv * mean if cv is not None else sd)
        band = Band(
            low=mean - half_width,
            high=mean + half_width,
            half_width=half_width,
            low_native=_native(marker, mean - half_width),
            high_native=_native(marker, mean + half_width),
        )
        if rolling.mean is not None:
            difference = rolling.mean - mean
            # A distance in SD units is undefined, not infinite, when the SD is
            # zero — so it is reported only when it means something. The
            # direction still is: a zero-width band has an outside.
            deviation = (
                difference / sd if sd > 0 else (0.0 if difference == 0 else None)
            )
            direction = (
                Direction.BELOW
                if rolling.mean < band.low
                else Direction.ABOVE
                if rolling.mean > band.high
                else Direction.WITHIN
            )

    return Baseline(
        metric=marker.metric,
        hrv_context=hrv_context,
        space=marker.space,
        unit=marker.unit,
        n=len(usable),
        span_days=span.have,
        mean=mean,
        mean_native=_native(marker, mean),
        sd=sd,
        cv=cv,
        trend=_slope(usable),
        band=band,
        deviation_sd=deviation,
        direction=direction,
    )


def trend_for(
    marker: Marker,
    days: Iterable[DaySample],
    *,
    start: dt.date,
    end: dt.date,
    on: dt.date,
) -> MetricTrend:
    """One metric's dated readings, its seven-day mean and its baseline.

    ``[start, end)`` is what the series covers and ``on`` is what the baseline
    and the rolling mean are anchored to — usually the last day of the range.
    Every date in the range appears in the series exactly once, carrying its
    reading or an explicit gap; nothing is interpolated and nothing is zero.
    """
    days = list(days)
    readings = readings_for(marker, days)
    by_date = {reading.local_date: reading for reading in readings}

    series = tuple(
        SeriesPoint(
            local_date=date,
            value=by_date[date].value if date in by_date else None,
            standing=by_date[date].standing if date in by_date else None,
        )
        for date in _dates(start, end)
    )

    usable = eligible(marker, readings)
    today = by_date[on].value if on in by_date else None

    contexts: dict[HrvContext, Baseline | Abstention] = {}
    if marker.by_context:
        present = {
            reading.hrv_context
            for reading in readings
            if reading.hrv_context is not None
        }
        for context in HrvContext:
            if context not in present:
                continue
            contexts[context] = baseline_for(
                marker,
                [reading for reading in readings if reading.hrv_context is context],
                on=on,
                hrv_context=context,
            )
        baseline = contexts.get(PREFERRED_HRV_CONTEXT) or baseline_for(
            marker, (), on=on, hrv_context=PREFERRED_HRV_CONTEXT
        )
        rolling = _rolling_mean(
            marker,
            [
                reading
                for reading in eligible(marker, readings)
                if reading.hrv_context is PREFERRED_HRV_CONTEXT
            ],
            on=on,
        )
    else:
        baseline = baseline_for(marker, readings, on=on)
        rolling = _rolling_mean(marker, usable, on=on)

    return MetricTrend(
        metric=marker.metric,
        unit=marker.unit,
        space=marker.space,
        series=series,
        today=today,
        rolling_mean_7d=rolling,
        baseline=baseline,
        by_context=contexts,
    )


def _dates(start: dt.date, end: dt.date) -> tuple[dt.date, ...]:
    """Every date in the half-open range ``[start, end)``, oldest first."""
    span = (end - start).days
    return tuple(start + dt.timedelta(days=offset) for offset in range(max(span, 0)))


def readiness(
    trends: Mapping[WellnessMetric, MetricTrend], *, on: dt.date
) -> Readiness:
    """Count the markers outside their own band, and name the HRV/RHR quadrant.

    A projection, not a judgement: it says how many of the markers that *can*
    speak are outside their normal range, which ones, and which way. It does
    not weigh them, score them or recommend anything, and
    `test_readiness_field_inventory` fails if a key that would suggest
    otherwise ever appears.
    """
    outside: list[OutsideMarker] = []
    denominator = 0
    for metric in READINESS_MARKERS:
        found = trends.get(metric)
        if found is None:
            continue
        baseline = found.baseline
        if not isinstance(baseline, Baseline) or baseline.direction is None:
            # An immature baseline is excluded from the denominator too: two of
            # five reads calmer than two of two, and only one of them is true.
            continue
        denominator += 1
        if (
            baseline.direction is not Direction.WITHIN
            and baseline.deviation_sd is not None
        ):
            outside.append(
                OutsideMarker(
                    metric=metric,
                    direction=baseline.direction,
                    deviation_sd=baseline.deviation_sd,
                )
            )

    return Readiness(
        as_of=on,
        markers_outside_band=MarkersOutsideBand(
            count=len(outside), of=denominator, markers=tuple(outside)
        ),
        joint_state=_joint_state(trends),
    )


def _joint_state(
    trends: Mapping[WellnessMetric, MetricTrend],
) -> JointState | None:
    """The HRV x resting-HR quadrant, or None when it cannot be drawn.

    Both halves must be present **and mature**. Falling back to one of them, or
    to an immature baseline, would produce a label that reads like evidence and
    is not — which is the failure this whole module is built around.
    """
    pair = []
    for metric in (WellnessMetric.HRV_RMSSD_MS, WellnessMetric.RESTING_HR_BPM):
        found = trends.get(metric)
        if found is None:
            return None
        baseline = found.baseline
        if not isinstance(baseline, Baseline) or baseline.deviation_sd is None:
            return None
        pair.append(baseline.deviation_sd)
    hrv_deviation, rhr_deviation = pair
    key = {
        (True, True): JointStateKey.HRV_LOW_RHR_LOW,
        (True, False): JointStateKey.HRV_LOW_RHR_HIGH,
        (False, True): JointStateKey.HRV_HIGH_RHR_LOW,
        (False, False): JointStateKey.HRV_HIGH_RHR_HIGH,
    }[(hrv_deviation < 0, rhr_deviation < 0)]
    return JointState(
        key=key,
        label=JOINT_STATE_LABELS[key],
        hrv_deviation_sd=hrv_deviation,
        resting_hr_deviation_sd=rhr_deviation,
    )
