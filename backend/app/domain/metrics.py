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
slot and renders the reason (`.claude/rules/frontend-ui-conventions.md` rule
4); WP-7's scoring axes reuse the same type, so "not assessed" means one thing
across the product.

**Nulls are stops.** Every function here takes a ``*_fixed`` column exactly as
`app.domain.streams` produced it: ``Sequence[float | None]`` on the 1 Hz grid,
with ``None`` for a recording stop, a dropout the cleaner declined to repair,
or a channel that simply was not recording yet. Null rows are **excluded**
everywhere — never read as zero.

**Two durations, and which number gets which** (D194). An *average* is divided
by :class:`AveragingBasis` — moving time, the seconds the athlete was actually
travelling — because that is the quantity "average power" and "average speed"
name on every head unit and every other platform, and a divisor nobody else
uses makes a correct number read as a wrong one. The *load chain* is untouched:
NP is still a rolling window over the recorded series and TSS's duration term
is still `recording time` (A4.4, A5.1). The split is deliberate and each
explanation says which side of it its number stands on.
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

#: Metres a climb must gain over the running low point before it counts
#: (D120). Barometric altimeters wander by a metre or two while standing
#: still, and summing raw positive deltas turns that wander into hundreds of
#: metres of "climbing" on a flat ride.
ELEVATION_HYSTERESIS_M = 2.0

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


# --- the averaging basis (D194) -----------------------------------------------


#: What the load chain's duration term is, restated wherever an average has to
#: say that it is **not** that number. One sentence, one place: an average and
#: a load that disagree about their divisor are a support question, and the
#: answer has to be identical every time it is asked.
LOAD_DURATION_NOTE = (
    "the training load's duration term is unaffected: TSS is still computed "
    "over recording time (A5.1), not over this basis"
)


@dataclass(frozen=True, slots=True)
class AveragingBasis:
    """The denominator a ride's **averages** are divided by (D194).

    Moving time where the ride recorded speed: the seconds the athlete was
    actually travelling, at or above :data:`app.domain.streams.MOVING_SPEED_MS`.
    That is what "average power" and "average speed" mean on a head unit and on
    every platform this one will be compared against, and an average taken over
    a divisor nobody else uses is reported as a bug about a number that is
    arithmetically fine.

    Recording time is the **fallback**, not a second policy: an indoor session
    with no speed channel has no moving time to divide by, and refusing to
    report an average power for it would be a worse answer than reporting one
    over the duration that does exist. :attr:`from_moving_time` says which
    happened, and every explanation built from this basis prints
    :attr:`described`, so the two cases are never confused on screen.

    Args:
        seconds: The divisor. Always above zero.
        label: What it is called, for a formula line.
        described: The same, with its number and its definition, for an input
            line.
        from_moving_time: Whether moving time supplied it.
    """

    seconds: float
    label: str
    described: str
    from_moving_time: bool


def averaging_basis(
    moving_time_s: float, recording_time_s: float
) -> AveragingBasis | NotAssessed:
    """Pick the divisor for this session's averages, or say there is none.

    Returns:
        The basis, or a :class:`NotAssessed` when the session recorded neither
        duration — a manual session whose columns are empty anyway.
    """
    if moving_time_s > 0:
        return AveragingBasis(
            seconds=moving_time_s,
            label="moving time",
            described=(
                f"{moving_time_s:.0f} s of moving time "
                f"(speed ≥ {MOVING_SPEED_MS * MS_TO_KMH:g} km/h)"
            ),
            from_moving_time=True,
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
        )
    return NotAssessed("this session records no duration to average over")


def _basis_assumptions(basis: AveragingBasis) -> tuple[str, ...]:
    """What an average over ``basis`` had to assume, in order."""
    if basis.from_moving_time:
        return (
            (
                "divided by moving time, the seconds spent travelling — the "
                "same basis a head unit averages over"
            ),
            LOAD_DURATION_NOTE,
        )
    return (
        (
            "no speed was recorded, so there is no moving time; the divisor is "
            "recording time — elapsed minus every stop over 30 s — and this "
            "reads lower than the average a head unit would display"
        ),
        LOAD_DURATION_NOTE,
    )


# --- the power chain (work order A-2, Appendix A.1) ----------------------------


def average_power(
    power_fixed: Sequence[float | None], basis: AveragingBasis | NotAssessed
) -> Assessment:
    """Total work divided by the averaging basis — moving time (D194).

    ``average_power = Σ P × Δt / basis`` (Appendix A.1, with D194's divisor).
    Rows with no power reading contribute no joules while still costing their
    second, so a ride whose meter dropped out for ten minutes averages lower
    than the ten minutes it lost — which is the honest reading and is stated
    as an assumption rather than hidden.

    The basis is moving time wherever speed was recorded, which is what a head
    unit divides by; :class:`AveragingBasis` documents the fallback and why it
    exists. What this is **not** is the load's duration term: TSS still divides
    the ride by recording time (A5.1), and every explanation says so, because
    an average and a load that appear to disagree about how long the ride was
    is exactly the question this data is here to answer.
    """
    if isinstance(basis, NotAssessed):
        return basis
    present = _present(power_fixed)
    if not present:
        return _absent("power")
    joules = sum(present)
    return Measured(
        value=joules / basis.seconds,
        explanation=MetricExplanation(
            formula=f"average power = Σ P × Δt / {basis.label}",
            inputs=MappingProxyType(
                {
                    "work": f"{joules / 1000:.0f} kJ over {len(present)} power samples",
                    basis.label: basis.described,
                }
            ),
            assumptions=(
                "rows with no power reading contribute no work",
                *_basis_assumptions(basis),
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
    np_watts: float, average_watts: float, *, basis_label: str = "moving time"
) -> Assessment:
    """``NP / average power`` — how ragged the ride was.

    1.0 is a perfectly steady effort; a criterium sits well above 1.1. It
    inherits :func:`average_power`'s divisor, which since D194 is moving time —
    the same basis a head unit averages over, so the ratio is now comparable
    with the one a head unit would show.

    Args:
        np_watts: Normalized power.
        average_watts: The average power this is taken against.
        basis_label: What that average was divided by, named in the
            explanation so a reader can tell a moving-time ratio from the
            recording-time one a speed-less session falls back to.
    """
    if average_watts <= 0:
        return NotAssessed("average power is zero, so there is no ratio to take")
    return Measured(
        value=np_watts / average_watts,
        explanation=MetricExplanation(
            formula="variability index = NP / average power",
            inputs=MappingProxyType(
                {
                    "NP": f"{np_watts:.0f} W",
                    "average power": f"{average_watts:.0f} W (work ÷ {basis_label})",
                }
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


def distance_km(speed_fixed: Sequence[float | None]) -> Assessment:
    """How far the ride went: ``Σ v × Δt / 1000`` over the speed column.

    Integrated from **speed**, not from the latitude/longitude track. The two
    disagree, and the disagreement is not noise: a GPS trace through a tunnel
    or under a plane-tree avenue draws a straight line the athlete did not
    ride, and a trace from a device sampling every four seconds cuts every
    corner. The speed channel is what the wheel magnet or the trainer measured,
    which is the same quantity the head unit's odometer integrates — so this
    number is the one an athlete can check against their own screen.

    Δt is one second by construction (A4.1's grid), so the sum of the column is
    already metres. Rows with no reading contribute no distance, which makes a
    ride whose speed sensor dropped out read short — stated as an assumption
    rather than papered over with an interpolation nobody could audit.
    """
    present = _present(speed_fixed)
    if not present:
        return _absent("speed")
    return Measured(
        value=sum(present) / 1000,
        explanation=MetricExplanation(
            formula="distance = Σ v × Δt / 1000, with Δt = 1 s",
            inputs=MappingProxyType(
                {"samples": f"{len(present)} speed readings at 1 Hz"}
            ),
            assumptions=(
                (
                    "integrated from the speed channel, not from the GPS track "
                    "— it is the quantity a head unit's odometer counts"
                ),
                "rows with no speed reading contribute no distance",
            ),
        ),
    )


def average_speed_kmh(
    distance: Assessment, basis: AveragingBasis | NotAssessed
) -> Assessment:
    """``distance / moving time``, in km/h — the ride-log average (D194).

    Divided by the same :class:`AveragingBasis` as average power, which is what
    keeps "average speed 25.6" beside "average power 130" a single consistent
    claim about the same stretch of the ride rather than two numbers over two
    different clocks. Either input's reason is propagated unchanged: an average
    speed with no distance behind it should say "no speed was recorded", not
    invent a second sentence for the same fact.
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
            assumptions=_basis_assumptions(basis),
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
    *, elapsed_time_s: float, moving_time_s: float, recording_time_s: float
) -> Assessment:
    """Time the athlete was not travelling: ``elapsed − moving``.

    Derived here rather than by whatever renders it, because the subtraction
    only means something against this system's definitions of its two terms —
    elapsed is last sample minus first, moving is time at or above
    :data:`app.domain.streams.MOVING_SPEED_MS` — and a client doing the
    arithmetic itself would be free to pair them with any other pair of
    durations it had to hand.

    It covers **both** kinds of standing still: the stops long enough that the
    head unit paused (elapsed − recording) and the traffic lights it kept
    recording through. Those are one number to an athlete asking why a
    90-minute ride took two hours, and two numbers only to the ingest pipeline.

    Not assessed without a speed channel: ``elapsed − 0`` would claim the whole
    ride was spent at a standstill.
    """
    if elapsed_time_s <= 0:
        return NotAssessed("this session records no elapsed time")
    if moving_time_s <= 0:
        return NotAssessed(
            "no speed was recorded, so standing still cannot be told from riding"
        )
    paused_s = max(0.0, elapsed_time_s - recording_time_s)
    return Measured(
        value=max(0.0, elapsed_time_s - moving_time_s),
        explanation=MetricExplanation(
            formula="stopped = elapsed time − moving time",
            inputs=MappingProxyType(
                {
                    "elapsed": f"{elapsed_time_s:.0f} s",
                    "moving": f"{moving_time_s:.0f} s",
                    "of which the device paused for": f"{paused_s:.0f} s",
                }
            ),
            assumptions=(
                (
                    "counts every second below "
                    f"{MOVING_SPEED_MS * MS_TO_KMH:g} km/h, whether or not the "
                    "device stopped recording for it"
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


def elevation_gain_m(
    elevation_fixed: Sequence[float | None],
    *,
    hysteresis_m: float = ELEVATION_HYSTERESIS_M,
) -> Assessment:
    """Total ascent, with a hysteresis band against barometric wander.

    Summing every positive delta of a barometric altimeter counts its noise:
    a metre of drift each way, once a second, is hundreds of metres of
    imaginary climbing over a flat four-hour ride. So the sum runs against a
    running **low point** instead: a rise only counts once it is
    ``hysteresis_m`` above that low point, and any new low replaces it. The
    band is a constant rather than a filter width because it is the quantity
    an athlete can check — "climbs under 2 m are not counted" is a sentence;
    "a 15-sample Savitzky-Golay filter" is not.
    """
    present = _present(elevation_fixed)
    if not present:
        return _absent("elevation")
    reference = present[0]
    gain = 0.0
    for value in present[1:]:
        if value - reference >= hysteresis_m:
            gain += value - reference
            reference = value
        elif value < reference:
            reference = value
    return Measured(
        value=gain,
        explanation=MetricExplanation(
            formula=(
                "elevation gain = Σ rises above the running low point, counted "
                f"once they exceed {hysteresis_m:g} m"
            ),
            inputs=MappingProxyType({"readings": f"{len(present)} at 1 Hz"}),
            assumptions=(
                (
                    f"rises smaller than {hysteresis_m:g} m are barometric "
                    "noise and are not counted"
                ),
            ),
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
#: naive re-use of those integers (D121). Its Z4 is `SubThreshold`
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
