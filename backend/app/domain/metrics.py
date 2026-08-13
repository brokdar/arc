"""Training metrics computed from a power series, and how a number explains itself.

Two things live here, and they are related by more than convenience.

**The metrics.** :func:`normalized_power`, :func:`intensity_factor` and
:func:`training_load` are the Coggan chain — NP, IF, TSS — as plain Python over
a sequence of watts. Plain Python because a four-hour ride is 14 400 samples
and that does not need a dataframe; the point of the module is that there is
**one** implementation. The planned number (`app.domain.prediction`) and the
recorded number (WP-5) come out of the same function, so they cannot disagree
by a few percent for short intervals — which is exactly what a closed-form
integral over step midpoints would do, and it would read as a systematic "you
did less than planned" bias in every interval session's adherence score. WP-5
may re-implement the *body* over a frame behind these exact signatures, and
must keep passing the fixtures in ``tests/unit/test_domain_metrics.py``.

**The explanation.** :class:`MetricExplanation` is the pattern every computed
number in this codebase follows from here on: the explanation of a number is
**data attached to the number**, not copy attached to a page. That is the only
form that survives the number being rendered in three places, and the only one
an MCP tool can hand to the coaching agent so the agent cites the same facts
the screen shows. A function that computes a metric builds the explanation
itself — it is the only code that knows which inputs and which assumptions
actually went in.

**And what happens when it cannot.** :class:`NotAssessed` is the third shape:
a metric that has no honest answer returns the *reason* it has none, never
``None`` and never a zero standing in for a missing channel. The UI holds the
slot and renders the reason; WP-7's scoring axes reuse the same type, so
"not assessed" means one thing across the product.

**Nulls are stops.** Every function here takes a ``*_fixed`` column exactly as
`app.domain.streams` produced it: ``Sequence[float | None]`` on the 1 Hz grid,
with ``None`` for a recording stop, a dropout the cleaner declined to repair,
or a channel that simply was not recording yet. Null rows are **excluded**
everywhere — never read as zero.

**Two durations, and which number gets which**. An *average* is divided
by :class:`AveragingBasis` — moving time, the seconds the athlete was actually
travelling — because that is the quantity "average power" and "average speed"
name on every head unit and every other platform, and a divisor nobody else
uses makes a correct number read as a wrong one. The *load chain* is untouched:
NP is still a rolling window over the recorded series and TSS's duration term
is still `recording time` (A4.4, A5.1). The split is deliberate and each
explanation says which side of it its number stands on.

**And one series per ratio**. The basis counts its own rows and hands
them to :meth:`AveragingBasis.integrate`, so an average's numerator covers
exactly the seconds its denominator did — the speed channel cannot dropout its
way into doubling an average or into inventing a standstill. The one number
that is a *ratio of two statistics* rather than a total over a duration, the
variability index, takes both of them over the recorded series instead, because
NP over one series divided by a mean over another is not a variability index
and can fall below 1.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.domain.activity import SessionDiscipline
from app.domain.anchors import AnchorVersion, describe_anchor
from app.domain.athlete import Sex
from app.domain.streams import MOVING_SPEED_MS
from app.domain.zones import Zone, ZoneModel

#: The rolling window Coggan's normalized power is defined over, in seconds.
NP_WINDOW_S = 30

#: TSS is scaled so that one hour (3 600 s) at ``IF == 1`` is 100 points:
#: ``3600 / 36 == 100``.
TSS_SCALE = 36.0


@dataclass(frozen=True, slots=True)
class MetricExplanation:
    """Why a number is the number. Travels with it; not page copy.

    Args:
        formula: The arithmetic, written the way a human would read it.
        inputs: Named quantities that went in, already rendered — an anchor
            input names the **version's** value, provenance and effective date,
            never "the athlete's current FTP", because the number was computed
            against a frozen version and stays true when the athlete's FTP
            moves.
        assumptions: What the computation had to assume, in the order it
            assumed them. Empty when there were none.
        citation: Where the method comes from, when it comes from somewhere.
    """

    formula: str
    inputs: Mapping[str, str]
    assumptions: tuple[str, ...] = ()
    citation: str | None = None


def normalized_power(watts: Sequence[float], *, sample_hz: int = 1) -> float:
    """Coggan normalized power: 30 s rolling mean, 4th power, mean, 4th root.

    ``NP = ( mean( rolling_mean_30s(P)^4 ) )^(1/4)``

    The window is ``NP_WINDOW_S * sample_hz`` samples. Leading samples use a
    **shorter** window rather than being dropped: dropping them would shorten
    the series a short interval is averaged over, which moves NP for exactly
    the sessions NP matters most for.

    Requires a **uniformly sampled** series; over irregular samples the result
    is meaningless (WP-4 guarantees the grid).

    Args:
        watts: Power samples, evenly spaced at ``sample_hz``.
        sample_hz: Samples per second. At least 1.

    Returns:
        The normalized power in watts, or ``0.0`` for an empty series — a
        series with no samples carries no work, and returning 0.0 rather than
        raising keeps every caller from wrapping this in a length check that
        says the same thing.

    Raises:
        ValueError: When ``sample_hz`` is below 1.

    Reference: Allen & Coggan, *Training and Racing with a Power Meter*.
    """
    if sample_hz < 1:
        raise ValueError(f"sample_hz must be at least 1, got {sample_hz}")
    if not watts:
        return 0.0

    window = NP_WINDOW_S * sample_hz
    total = 0.0
    fourth_power_sum = 0.0
    for index, value in enumerate(watts):
        total += value
        if index >= window:
            total -= watts[index - window]
        rolling_mean = total / min(index + 1, window)
        fourth_power_sum += rolling_mean**4
    return (fourth_power_sum / len(watts)) ** 0.25


def intensity_factor(np_watts: float, ftp_watts: float) -> float:
    """Normalized power as a fraction of threshold: ``NP / FTP``.

    Args:
        np_watts: Normalized power, from :func:`normalized_power`.
        ftp_watts: The FTP the effort is judged against — the value of one
            *anchor version*, not a current-value lookup.

    Raises:
        ValueError: When ``ftp_watts`` is not above zero.
    """
    if ftp_watts <= 0:
        raise ValueError(f"ftp_watts must be above 0, got {ftp_watts}")
    return np_watts / ftp_watts


def training_load(duration_s: int, intensity_factor: float) -> float:
    """TSS = ``duration_s × IF² / 36``. One hour at FTP is 100.

    Args:
        duration_s: How long the effort lasted, in seconds.
        intensity_factor: From :func:`intensity_factor`.

    Raises:
        ValueError: When ``duration_s`` is negative.
    """
    if duration_s < 0:
        raise ValueError(f"duration_s must not be negative, got {duration_s}")
    return duration_s * intensity_factor**2 / TSS_SCALE


# --- not assessed (work order A-1) --------------------------------------------


@dataclass(frozen=True, slots=True)
class NotAssessed:
    """A metric that has no honest answer, and the reason it has none.

    Deliberately tiny and deliberately not ``None``: the difference between
    "0 W" and "this ride carried no power meter" is the whole product, and a
    caller that receives ``None`` has to invent the sentence itself — three
    times, differently. The reason is written for the athlete and **names the
    missing input** ("no heart rate was recorded", "no FTP anchor is in
    force"), because that is what the empty-state rule renders and what the
    coaching agent quotes.

    WP-7's scoring axes return the same type for the same reason.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Measured:
    """A number that came out, together with why it is that number."""

    value: float
    explanation: MetricExplanation


#: What every metric function in this module answers with.
type Assessment = Measured | NotAssessed


def value_of(assessment: Assessment) -> float | None:
    """The number, or ``None`` when the metric was not assessed."""
    return assessment.value if isinstance(assessment, Measured) else None


# --- the shared vocabulary ----------------------------------------------------

_COGGAN = "Allen & Coggan, Training and Racing with a Power Meter"
_BANISTER = "Banister TRIMP, rescaled to HRSS (one hour at threshold HR = 100)"
_TREFF = "Treff et al., polarization index"
_GOLDEN_CHEETAH = (
    "GoldenCheetah, ElevationGain metric (GC_ELEVATION_HYSTERESIS, default 3 m)"
)

#: Power at or below which the athlete is not pedalling, in watts. Paired with
#: :data:`app.domain.streams.MOVING_SPEED_MS` this is the parity definition of
#: coasting (Appendix A.5): moving at 1 km/h or more while producing 10 W or
#: less.
COASTING_MAX_W = 10.0

#: Metres per second to kilometres per hour. The parquet's ``speed`` channel is
#: SI (`app.domain.streams.PLAUSIBLE_RANGE` bounds it at 35 m/s); every speed
#: **metric** is km/h, because that is the unit the athlete reads. The
#: conversion happens once, here, rather than in whichever adapter renders it.
MS_TO_KMH = 3.6

#: Metres a climb must gain before it is counted at all. Barometric
#: altimeters wander by a metre or two while standing still,
#: and summing raw positive deltas turns that wander into hundreds of metres
#: of "climbing" on a flat ride.
#:
#: Three metres is GoldenCheetah's ``GC_ELEVATION_HYSTERESIS``, the default its
#: ``ElevationGain`` metric falls back to (``src/Metrics/BasicRideMetrics.cpp``:
#: ``if (hysteresis <= 0.1) hysteresis = 3.00;``) and the number its manual
#: describes as "only elevation changes greater than 3 m are aggregated". It is
#: a threshold on the *climb*, not on the sample-to-sample step, so a long drag
#: is not charged it repeatedly — see :func:`elevation_gain_m`.
ELEVATION_HYSTERESIS_M = 3.0

#: Seconds of altitude averaged together, centred on each row, before the
#: threshold above is applied.
#:
#: A threshold alone cannot separate a hill from an altimeter: pressure noise
#: at 1 Hz is a few tenths of a metre per sample and wanders several metres
#: over a minute, so an excursion large enough to clear any believable
#: threshold happens on a perfectly flat road several times an hour. Averaging
#: first attacks the noise where it actually differs from terrain — in
#: frequency. Fifteen seconds is about a hundred metres of road at riding
#: speed: shorter than any hill, longer than any pressure blip. The cost is
#: stated rather than hidden: a sharp summit reads roughly a metre lower than
#: the raw trace, because averaging rounds a peak off.
ELEVATION_SMOOTHING_S = 15

#: Banister's exponential weighting coefficient, per sex (Appendix A.2). It
#: appears in numerator and denominator of HRSS and does **not** cancel — the
#: exponential is non-linear — so HRSS is genuinely sex-dependent.
HRSS_K: Mapping[Sex, float] = MappingProxyType({Sex.MALE: 1.92, Sex.FEMALE: 1.67})

#: Banister's scaling constant, the same for both sexes.
HRSS_C = 0.64


class LoadBasis(StrEnum):
    """Which model produced the training load that was selected (A5.2)."""

    POWER = "power"
    HR = "hr"


def _present(values: Sequence[float | None]) -> list[float]:
    """The rows that hold a reading, in order, stops and dropouts dropped."""
    return [value for value in values if value is not None]


def _absent(channel: str) -> NotAssessed:
    """The reason a channel that was never recorded cannot be assessed."""
    return NotAssessed(f"no {channel} was recorded")


# --- the averaging basis -----------------------------------------------


#: What the load chain's duration term is, restated wherever an average has to
#: say that it is **not** that number. One sentence, one place: an average and
#: a load that disagree about their divisor are a support question, and the
#: answer has to be identical every time it is asked.
LOAD_DURATION_NOTE = (
    "the training load's duration term is unaffected: TSS is still computed "
    "over recording time (A5.1), not over this basis"
)

#: How much of the recorded time the cleaned speed column has to cover before
#: moving time may be a divisor at all.
#:
#: The failure this line exists for is not proportional, so it cannot be
#: absorbed by a wider assumption string. A speed sensor that dies halfway
#: through a steady hour leaves a column that is perfectly plausible for the
#: half it covers: 1 800 moving seconds, every one of them real. Dividing by it
#: would double the average power of a ride nothing else about changed, and
#: subtracting it from elapsed time would report the athlete as having stood
#: still for thirty minutes they spent riding. Under this line the answer is
#: not a smaller number, it is a different question — so the basis falls back
#: to recording time and says so, and stopped time is refused outright.
SPEED_COVERAGE_FLOOR = 0.9


@dataclass(frozen=True, slots=True)
class AveragingBasis:
    """The denominator a ride's **averages** are divided by, and the seconds it counted.

    Moving time where the ride recorded speed: the seconds the athlete was
    actually travelling, at or above :data:`app.domain.streams.MOVING_SPEED_MS`.
    That is what "average power" and "average speed" mean on a head unit and on
    every platform this one will be compared against, and an average taken over
    a divisor nobody else uses is reported as a bug about a number that is
    arithmetically fine.

    **The basis carries its own rows**, and that is the point of the
    type rather than a convenience. Moving time used to be counted off the raw
    device samples while every numerator integrated the cleaned 1 Hz column;
    nothing tied the two together, so a speed channel that dropped out for half
    a ride produced a numerator over 3 600 seconds above a denominator over
    1 800. :meth:`integrate` closes that: a numerator is summed over **exactly**
    the rows :attr:`seconds` counted, so the two cannot describe different
    stretches of the ride however the speed channel behaves.

    Recording time is the **fallback**, not a second policy: an indoor session
    with no speed channel has no moving time to divide by, and refusing to
    report an average power for it would be a worse answer than reporting one
    over the duration that does exist. :attr:`from_moving_time` says which
    happened, :attr:`assumption` says *why* in the athlete's words, and every
    explanation built from this basis prints both, so the two cases are never
    confused on screen.

    Args:
        seconds: The divisor. Always above zero.
        label: What it is called, for a formula line.
        described: The same, with its number and its definition, for an input
            line.
        from_moving_time: Whether moving time supplied it.
        assumption: The one sentence an average over this basis has to state —
            what the divisor is and, when it is not moving time, what stopped
            it from being.
        rows: The grid rows this basis counted, or ``None`` for "every row",
            which is what a recording-time basis means.
        moving_s: Seconds the cleaned speed column reported travelling,
            whichever basis was chosen. Equal to :attr:`seconds` exactly when
            :attr:`from_moving_time`; reported anyway when the basis fell back,
            because it is still a fact about the column.
        uncovered_s: Recorded seconds the speed column had no reading for.
            Neither moving nor standing still — nothing is known about them —
            and :func:`stopped_time_s` is the one that must not pretend
            otherwise.
    """

    seconds: float
    label: str
    described: str
    from_moving_time: bool
    assumption: str
    rows: tuple[int, ...] | None
    moving_s: float
    uncovered_s: float

    def integrate(self, column: Sequence[float | None]) -> tuple[float, int]:
        """Sum one column over exactly the rows this basis counted.

        Returns:
            ``(total, readings)`` — the sum of the column over this basis' own
            rows and how many of them carried a reading. Rows with no reading
            contribute nothing to the total while still costing their second in
            :attr:`seconds`, which is what makes a channel's dropout read as a
            lower average rather than as a shorter ride.
        """
        indices = range(len(column)) if self.rows is None else self.rows
        total = 0.0
        readings = 0
        for index in indices:
            if index < len(column) and (value := column[index]) is not None:
                total += value
                readings += 1
        return total, readings


def averaging_basis(
    speed_fixed: Sequence[float | None], *, recording_time_s: float
) -> AveragingBasis | NotAssessed:
    """Pick the divisor for this session's averages, or say there is none.

    Counted off the **cleaned speed column** — the same column every numerator
    is integrated over — rather than off the raw device samples
    (`app.domain.streams.ResampleResult.moving_time_s`, which remains the
    recording's own device-derived number and is not what the artefact
    averages by). One second per row at or above
    :data:`app.domain.streams.MOVING_SPEED_MS`, which on the 1 Hz grid is the
    same arithmetic and, unlike the raw count, cannot disagree with the
    numerators about which seconds existed.

    Moving time is refused when the column does not cover the ride: see
    :data:`SPEED_COVERAGE_FLOOR` for the failure that line exists for.

    One second per row is the same convention :func:`coasting_time_s` and
    :func:`time_in_zone` count by, and it carries the grid's ``+1``: a frame
    spans ``floor(elapsed) + 1`` rows, so a ride that never stopped moving can
    report one second more moving time than recording time. That single second
    is the last row's, and it is a real row of the ride.

    Args:
        speed_fixed: The cleaned speed column on the 1 Hz grid, in m/s. Empty
            for a session that recorded no speed at all.
        recording_time_s: Elapsed minus every stop over 30 s (A4.4) — both the
            fallback divisor and the yardstick the speed channel's coverage is
            measured against.

    Returns:
        The basis, or a :class:`NotAssessed` when the session recorded neither
        duration — a manual session whose columns are empty anyway.
    """
    moving = tuple(
        index
        for index, value in enumerate(speed_fixed)
        if value is not None and value >= MOVING_SPEED_MS
    )
    covered_s = float(sum(1 for value in speed_fixed if value is not None))
    coverage = min(covered_s / recording_time_s, 1.0) if recording_time_s > 0 else 0.0
    uncovered_s = max(0.0, recording_time_s - covered_s)
    threshold_kmh = f"{MOVING_SPEED_MS * MS_TO_KMH:g} km/h"

    if moving and (coverage >= SPEED_COVERAGE_FLOOR or recording_time_s <= 0):
        return AveragingBasis(
            seconds=float(len(moving)),
            label="moving time",
            described=(
                f"{len(moving)} rows of the cleaned speed column at or above "
                f"{threshold_kmh}"
            ),
            from_moving_time=True,
            assumption=(
                "divided by moving time, the seconds spent travelling — the "
                "same basis a head unit averages over — and the sum above it "
                "runs over those same seconds, so a second missing from one "
                "is missing from both"
            ),
            rows=moving,
            moving_s=float(len(moving)),
            uncovered_s=uncovered_s,
        )
    if recording_time_s > 0:
        return AveragingBasis(
            seconds=recording_time_s,
            label="recording time",
            described=(
                f"{recording_time_s:.0f} s of recording time "
                "(elapsed minus every stop over 30 s)"
            ),
            from_moving_time=False,
            assumption=_fallback_assumption(
                covered_s=covered_s, coverage=coverage, moving_rows=len(moving)
            ),
            rows=None,
            moving_s=float(len(moving)),
            uncovered_s=uncovered_s,
        )
    return NotAssessed("this session records no duration to average over")


def _fallback_assumption(*, covered_s: float, coverage: float, moving_rows: int) -> str:
    """Why this session's averages are over recording time, precisely.

    Three different facts end up at the same divisor and they are not the same
    thing to an athlete: an indoor session that carries no speed channel at
    all, a ride whose speed channel covers too little of the recording to be
    divided by, and a recording that never moved. Saying "no speed was
    recorded" for all three — which is what this used to do — tells two of them
    something untrue about their own file.
    """
    tail = (
        "the divisor is recording time — elapsed minus every stop over 30 s — "
        "and this reads lower than the average a head unit would display"
    )
    if covered_s <= 0:
        return f"no speed was recorded, so there is no moving time; {tail}"
    if coverage < SPEED_COVERAGE_FLOOR:
        return (
            f"the speed channel covers only {coverage:.0%} of the recorded "
            "seconds, so its moving time would divide a whole ride's readings "
            f"by part of one; {tail}"
        )
    if moving_rows == 0:
        return (
            "the speed channel never reached "
            f"{MOVING_SPEED_MS * MS_TO_KMH:g} km/h, so there is no moving "
            f"time; {tail}"
        )
    return f"there is no moving time to divide by; {tail}"


# --- the power chain (work order A-2, Appendix A.1) ----------------------------


def average_power(
    power_fixed: Sequence[float | None], basis: AveragingBasis | NotAssessed
) -> Assessment:
    """Work done over the averaging basis, divided by it — moving time.

    ``average_power = Σ P × Δt / basis`` (Appendix A.1, over moving time),
    where the sum runs over **exactly the seconds the basis counted**.
    That coupling is the whole of it: the numerator and the denominator are
    both taken from :class:`AveragingBasis`, so no dropout, no repair and no
    noisy GPS can leave one describing more of the ride than the other. Over a
    moving basis this is work done while travelling ÷ moving time, which is the
    number a head unit shows; the ride's total work is
    :func:`work_kj`, over every recorded row, and the two differ by whatever
    was produced at a standstill.

    Rows with no power reading contribute no joules while still costing their
    second, so a ride whose meter dropped out for ten minutes averages lower
    than the ten minutes it lost — which is the honest reading and is stated
    as an assumption rather than hidden.

    The basis is moving time wherever the speed channel covers the ride, which
    is what a head unit divides by; :class:`AveragingBasis` documents the
    fallback and why it exists. What this is **not** is the load's duration
    term: TSS still divides the ride by recording time (A5.1), and every
    explanation says so, because an average and a load that appear to disagree
    about how long the ride was is exactly the question this data is here to
    answer.
    """
    if isinstance(basis, NotAssessed):
        return basis
    if not _present(power_fixed):
        return _absent("power")
    joules, readings = basis.integrate(power_fixed)
    if not readings:
        return NotAssessed(f"no power was recorded during this session's {basis.label}")
    moving = basis.from_moving_time
    work_label = "work while moving" if moving else "work"
    return Measured(
        value=joules / basis.seconds,
        explanation=MetricExplanation(
            formula=(
                "average power = Σ P × Δt"
                + (" while moving" if moving else "")
                + f" / {basis.label}"
            ),
            inputs=MappingProxyType(
                {
                    work_label: f"{joules / 1000:.0f} kJ over {readings} power samples",
                    basis.label: basis.described,
                }
            ),
            assumptions=(
                "rows with no power reading contribute no work",
                basis.assumption,
                LOAD_DURATION_NOTE,
            ),
            citation=_COGGAN,
        ),
    )


def work_kj(power_fixed: Sequence[float | None]) -> Assessment:
    """Total mechanical work: ``Σ P × Δt / 1000`` on the 1 Hz grid."""
    present = _present(power_fixed)
    if not present:
        return _absent("power")
    return Measured(
        value=sum(present) / 1000,
        explanation=MetricExplanation(
            formula="work = Σ P × Δt / 1000",
            inputs=MappingProxyType(
                {"samples": f"{len(present)} power readings at 1 Hz"}
            ),
            assumptions=("rows with no power reading contribute no work",),
            citation=_COGGAN,
        ),
    )


def work_above_ftp_kj(
    power_fixed: Sequence[float | None], ftp: AnchorVersion | None
) -> Assessment:
    """Work done above threshold: ``Σ max(0, P − FTP) × Δt / 1000``."""
    if ftp is None:
        return NotAssessed("no FTP anchor is in force, so threshold is unknown")
    present = _present(power_fixed)
    if not present:
        return _absent("power")
    above = sum(max(0.0, watts - ftp.value) for watts in present)
    return Measured(
        value=above / 1000,
        explanation=MetricExplanation(
            formula="work above FTP = Σ max(0, P − FTP) × Δt / 1000",
            inputs=MappingProxyType(
                {
                    "FTP": describe_anchor(ftp),
                    "samples": f"{len(present)} power readings at 1 Hz",
                }
            ),
            citation=_COGGAN,
        ),
    )


def variability_index(
    np_watts: float, power_fixed: Sequence[float | None]
) -> Assessment:
    """``NP / the mean of the same series NP was taken over``.

    1.0 is a perfectly steady effort; a criterium sits well above 1.1. Below
    1.0 is not a ragged ride, it is a broken ratio — and that is what this
    function exists to make impossible, so it takes the **column** rather than
    a number somebody else averaged.

    VI is a ratio of two statistics of one series: NP is the fourth power mean
    of the 30 s rolling means of the recorded rows, and the denominator is the
    arithmetic mean of those same rows. By the power-mean inequality the fourth
    power mean dominates the first, so the ratio sits at or above 1 and reads
    as "how much more the ride cost than its average suggests". Hand it the
    **published** average power instead and it stops meaning that: that
    number is divided by moving time, so on a ride with recorded traffic
    lights the denominator grows while NP does not, and a steady 200 W hour
    interrupted by ten minutes of lights reports a variability index of 0.92 —
    a number no definition of VI can produce, presented with no assumption
    saying anything had happened.

    So the moving-time average stays the published one and this takes its own,
    unpublished, over the recorded series; the explanation names that basis
    rather than letting a reader assume it is the average power on screen
    beside it.

    Args:
        np_watts: Normalized power, from :func:`normalized_power` over this
            same column's recorded rows.
        power_fixed: The cleaned power column NP was taken over.
    """
    present = _present(power_fixed)
    if not present:
        return _absent("power")
    series_mean = sum(present) / len(present)
    if series_mean <= 0:
        return NotAssessed("average power is zero, so there is no ratio to take")
    return Measured(
        value=np_watts / series_mean,
        explanation=MetricExplanation(
            formula="variability index = NP / mean power over the recorded rows",
            inputs=MappingProxyType(
                {
                    "NP": f"{np_watts:.0f} W",
                    "mean power": (
                        f"{series_mean:.0f} W over the {len(present)} recorded rows"
                    ),
                }
            ),
            assumptions=(
                (
                    "both terms are taken over the same recorded rows, which is "
                    "what keeps the ratio at or above 1 — it is deliberately "
                    "not the average power shown beside it, which is divided "
                    "by moving time"
                ),
            ),
            citation=_COGGAN,
        ),
    )


def efficiency_factor(np_watts: float, average_hr: float) -> Assessment:
    """``NP / average HR`` — watts per beat, the aerobic-fitness trend line."""
    if average_hr <= 0:
        return NotAssessed("average heart rate is zero, so there is no ratio to take")
    return Measured(
        value=np_watts / average_hr,
        explanation=MetricExplanation(
            formula="efficiency factor = NP / average HR",
            inputs=MappingProxyType(
                {"NP": f"{np_watts:.0f} W", "average HR": f"{average_hr:.0f} bpm"}
            ),
            assumptions=(
                (
                    "comparable only between sessions of similar duration and "
                    "conditions — heat and fatigue move it on their own"
                ),
            ),
            citation=_COGGAN,
        ),
    )


def coasting_time_s(
    power_fixed: Sequence[float | None], speed_fixed: Sequence[float | None]
) -> Assessment:
    """Seconds spent moving without pedalling (Appendix A.5's definition).

    Moving at or above :data:`app.domain.streams.MOVING_SPEED_MS` (1 km/h)
    while producing at most :data:`COASTING_MAX_W`. Both channels are needed:
    10 W at a standstill is a traffic light, not a descent.

    Coasting is **display only**. It is deliberately *not* subtracted from the
    duration training load is computed over — A5.1 verified that subtracting
    it puts our TSS 7 % below the reference platform's, and a descent is part
    of a ride's physiological cost even though the legs stopped.
    """
    if not _present(power_fixed):
        return _absent("power")
    if not _present(speed_fixed):
        return _absent("speed")
    rows = min(len(power_fixed), len(speed_fixed))
    seconds = sum(
        1
        for index in range(rows)
        if (watts := power_fixed[index]) is not None
        and (speed := speed_fixed[index]) is not None
        and speed >= MOVING_SPEED_MS
        and watts <= COASTING_MAX_W
    )
    return Measured(
        value=float(seconds),
        explanation=MetricExplanation(
            formula=(
                f"coasting = rows with speed ≥ {MOVING_SPEED_MS:.3f} m/s "
                f"and power ≤ {COASTING_MAX_W:g} W"
            ),
            inputs=MappingProxyType({"rows examined": f"{rows} at 1 Hz"}),
            assumptions=(
                (
                    "display only — coasting is never subtracted from the "
                    "duration training load is computed over"
                ),
            ),
        ),
    )


# --- distance and speed (the ride log's basics) -------------------------------


#: First assumption on every distance a device's own odometer produced. The
#: **source sentence**: :func:`distance_km` always puts the one that applies
#: first, and :func:`average_speed_kmh` repeats whichever it finds there, so a
#: km/h figure says what its kilometres came from without deriving it a second
#: time.
ODOMETER_NOTE = (
    "the distance is the device's own odometer channel — its cumulative "
    "distance field — differenced from its first reading to its last, which is "
    "the number the head unit displayed and the number every platform reading "
    "this file reports"
)

#: The counterpart, completed with the reason the odometer was not used.
SPEED_INTEGRATION_NOTE = (
    "the distance is integrated from the 1 Hz speed channel because {reason}; "
    "a head unit integrates wheel revolutions far faster than once a second, "
    "so this reads roughly 1-2 % under the odometer it would have shown"
)

#: The source sentence for a session merged from several recordings, every one
#: of which had an odometer of its own.
MERGED_ODOMETER_NOTE = (
    "the distance is each recording's own odometer span — the device's "
    "cumulative distance field, differenced from that recording's first "
    "reading to its last — summed over the {recordings} recordings this "
    "session was merged from, because each of them counts from its own zero"
)

#: And the source sentence for a merge only some of whose recordings carried
#: one, where the two kinds of kilometre are added together and must say so.
MIXED_SOURCE_NOTE = (
    "the distance is the sum of what each of this session's {recordings} "
    "recordings could report: {odometer} of them from the device's own "
    "odometer channel, and {speed} integrated from the 1 Hz speed channel "
    "because {reason}"
)

#: Why a stream has no odometer to difference. Named because it is the answer
#: for every file ingested before the channel existed, and the athlete reading
#: it should be told that recomputing alone will not change it.
NO_ODOMETER_REASON = (
    "this recording carries no odometer channel — either the device wrote "
    "none, or the stream was stored before arc read one, in which case "
    "rebuilding it from the original file supplies it"
)

#: How much of a recording's own recorded seconds its odometer has to cover
#: before the span of that odometer may stand for the whole of it.
#:
#: The mirror of :data:`SPEED_COVERAGE_FLOOR`, and it exists for the same
#: shape of failure. A cumulative channel that stops halfway through a ride —
#: a sensor that dies, a device that stops writing the field — still yields a
#: perfectly monotone column whose span is a real number of metres, just not
#: this ride's: a 10 km ride whose odometer gave up at 60 % reports 6.08 km,
#: with no reading out of range and nothing for `app.domain.streams.clean` to
#: have caught. Under this line the odometer is not *worse*, it is answering a
#: different question, so the metric falls back to integrating speed and names
#: the fraction that made it.
#:
#: The yardstick is the **speed column's own coverage of the same rows**, not
#: the grid length, for two reasons: the rows a device paused for hold no
#: reading in either channel and must not count against the odometer, and a
#: merged session's padding gap is exactly such a run. Where the recording
#: carried no speed at all the row count is used instead, which is the only
#: yardstick left.
ODOMETER_COVERAGE_FLOOR = 0.9

#: Consecutive readings below the running maximum that are read as a glitch
#: rather than as a reset.
#:
#: A cumulative channel must not go backwards, but a *reading* of one may: a
#: sensor that reports a garbled packet, or a device that corrects its own
#: wheel-circumference estimate, puts one or two readings below where it had
#: already been and then carries on from where it was. Refusing the whole
#: column for that discards a thousand good readings over a hundred metres of
#: self-correcting noise. A genuine reset does not recover — the column
#: restarts near zero and stays there — so "how long it stays below" separates
#: the two exactly, and five readings is comfortably longer than any glitch
#: and far shorter than any reset.
ODOMETER_DIP_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class _Leg:
    """One recording's contribution to a session's distance, and its source.

    A merged session is several recordings laid on one grid (WP-6.5), and each
    one's odometer counts from **its own** zero — so the session's distance is
    the sum of the parts and never the span of the joined column. See
    :func:`distance_km`.

    Args:
        metres: What this recording contributed.
        readings: Rows that produced it — odometer readings for an odometer
            leg, speed readings for an integrated one.
        coverage: Fraction of this recording's recorded seconds the odometer
            covered. Zero for a leg that fell back.
        from_odometer: Which of the two it is.
        reason: Why it is not the odometer, for a leg that fell back. ``None``
            for an odometer leg.
    """

    metres: float
    readings: int
    coverage: float
    from_odometer: bool
    reason: str | None


def _odometer_span(
    distance_fixed: Sequence[float | None], *, recorded_rows: int
) -> tuple[float, int, float] | str:
    """Metres between the odometer's first and last reading, or why not.

    Returns ``(metres, readings, coverage)`` when the column can be
    differenced, and otherwise the sentence explaining why it cannot — which
    the caller states rather than swallowing.

    Two checks, and they catch different things.

    **Ordering.** An odometer counts upwards forever; a column that goes
    backwards *and stays there* has been reset by a device or corrupted in
    transit, and the metres it lost are not recoverable from it. Every such
    column still holds perfectly plausible numbers, so nothing upstream can
    have caught it (see `app.domain.streams.PLAUSIBLE_RANGE`). An isolated dip
    that recovers within :data:`ODOMETER_DIP_SAMPLES` readings is a glitch and
    is ridden over, with the span taken from the running maximum rather than
    from the last row so a dip at the very end cannot shorten the ride.

    **Coverage.** A monotone column that covers 60 % of the ride reports 60 %
    of the ride, with nothing about the number saying so — see
    :data:`ODOMETER_COVERAGE_FLOOR`.

    Args:
        distance_fixed: The cleaned odometer column of **one recording**, in
            cumulative metres.
        recorded_rows: How many seconds of that recording carry a reading at
            all — the yardstick coverage is measured against.
    """
    present = _present(distance_fixed)
    if not present:
        return NO_ODOMETER_REASON
    if len(present) < 2:
        return (
            "the odometer channel carries a single reading, so there is "
            "nothing to difference"
        )
    high = present[0]
    below = 0
    for value in present[1:]:
        if value < high:
            below += 1
            if below > ODOMETER_DIP_SAMPLES:
                return (
                    f"the odometer channel goes backwards and stays there "
                    f"({high:.0f} m, then {value:.0f} m for more than "
                    f"{ODOMETER_DIP_SAMPLES} readings), which means this "
                    "device reset it or the column was corrupted rather than "
                    "merely glitching"
                )
        else:
            high = value
            below = 0
    span = high - present[0]
    if span <= 0:
        return "the odometer channel never advanced"
    coverage = min(len(present) / recorded_rows, 1.0) if recorded_rows > 0 else 1.0
    if coverage < ODOMETER_COVERAGE_FLOOR:
        return (
            f"the odometer channel covers only {coverage:.0%} of the seconds "
            "this recording holds a reading for, so its span would report "
            "part of the ride as the whole of it"
        )
    return span, len(present), coverage


def _leg(
    speed_fixed: Sequence[float | None], distance_fixed: Sequence[float | None]
) -> _Leg:
    """One recording's distance: its own odometer where it has one, speed else.

    The fallback is **per recording** rather than per session, because that is
    the unit the odometer is a fact about: a session merged from a ride with an
    odometer and one without must report the first from its device and the
    second from its speed column, not throw the good one away.
    """
    present = _present(speed_fixed)
    recorded_rows = len(present) or len(speed_fixed) or len(distance_fixed)
    odometer = _odometer_span(distance_fixed, recorded_rows=recorded_rows)
    if not isinstance(odometer, str):
        metres, readings, coverage = odometer
        return _Leg(
            metres=metres,
            readings=readings,
            coverage=coverage,
            from_odometer=True,
            reason=None,
        )
    return _Leg(
        metres=sum(present),
        readings=len(present),
        coverage=0.0,
        from_odometer=False,
        reason=odometer,
    )


def _odometer_coverage_note(legs: Sequence[_Leg]) -> str:
    """What the odometer legs were actually verified to cover.

    The assumption this replaces claimed the odometer "covers the whole
    recording" unconditionally, which was a promise about a column nothing had
    measured — and false for the very first real file this was run against,
    whose odometer starts 29 rows in. Now the sentence states what
    :func:`_odometer_span` checked.
    """
    coverage = min(leg.coverage for leg in legs)
    subject = "this recording holds" if len(legs) == 1 else "these recordings hold"
    if coverage >= 1.0:
        return (
            f"the odometer covers every second {subject} a reading for, "
            f"including the metres rolled below {MOVING_SPEED_MS * MS_TO_KMH:g} "
            "km/h that moving time does not count"
        )
    return (
        f"the odometer covers {coverage:.0%} of the seconds {subject} a "
        "reading for — a cumulative reading spans the gaps inside it, so only "
        "metres rolled before its first reading or after its last are missing "
        "here"
    )


def distance_km(
    speed_fixed: Sequence[float | None],
    distance_fixed: Sequence[float | None] = (),
    *,
    segments: Sequence[tuple[int, int]] = (),
) -> Assessment:
    """How far the ride went — the odometer where there is one, speed otherwise.

    **The odometer wins**. A head unit carries a cumulative ``distance``
    channel that it integrates internally from wheel revolutions at a far
    higher rate than the once-a-second speed it writes out, so the last reading
    minus the first is the distance the device displayed — and the distance
    Strava and intervals.icu report, because they read the same field. Summing
    the speed column instead loses whatever happened between two samples: on
    the 41 km reference ride it came out 1.5 % short, 40.3 km against 40.95, a
    gap far too large to be quantisation and exactly the size that makes an
    athlete distrust every other number on the page.

    **Differenced per recording, then summed**. A merged session lays
    several recordings on one 1 Hz grid (`app.ingest.analysis`), and each
    device's odometer counts from **its own** zero — so the joined column runs
    0 → 40 950, gap, 0 → 30 000. Differencing *that* end to end is not a
    smaller number, it is a different ride: at best the join trips the
    backwards guard and the athlete is told their hardware reset itself, and at
    worst the second file's odometer happens to start above the first's end and
    a silently wrong monotone number comes out. Each recording's own span is
    the only honest reading, and each one falls back on its own terms, so a
    merge of one file with an odometer and one without reports the sum rather
    than the first file's distance for the whole session.

    Neither reading is taken from the latitude/longitude track. That
    disagreement is not noise either: a GPS trace through a tunnel or under a
    plane-tree avenue draws a straight line the athlete did not ride, and a
    trace sampled every four seconds cuts every corner.

    **Falling back is normal, not exceptional.** A GPX file has no odometer, an
    indoor session may have none, every stream stored before this channel
    existed has none, and a channel that covers too little of its recording is
    refused outright (:data:`ODOMETER_COVERAGE_FLOOR`) — so speed integration
    stays a first-class path and the explanation names which one produced the
    number and why. That sentence is always
    :attr:`MetricExplanation.assumptions`'s first entry (see
    :data:`ODOMETER_NOTE`).

    Δt is one second by construction (A4.1's grid), so summing the speed column
    already gives metres. Rows with no reading contribute no distance either
    way, which makes a ride whose speed sensor dropped out read short.

    Args:
        speed_fixed: The cleaned speed column, in m/s.
        distance_fixed: The cleaned odometer column, in cumulative metres.
            Empty for a file that carried none, which is the ordinary case for
            GPX and for anything ingested before the odometer was stored.
        segments: ``[start, end)`` row ranges, one per recording on the joined
            grid, in order. Empty — the ordinary single-recording case — means
            one segment spanning everything.
    """
    spans = tuple(segments) or ((0, max(len(speed_fixed), len(distance_fixed))),)
    legs = [
        _leg(speed_fixed[start:end], distance_fixed[start:end]) for start, end in spans
    ]
    if not any(leg.readings for leg in legs):
        return _absent("speed")

    from_odometer = [leg for leg in legs if leg.from_odometer]
    integrated = [leg for leg in legs if not leg.from_odometer and leg.readings]
    metres = sum(leg.metres for leg in legs)
    inputs: dict[str, str] = {}
    if from_odometer:
        odometer_m = sum(leg.metres for leg in from_odometer)
        readings = sum(leg.readings for leg in from_odometer)
        inputs["odometer"] = f"{odometer_m:.0f} m over {readings} readings" + (
            f" in {len(from_odometer)} recordings" if len(from_odometer) > 1 else ""
        )
    if integrated:
        samples = sum(leg.readings for leg in integrated)
        speed_m = sum(leg.metres for leg in integrated)
        inputs["speed"] = f"{speed_m:.0f} m over {samples} speed readings at 1 Hz"

    return Measured(
        value=metres / 1000,
        explanation=MetricExplanation(
            formula=_distance_formula(from_odometer, integrated),
            inputs=MappingProxyType(inputs),
            assumptions=_distance_assumptions(legs, from_odometer, integrated),
        ),
    )


def _distance_formula(from_odometer: Sequence[_Leg], integrated: Sequence[_Leg]) -> str:
    """The arithmetic that produced the distance, written for a human."""
    if from_odometer and integrated:
        return (
            "distance = Σ over the recordings of (last odometer reading − "
            "first, where there is one, else Σ v × Δt) / 1000"
        )
    if from_odometer:
        if len(from_odometer) > 1:
            return (
                "distance = Σ over the recordings of (last odometer reading − "
                "first) / 1000"
            )
        return "distance = last odometer reading − first, / 1000"
    return "distance = Σ v × Δt / 1000, with Δt = 1 s"


def _distance_assumptions(
    legs: Sequence[_Leg],
    from_odometer: Sequence[_Leg],
    integrated: Sequence[_Leg],
) -> tuple[str, ...]:
    """What the distance had to assume, source sentence first."""
    notes: list[str] = []
    if from_odometer and integrated:
        notes.append(
            MIXED_SOURCE_NOTE.format(
                recordings=len(legs),
                odometer=len(from_odometer),
                speed=len(integrated),
                reason=integrated[0].reason,
            )
        )
    elif len(from_odometer) > 1:
        notes.append(MERGED_ODOMETER_NOTE.format(recordings=len(from_odometer)))
    elif from_odometer:
        notes.append(ODOMETER_NOTE)
    else:
        notes.append(SPEED_INTEGRATION_NOTE.format(reason=integrated[0].reason))

    if from_odometer:
        notes += [
            _odometer_coverage_note(from_odometer),
            (
                "a device that paused does not advance its odometer, so a "
                "recording stop costs no distance"
            ),
        ]
    if integrated:
        notes += [
            "rows with no speed reading contribute no distance",
            (
                "every reading counts, including the metres rolled below "
                f"{MOVING_SPEED_MS * MS_TO_KMH:g} km/h that moving time "
                "does not — a head unit's odometer counts those too"
            ),
            (
                "a reading the cleaner carried forward over a dropout (up "
                "to 29 s at the last good speed) contributes distance the "
                "wheel may not have turned; the repaired regions are "
                "listed on the stream itself"
            ),
        ]
    return tuple(notes)


def average_speed_kmh(
    distance: Assessment, basis: AveragingBasis | NotAssessed
) -> Assessment:
    """``distance / moving time``, in km/h — the ride-log average.

    Divided by the same :class:`AveragingBasis` as average power, which is what
    keeps "average speed 25.6" beside "average power 130" a single consistent
    claim about the same stretch of the ride rather than two numbers over two
    different clocks. Either input's reason is propagated unchanged: an average
    speed with no distance behind it should say "no speed was recorded", not
    invent a second sentence for the same fact.

    **The numerator is the whole ride and the denominator is not**, and
    that asymmetry is deliberate rather than an oversight. The row-set
    invariant applies to averages that are a *rate integrated over the
    same rows they are divided by* — average power sums watts over exactly the
    seconds :meth:`AveragingBasis.integrate` counted, so no dropout can leave
    numerator and denominator describing different stretches. Distance is not
    that kind of numerator. It is a **total for the ride**: every metre the
    wheel turned, including the metres rolled below the moving threshold, and
    it is read off the device's odometer, which has no per-row
    decomposition at all — the odometer knows where the ride ended, not which
    seconds of it were spent moving.

    So "average speed = the whole ride's distance ÷ the seconds spent moving"
    is the definition, and it is the same one the athlete's head unit and every
    platform they will compare against use. Restricting the numerator to the
    moving rows instead would report a *lower* average than the device on every
    ride with a traffic light in it — arithmetically defensible, universally
    read as a bug, and impossible to compute from an odometer anyway. The
    trade-off is stated in the explanation rather than left for a reader to
    discover: the few metres rolled below 1 km/h are in the numerator without
    their seconds being in the divisor.

    The load-duration note that average power carries is deliberately **not**
    here: average speed has no duration term in any load model, and
    attaching "TSS is still computed over recording time" to it answered a
    question nobody reading a km/h figure had asked.
    """
    if isinstance(distance, NotAssessed):
        return distance
    if isinstance(basis, NotAssessed):
        return basis
    hours = basis.seconds / 3600
    return Measured(
        value=distance.value / hours,
        explanation=MetricExplanation(
            formula=f"average speed = distance / {basis.label}",
            inputs=MappingProxyType(
                {
                    "distance": f"{distance.value:.2f} km",
                    basis.label: basis.described,
                }
            ),
            assumptions=(
                # The distance's own first assumption says which source
                # produced it; repeating it here is what stops a km/h
                # figure from being the one number on the page whose provenance
                # a reader has to go and look up.
                *distance.explanation.assumptions[:1],
                basis.assumption,
                # Gated, because the sentence is only true of a moving-time
                # divisor and the basis falls back for every indoor ride
                # (`AveragingBasis`): printing it there told the athlete their
                # average read *higher* than distance ÷ elapsed time directly
                # under an assumption saying the divisor **was** recording
                # time. Two assumptions on one number that contradict each
                # other are worse than either alone.
                (
                    "the distance covers the whole recording while the divisor "
                    "counts only the seconds spent moving — the convention a "
                    "head unit's average speed uses, and the reason this reads "
                    "higher than distance ÷ elapsed time"
                )
                if basis.from_moving_time
                else (
                    "the distance covers the whole recording and so does the "
                    "divisor — there was no moving time to divide by, so this "
                    "is distance ÷ recording time, which reads lower than the "
                    "average speed a head unit displays"
                ),
            ),
        ),
    )


def max_speed_kmh(speed_fixed: Sequence[float | None]) -> Assessment:
    """The fastest cleaned reading, in km/h.

    Over the ``_fixed`` column for :func:`channel_maximum`'s reason, and it
    matters more here than anywhere else: a maximum is the one statistic a
    single bad sample owns outright, and a GPS glitch writes 900 m/s.
    """
    present = _present(speed_fixed)
    if not present:
        return _absent("speed")
    return Measured(
        value=max(present) * MS_TO_KMH,
        explanation=MetricExplanation(
            formula="max speed = the largest cleaned reading × 3.6",
            inputs=MappingProxyType(
                {"samples": f"{len(present)} speed readings at 1 Hz"}
            ),
            assumptions=(
                "taken over the repaired column, so a GPS glitch is not the maximum",
            ),
        ),
    )


def stopped_time_s(
    *,
    elapsed_time_s: float,
    recording_time_s: float,
    basis: AveragingBasis | NotAssessed,
) -> Assessment:
    """Time the athlete is **known** not to have been travelling.

    ``elapsed − moving − the seconds the speed channel said nothing about``.

    Derived here rather than by whatever renders it, because the subtraction
    only means something against this system's definitions of its terms —
    elapsed is last sample minus first, moving is the rows of the cleaned speed
    column at or above :data:`app.domain.streams.MOVING_SPEED_MS` — and a
    client doing the arithmetic itself would be free to pair them with any
    other pair of durations it had to hand. It reads the same
    :class:`AveragingBasis` the averages divide by, so "stopped" and "the
    seconds the averages were taken over" cannot drift apart.

    It covers **both** kinds of standing still: the stops long enough that the
    head unit paused (elapsed − recording) and the traffic lights it kept
    recording through. Those are one number to an athlete asking why a
    90-minute ride took two hours, and two numbers only to the ingest pipeline.

    What it refuses to count as standing still is a second the speed
    channel had **no reading** for. A dropout is not a standstill, and
    ``elapsed − moving`` alone cannot tell them apart: a sensor that dies for
    half a ride would be reported as half an hour spent at the kerb. Those
    seconds are subtracted from the total instead and named in the inputs, and
    where the channel covers so little of the ride that the moving basis was
    refused altogether (:data:`SPEED_COVERAGE_FLOOR`) there is no honest
    subtraction left to make and the reason is returned instead.
    """
    if elapsed_time_s <= 0:
        return NotAssessed("this session records no elapsed time")
    if isinstance(basis, NotAssessed) or not basis.from_moving_time:
        return NotAssessed(
            "standing still cannot be told from riding here: "
            + (basis.reason if isinstance(basis, NotAssessed) else basis.assumption)
        )
    paused_s = max(0.0, elapsed_time_s - recording_time_s)
    return Measured(
        value=max(0.0, elapsed_time_s - basis.moving_s - basis.uncovered_s),
        explanation=MetricExplanation(
            formula=(
                "stopped = elapsed time − moving time − seconds with no speed reading"
            ),
            inputs=MappingProxyType(
                {
                    "elapsed": f"{elapsed_time_s:.0f} s",
                    "moving": f"{basis.moving_s:.0f} s",
                    "no speed reading": f"{basis.uncovered_s:.0f} s",
                    "of which the device paused for": f"{paused_s:.0f} s",
                }
            ),
            assumptions=(
                (
                    "counts every second below "
                    f"{MOVING_SPEED_MS * MS_TO_KMH:g} km/h, whether or not the "
                    "device stopped recording for it"
                ),
                (
                    "a second the speed channel had no reading for is not "
                    "counted as standing still — nothing is known about it"
                ),
                (
                    f"{paused_s:.0f} s of this is time the device was not "
                    "recording at all: a pause, or — on a session merged from "
                    "more than one file (WP-6.5) — the gap between them, which "
                    "the athlete may not have spent beside the bike. Nothing "
                    "in the data distinguishes them"
                ),
            ),
        ),
    )


def channel_average(label: str, values: Sequence[float | None]) -> Assessment:
    """Mean of a channel's readings, stops and dropouts excluded."""
    present = _present(values)
    if not present:
        return _absent(label)
    return Measured(
        value=sum(present) / len(present),
        explanation=MetricExplanation(
            formula=f"average {label} = Σ {label} / number of readings",
            inputs=MappingProxyType({"readings": f"{len(present)} at 1 Hz"}),
            assumptions=("rows with no reading are excluded, never read as zero",),
        ),
    )


def average_cadence(cadence_fixed: Sequence[float | None]) -> Assessment:
    """Mean cadence over the rows the athlete was **pedalling**.

    Zero-rpm rows are coasting, and every head unit, Strava and intervals.icu
    leave them out of the average — so arc does too. The difference is not
    small and it is not a rounding argument: on the reference ride, 356 of the
    5 738 recorded seconds were spent freewheeling — leaving 5 382 pedalling —
    which drags a mean-over-everything down to 77.7 rpm against the 82.8 the
    athlete's own screen and every platform showed. A number nobody else
    produces is read as a wrong number, however carefully it is defined.

    What makes it honest rather than merely conventional is that the excluded
    seconds are **reported**: the explanation carries how many rows sat at
    0 rpm, so "83 rpm" cannot quietly describe forty minutes of a two-hour
    ride. Coasting has a metric of its own too (:func:`coasting_time_s`), which
    is the same fact measured from power and speed instead.

    A cadence column that never left zero has no pedalling to average, and says
    so rather than reporting 0 rpm — which is a claim about how the athlete
    rode, not about what the sensor recorded.
    """
    present = _present(cadence_fixed)
    if not present:
        return _absent("cadence")
    pedalling = [rpm for rpm in present if rpm > 0]
    coasting = len(present) - len(pedalling)
    if not pedalling:
        return NotAssessed(
            f"every one of the {len(present)} cadence readings is 0 rpm, so "
            "there is no pedalling to average"
        )
    return Measured(
        value=sum(pedalling) / len(pedalling),
        explanation=MetricExplanation(
            formula="average cadence = Σ cadence / rows above 0 rpm",
            inputs=MappingProxyType(
                {
                    "readings": f"{len(pedalling)} of {len(present)} at 1 Hz",
                    "coasting": f"{coasting} of {len(present)} s at 0 rpm",
                }
            ),
            assumptions=(
                (
                    "coasting (0 rpm) excluded — the convention every head unit "
                    "and analysis platform averages by; including those seconds "
                    "reports a lower number than the athlete's own device did"
                ),
                "rows with no reading are excluded, never read as zero",
            ),
        ),
    )


def channel_maximum(label: str, values: Sequence[float | None]) -> Assessment:
    """Largest reading of a channel, over the cleaned column.

    Over the ``_fixed`` column on purpose: the raw column may still hold the
    1 900 W spike from a dropped magnet, and a maximum is the one statistic
    a single bad sample owns outright.
    """
    present = _present(values)
    if not present:
        return _absent(label)
    return Measured(
        value=max(present),
        explanation=MetricExplanation(
            formula=f"max {label} = the largest cleaned reading",
            inputs=MappingProxyType({"readings": f"{len(present)} at 1 Hz"}),
            assumptions=(
                "taken over the repaired column, so a clipped spike is not the maximum",
            ),
        ),
    )


def channel_minimum(label: str, values: Sequence[float | None]) -> Assessment:
    """Smallest reading of a channel, over the cleaned column.

    The mirror of :func:`channel_maximum`, and it exists for one channel:
    temperature, where the low is as much of the ride's story as the high — a
    dawn start at 4 °C and an afternoon at 24 °C is why the second half felt
    different, and an average alone hides it.
    """
    present = _present(values)
    if not present:
        return _absent(label)
    return Measured(
        value=min(present),
        explanation=MetricExplanation(
            formula=f"min {label} = the smallest cleaned reading",
            inputs=MappingProxyType({"readings": f"{len(present)} at 1 Hz"}),
            assumptions=(
                "taken over the repaired column, so a dropout is not the minimum",
            ),
        ),
    )


def _centred_mean(values: Sequence[float], window: int) -> list[float]:
    """Each value replaced by the mean of the ``window`` rows centred on it.

    The two ends are **reflected through the endpoint** rather than truncated:
    the row ``k`` before the start is read as ``2·v[0] − v[k]``, and the row
    ``k`` after the end as ``2·v[-1] − v[k-from-end]``. That extension is
    antisymmetric, so it continues the trace's own slope instead of inventing a
    plateau, and it is exactly what makes the smoothing *unbiased* at the ends:
    over any straight run the reflected window's mean is the row's own value,
    so a ride that begins or ends mid-climb loses nothing.

    Shrinking the window instead — the shape this had — quietly costs about
    ``half_window × slope / 2`` metres at each end, because the first row's
    "centred" mean is really the mean of the half-window *above* it. On a
    100 m climb sampled at a metre a second that is 3.5 m at each end, 7 % of
    the climb, and it is invisible in any test whose trace starts and ends on
    the flat. Repeating the endpoint (an even reflection) would be worse still:
    it flattens the ends deliberately, which is the effect this replaced.
    """
    if window <= 1 or len(values) <= 1:
        return list(values)
    half = window // 2
    length = len(values)
    first, last = values[0], values[-1]
    padded = (
        [2 * first - values[min(k, length - 1)] for k in range(half, 0, -1)]
        + list(values)
        + [2 * last - values[max(length - 1 - k, 0)] for k in range(1, half + 1)]
    )
    prefix = [0.0]
    for value in padded:
        prefix.append(prefix[-1] + value)
    span = 2 * half + 1
    return [
        (prefix[index + span] - prefix[index]) / span for index in range(len(values))
    ]


def elevation_gain_m(
    elevation_fixed: Sequence[float | None],
    *,
    hysteresis_m: float = ELEVATION_HYSTERESIS_M,
    smoothing_s: int = ELEVATION_SMOOTHING_S,
) -> Assessment:
    """Total ascent: climbs of at least ``hysteresis_m``, over a smoothed trace.

    Summing every positive delta of a barometric altimeter counts its noise —
    a metre of drift each way, once a second, is hundreds of metres of
    imaginary climbing over a flat four-hour ride. Two filters in series, and
    they catch different things:

    1. **Averaged** over a centred :data:`ELEVATION_SMOOTHING_S` window, which
       removes the high-frequency part of the wander. Terrain does not change
       in a second; pressure does. The two ends of the trace are reflected
       rather than truncated (:func:`_centred_mean`), so a ride that starts or
       finishes mid-climb is not charged half a window of it.
    2. **Banked by climb, not by step.** The trace is walked keeping the
       running valley and the running peak. A climb becomes real once the peak
       stands ``hysteresis_m`` above the valley, and it is banked **in full** —
       peak minus valley, every metre of it — when the trace turns back down by
       ``hysteresis_m`` from that peak, which is what makes the turn a descent
       rather than more wander. The last climb is banked at the end of the
       series.

    The second rule is the one that matters for long drags. Thresholding each
    *step* instead — the shape this function used to have — charges the
    threshold again at every wobble inside one climb, so a rolling hour loses
    metres it really gained while a flat hour still gains metres it did not.
    Here the threshold is paid once per climb, so the sentence in the
    explanation ("climbs smaller than 3 m are not counted") is the literal
    rule, and a 400 m alpine climb reports 400 m however noisy the trace is
    inside it.

    Args:
        elevation_fixed: The cleaned altitude column, in metres.
        hysteresis_m: Metres a climb must gain to count, and metres the trace
            must fall to close it.
        smoothing_s: Width of the centred averaging window, in rows (1 Hz, so
            seconds). ``1`` disables the averaging.

    Reference: the threshold is GoldenCheetah's ``GC_ELEVATION_HYSTERESIS``
    default; see :data:`ELEVATION_HYSTERESIS_M`.
    """
    present = _present(elevation_fixed)
    if not present:
        return _absent("elevation")
    trace = _centred_mean(present, smoothing_s)

    gain = 0.0
    climbs = 0
    valley = peak = trace[0]
    climbing = False
    for value in trace[1:]:
        if climbing:
            if value > peak:
                peak = value
            elif peak - value >= hysteresis_m:
                gain += peak - valley
                climbs += 1
                valley = value
                climbing = False
        elif value < valley:
            valley = value
        elif value - valley >= hysteresis_m:
            climbing = True
            peak = value
    if climbing:
        gain += peak - valley
        climbs += 1

    return Measured(
        value=gain,
        explanation=MetricExplanation(
            formula=(
                "elevation gain = Σ (peak − valley) over the climbs that gain "
                f"at least {hysteresis_m:g} m"
            ),
            inputs=MappingProxyType(
                {
                    "readings": f"{len(present)} at 1 Hz",
                    "smoothing": f"centred {smoothing_s} s mean of the altitude trace",
                    "climbs counted": str(climbs),
                }
            ),
            assumptions=(
                (
                    f"climbs smaller than {hysteresis_m:g} m are not counted — "
                    "a barometric altimeter wanders by that much while standing "
                    "still, and counting the wander is how a flat ride grows "
                    "hundreds of metres of ascent"
                ),
                (
                    f"the trace is averaged over a centred {smoothing_s} s "
                    "window first, so a one-second pressure blip is not a hill; "
                    "the cost is that a sharp summit reads about a metre lower "
                    "than the raw trace, and it is the only one — the ends of "
                    "the trace are reflected rather than truncated, so a ride "
                    "that begins or ends mid-climb keeps those metres"
                ),
                (
                    "a climb is banked in full once it clears the threshold, so "
                    "a long drag is not charged the threshold once per wobble"
                ),
            ),
            citation=_GOLDEN_CHEETAH,
        ),
    )


# --- HRSS (work order A-3, Appendix A.2) --------------------------------------


def hrss(
    hr_fixed: Sequence[float | None],
    *,
    resting_hr: AnchorVersion | None,
    max_hr: AnchorVersion | None,
    lthr: AnchorVersion | None,
    sex: Sex,
) -> Assessment:
    """Heart-rate training load by **per-sample** integration (A5.3).

    ::

        HRr(t)      = max((HR(t) − HR_rest) / (HR_max − HR_rest), 0)
        TRIMP       = Σ (Δt / 60) × HRr × c × e^(k × HRr)
        HRr_LT      = (LTHR − HR_rest) / (HR_max − HR_rest)
        TRIMP_LT_1h = 60 × HRr_LT × c × e^(k × HRr_LT)
        HRSS        = 100 × TRIMP / TRIMP_LT_1h

    with ``c = 0.64`` and ``k = 1.92`` (male) or ``1.67`` (female).

    **Per-sample, not per-session.** The widely-copied variant computes one
    TRIMP from the session's *average* HR. By Jensen's inequality
    ``e^(k·x̄) ≤ mean(e^(k·xᵢ))``, so that form systematically under-reports
    variable-intensity sessions — intervals, and every strength session, where
    HR swings between sets — which is precisely the class of session HR load
    exists for. On the 1 Hz grid the honest form is one pass over the column,
    so there is no cost argument for the wrong one.

    Note the deliberate asymmetry: the per-sample reserve is clamped at zero
    (a heart rate below resting is a measurement artefact, not negative
    training), while the threshold reserve conventionally is not — which is
    why ``LTHR <= HR_rest`` has to be guarded rather than clamped.

    Every guard returns a :class:`NotAssessed` naming the input that is
    missing, so the screen can say which anchor to enter.
    """
    missing = [
        name
        for name, version in (
            ("resting HR", resting_hr),
            ("max HR", max_hr),
            ("threshold HR (LTHR)", lthr),
        )
        if version is None
    ]
    if resting_hr is None or max_hr is None or lthr is None:
        return NotAssessed(
            "no anchor is in force for "
            + ", ".join(missing)
            + " — HRSS needs all three"
        )
    if sex is Sex.UNSPECIFIED:
        return NotAssessed(
            "the HRSS coefficient is sex-dependent and this athlete's sex is "
            "unspecified"
        )
    if max_hr.value <= resting_hr.value:
        return NotAssessed("max HR is not above resting HR")
    if lthr.value <= resting_hr.value:
        return NotAssessed("threshold HR is not above resting HR")

    present = _present(hr_fixed)
    if not present:
        return _absent("heart rate")

    coefficient = HRSS_K[sex]
    reserve = max_hr.value - resting_hr.value
    trimp = 0.0
    for beats in present:
        fraction = max((beats - resting_hr.value) / reserve, 0.0)
        trimp += (1 / 60) * fraction * HRSS_C * math.exp(coefficient * fraction)
    threshold_fraction = (lthr.value - resting_hr.value) / reserve
    trimp_lt_1h = (
        60 * threshold_fraction * HRSS_C * math.exp(coefficient * threshold_fraction)
    )
    return Measured(
        value=100 * trimp / trimp_lt_1h,
        explanation=MetricExplanation(
            formula=(
                "HRr = (HR − HR_rest) / (HR_max − HR_rest); "
                "TRIMP = Σ (Δt/60) × HRr × 0.64 × e^(k × HRr); "
                "HRSS = 100 × TRIMP / TRIMP at threshold for one hour"
            ),
            inputs=MappingProxyType(
                {
                    "resting HR": describe_anchor(resting_hr),
                    "max HR": describe_anchor(max_hr),
                    "threshold HR": describe_anchor(lthr),
                    "k": f"{coefficient} ({sex.value})",
                    "samples": f"{len(present)} heart-rate readings at 1 Hz",
                }
            ),
            assumptions=(
                (
                    "integrated per sample, not from the session's average "
                    "heart rate — the average form under-reports variable "
                    "sessions"
                ),
                "heart rates below resting count as zero reserve",
            ),
            citation=_BANISTER,
        ),
    )


# --- dual load and its selection (work order A-4, A5.2) -----------------------


@dataclass(frozen=True, slots=True)
class SelectedLoad:
    """Both load models, the one that was used, and the rule that chose it.

    Args:
        training_load: The selected value — what the week rail totals.
        basis: Which model it came from.
        rule: Why that one, in a sentence the screen can print.
        power_load: The power-model load, when it could be computed.
        hr_load: The HR-model load, when it could be computed. Stored even
            when it was not selected: watching the two track each other on the
            days both exist is the only way to learn whether the HR number can
            be trusted on the days only it exists (A5.2).
        explanation: How the selected number was arrived at.
    """

    training_load: float
    basis: LoadBasis
    rule: str
    power_load: float | None
    hr_load: float | None
    explanation: MetricExplanation


def select_training_load(
    power_load: Assessment,
    hr_load: Assessment,
    discipline: SessionDiscipline,
) -> SelectedLoad | NotAssessed:
    """Choose between the power and HR load models, and say why (A5.2).

    Power wins for cycling when it exists: it measures the work done rather
    than the body's response to it, and it is immune to heat, caffeine and
    cardiac drift. For strength — and for anything else a device recorded —
    there is no meaningful power trace, so the HR model is the only one that
    describes the session at all.

    Both values are carried through whichever is selected. Neither computable
    returns a :class:`NotAssessed` carrying **both** reasons, because "no
    power meter" and "no heart-rate strap" are two different things to fix.

    The selected model's own explanation is kept and the selection rule is
    appended to it as one more assumption, so a reader asking "why 79?" gets
    the arithmetic *and* the choice rather than a sentence about choosing that
    has forgotten how the number was computed.
    """
    power_value = value_of(power_load)
    hr_value = value_of(hr_load)
    prefers_power = discipline is SessionDiscipline.CYCLING

    if prefers_power and isinstance(power_load, Measured):
        chosen, basis = power_load, LoadBasis.POWER
        rule = "power available and preferred for cycling"
    elif isinstance(hr_load, Measured):
        chosen, basis = hr_load, LoadBasis.HR
        rule = (
            "no power was recorded, so the heart-rate model was used"
            if prefers_power
            else f"a {discipline.value} session has no meaningful power trace, "
            "so the heart-rate model was used"
        )
    elif isinstance(power_load, Measured):
        chosen, basis = power_load, LoadBasis.POWER
        rule = (
            f"a {discipline.value} session prefers the heart-rate model, but no "
            "heart rate was recorded, so the power model was used"
        )
    else:
        reasons = [
            assessment.reason
            for assessment in (power_load, hr_load)
            if isinstance(assessment, NotAssessed)
        ]
        return NotAssessed("; ".join(reasons) or "neither load model could be computed")

    return SelectedLoad(
        training_load=chosen.value,
        basis=basis,
        rule=rule,
        power_load=power_value,
        hr_load=hr_value,
        explanation=MetricExplanation(
            formula=chosen.explanation.formula,
            inputs=MappingProxyType(
                dict(chosen.explanation.inputs)
                | {
                    "power model": (
                        f"{power_value:.1f}" if power_value is not None else "—"
                    ),
                    "heart-rate model": (
                        f"{hr_value:.1f}" if hr_value is not None else "—"
                    ),
                }
            ),
            assumptions=(*chosen.explanation.assumptions, rule),
            citation=chosen.explanation.citation,
        ),
    )


# --- time in zone and polarization (work order A-5, Appendix A.3) -------------


#: How the zones of each model collapse into easy / moderate / hard (A.3).
#:
#: The addenda's band numbers (easy 1-2, moderate 3-4, hard 5-7) are the
#: **power** model's, and the rule behind them is where *threshold* sits:
#: `coggan_7`'s Z4 is Threshold (90-105 %FTP) and lands in **moderate**, so
#: hard begins above it at Z5.
#:
#: `lthr_5` has five bands, and the same rule places them differently from a
#: naive re-use of those integers. Its Z4 is `SubThreshold`
#: (94-100 %LTHR) — below LTHR, so moderate — and Z5 (`SuperThreshold`) is the
#: only band above threshold, so hard is Z5 alone. Putting Z4 in hard, as an
#: earlier version of this table did, counted every tempo-to-threshold minute
#: as hard riding and inflated the polarization index of exactly the sessions
#: it exists to describe.
THREE_ZONE_BANDS: Mapping[ZoneModel, tuple[frozenset[int], ...]] = MappingProxyType(
    {
        ZoneModel.COGGAN_7: (
            frozenset({1, 2}),
            frozenset({3, 4}),
            frozenset({5, 6, 7}),
        ),
        ZoneModel.LTHR_5: (frozenset({1, 2}), frozenset({3, 4}), frozenset({5})),
    }
)


@dataclass(frozen=True, slots=True)
class ZoneTime:
    """Seconds spent in one band of one zone model."""

    index: int
    name: str
    seconds: float


@dataclass(frozen=True, slots=True)
class TimeInZone:
    """One channel's zone distribution, and the three-zone view of it.

    Args:
        model: The zone model the bands came from. Pinned on the artefact
            beside the anchor version, because ``(anchor, model) -> zones`` is
            only deterministic while the model is recorded (A5.5).
        zones: One entry per band, ascending, including the empty ones — a
            zone with no time in it is a fact about the ride, and dropping it
            would make the bar chart's shape depend on the data.
        total_s: Seconds that fell in any band; the denominator of the
            fractions below.
        easy_s: Seconds in the easy bands.
        moderate_s: Seconds in the moderate bands.
        hard_s: Seconds in the hard bands.
        polarization_index: Treff's PI, or the reason it could not be taken.
        explanation: How the distribution was arrived at.
    """

    model: ZoneModel
    zones: tuple[ZoneTime, ...]
    total_s: float
    easy_s: float
    moderate_s: float
    hard_s: float
    polarization_index: Assessment
    explanation: MetricExplanation


def time_in_zone(
    values: Sequence[float | None],
    zones: Sequence[Zone],
    model: ZoneModel,
    *,
    anchor: AnchorVersion | None = None,
) -> TimeInZone | NotAssessed:
    """Seconds per zone over a cleaned channel, and the three-zone collapse.

    One second per row, so the totals sum to the number of rows carrying a
    reading — which is recording time minus this channel's own dropouts, not
    elapsed time. Bands are half-open and contiguous
    (`app.domain.zones.Zone.contains`), so every reading lands in exactly one.

    ``values`` must be in the model's own unit: watts for ``coggan_7``, beats
    per minute for ``lthr_5``. The caller converts, and the two enums that
    both spell "power" (`app.domain.workout.Channel` and
    `app.domain.streams.StreamChannel`) are never crossed by string.
    """
    present = _present(values)
    if not present:
        return _absent("data for this channel")
    if not zones:
        return NotAssessed("the zone model produced no bands")

    seconds = dict.fromkeys((zone.index for zone in zones), 0.0)
    for reading in present:
        band = next((zone for zone in zones if zone.contains(reading)), None)
        if band is not None:
            seconds[band.index] += 1.0

    easy_band, moderate_band, hard_band = THREE_ZONE_BANDS[model]
    easy_s = sum(value for index, value in seconds.items() if index in easy_band)
    moderate_s = sum(
        value for index, value in seconds.items() if index in moderate_band
    )
    hard_s = sum(value for index, value in seconds.items() if index in hard_band)
    total_s = sum(seconds.values())
    inputs = {"readings": f"{len(present)} at 1 Hz", "zone model": model.value}
    if anchor is not None:
        inputs["anchor"] = describe_anchor(anchor)
    return TimeInZone(
        model=model,
        zones=tuple(
            ZoneTime(index=zone.index, name=zone.name, seconds=seconds[zone.index])
            for zone in zones
        ),
        total_s=total_s,
        easy_s=easy_s,
        moderate_s=moderate_s,
        hard_s=hard_s,
        polarization_index=polarization_index(easy_s, moderate_s, hard_s),
        explanation=MetricExplanation(
            formula="time in zone = one second per reading, banded by the model",
            inputs=MappingProxyType(inputs),
            assumptions=(
                (
                    "rows with no reading are excluded, so the bands total "
                    "this channel's coverage rather than the ride's elapsed "
                    "time"
                ),
            ),
        ),
    )


def polarization_index(easy_s: float, moderate_s: float, hard_s: float) -> Assessment:
    """Treff's polarization index over the three-zone split (Appendix A.3).

    ``PI = log10( (Z_easy / Z_moderate) × Z_hard × 100 )``, where each term is
    a **fraction of the banded total**. Above 2.0 is the conventional
    "polarized" threshold; a typical 80/5/15 split gives 2.38 and a pyramidal
    80/15/5 gives 1.43.

    A zero in any band makes the expression ``-inf``, ``+inf`` or a division
    by zero, none of which is a training distribution — so a degenerate split
    returns :class:`NotAssessed` naming the empty band rather than a number
    that would sort as the most polarized session ever ridden.
    """
    total = easy_s + moderate_s + hard_s
    if total <= 0:
        return NotAssessed("no time fell in any zone")
    empty = [
        name
        for name, seconds in (
            ("easy", easy_s),
            ("moderate", moderate_s),
            ("hard", hard_s),
        )
        if seconds <= 0
    ]
    if empty:
        return NotAssessed(
            "the polarization index needs time in all three bands; there was "
            "none in the " + " or ".join(empty) + " band"
        )
    easy, moderate, hard = easy_s / total, moderate_s / total, hard_s / total
    return Measured(
        value=math.log10((easy / moderate) * hard * 100),
        explanation=MetricExplanation(
            formula="PI = log10( (Z_easy / Z_moderate) × Z_hard × 100 )",
            inputs=MappingProxyType(
                {
                    "easy": f"{easy:.1%}",
                    "moderate": f"{moderate:.1%}",
                    "hard": f"{hard:.1%}",
                }
            ),
            assumptions=("fractions are of the banded total, not of elapsed time",),
            citation=_TREFF,
        ),
    )


# --- strength volume (work order A-6) ----------------------------------------


@dataclass(frozen=True, slots=True)
class PerformedSet:
    """One set as it was logged. The domain's view of a `LoggedSetRow`."""

    reps: int
    load_kg: float | None


@dataclass(frozen=True, slots=True)
class StrengthVolume:
    """What a strength session actually moved. **Not** a load.

    Kilograms, on a different axis from TSS (v2 §5.4): never summed with
    endurance load, never converted to it, never rendered in the same column.
    Mirrors `app.domain.prediction.PredictedVolume`'s honesty so the planned
    and performed numbers are the same quantity.

    Args:
        volume_load_kg: ``Σ reps × kg`` over the sets logged in kilograms, or
            ``None`` when none of them was.
        sets_completed: Every set logged, whatever its load — the honest
            denominator, and a bodyweight session is still work.
        coverage: Fraction of :attr:`sets_completed` that carried kilograms.
        explanation: How the number was arrived at.
    """

    volume_load_kg: float | None
    sets_completed: int
    coverage: float
    explanation: MetricExplanation


def strength_volume(sets: Sequence[PerformedSet]) -> StrengthVolume | NotAssessed:
    """Volume load and set count over the logged sets.

    Only sets logged in kilograms contribute kilograms — a bodyweight set has
    no load to multiply and inventing one from the athlete's weight would put
    a made-up number in the same column as measured ones. What that leaves out
    is reported as :attr:`StrengthVolume.coverage` rather than hidden.
    """
    if not sets:
        return NotAssessed("no sets were logged")
    counted = [entry for entry in sets if entry.load_kg is not None]
    volume = sum(entry.reps * (entry.load_kg or 0.0) for entry in counted)
    coverage = len(counted) / len(sets)
    return StrengthVolume(
        volume_load_kg=volume if counted else None,
        sets_completed=len(sets),
        coverage=coverage,
        explanation=MetricExplanation(
            formula="volume load = Σ reps × kg over the sets logged in kilograms",
            inputs=MappingProxyType(
                {
                    "sets": str(len(sets)),
                    "sets carrying kilograms": f"{len(counted)} ({coverage:.0%})",
                }
            ),
            assumptions=(
                (
                    "bodyweight and unloaded sets contribute no kilograms and "
                    "are left out of coverage"
                ),
                (
                    "kilograms, never a training load — the two are different "
                    "axes and must not be added"
                ),
            ),
        ),
    )


# --- aggregating zone time across sessions (A5.4's one rule) ------------------

#: The stated priority A5.4 requires. Summing a session's power zones **and**
#: its heart-rate zones counts its duration twice, which is the one mistake
#: that makes a weekly distribution meaningless — so exactly one channel is
#: counted per session, and this sentence travels with the total that results.
ONE_CHANNEL_PER_SESSION_RULE = (
    "one channel per session — the same one the session's training load came "
    "from (power where it was recorded, otherwise heart rate) — so no "
    "session's duration is counted twice"
)


def zone_channel_for_aggregation(
    basis: LoadBasis | None, *, power_available: bool, hr_available: bool
) -> LoadBasis | None:
    """Which channel's zone times to count for one session, or ``None``.

    The session's own load basis leads, because that is the channel the
    session is already denominated in everywhere else; where it produced no
    distribution — a strength session's HR load with no zones, a ride whose
    FTP anchor was missing — the other channel is used, and a session with
    neither contributes nothing rather than a zero.

    See :data:`ONE_CHANNEL_PER_SESSION_RULE` for the sentence that has to be
    rendered beside any total this feeds.
    """
    preferred = (
        (LoadBasis.POWER, power_available)
        if basis is not LoadBasis.HR
        else (LoadBasis.HR, hr_available)
    )
    if preferred[1]:
        return preferred[0]
    if power_available:
        return LoadBasis.POWER
    if hr_available:
        return LoadBasis.HR
    return None
