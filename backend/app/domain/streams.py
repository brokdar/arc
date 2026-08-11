"""Device streams: the 1 Hz grid, its repairs, and what makes a file unusable.

The one irreversible decision in WP-4 (addenda A4.1): **every stream is
resampled to a uniform 1 Hz grid before it is stored, and row ``i`` of every
column describes the same instant.** Row index — not timestamp — is the
addressing unit for everything downstream: laps, detected efforts, chart
selections and WP-5's alignment are all ``[start_index, end_index)``.

Three rules follow from that, and they are the whole module:

**A hole is null, never zero.** A recording pause longer than
:data:`GAP_THRESHOLD_S` is a hole in the data, not a period of zero watts. The
grid continues across it and every channel is null; the pause is reported as a
``[start, end)`` range so the duration it covers can be subtracted from the
load-bearing :attr:`ResampleResult.recording_time_s` (A4.4, and A5.1 spends it).

**A repair is a derived value, so it records what it came from** (A4.2). The
raw column keeps the 1 900 W spike from the dropped magnet; a parallel
``*_fixed`` column is what analysis reads; and every substituted region becomes
an :class:`Anomaly` naming the rows, the kind of repair and the value that was
put there. An unrecorded repair is indistinguishable from a measurement, which
is the one thing this codebase's provenance rules exist to prevent.

**Systemic garbage is quarantined, not repaired** (:func:`validate`). A spike
is a repair; a channel that is 30 % nonsense is a broken file, and the athlete
is told rather than shown a cleaned-up average of it.

Everything here is pure: plain Python sequences and dataclasses in, the same
out. The parsers in `app.ingest` turn a FIT/GPX/TCX file into
:class:`ParsedActivity`; nothing in this module has ever seen a file.
"""

import datetime as dt
import statistics
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import cast

from app.domain.activity import QuarantineReason


class StreamChannel(StrEnum):
    """A per-sample measurement a recording may carry.

    Values are the parquet column names and the ``*_fixed`` columns are these
    plus that suffix, so this enum is the storage vocabulary as well as the
    domain's.
    """

    POWER = "power"
    HR = "hr"
    CADENCE = "cadence"
    SPEED = "speed"
    ELEVATION = "elevation"
    TEMP = "temp"
    LAT = "lat"
    LON = "lon"


class FillRule(StrEnum):
    """How a channel's value is carried across a sub-threshold gap.

    ``INTERPOLATE`` for the channels that describe a *position* — latitude,
    longitude, altitude — where the intermediate values genuinely lay between
    the two ends and a straight line is the best available account of them.

    ``HOLD`` for the channels that describe an *instantaneous rate* — power,
    heart rate, cadence, speed, temperature. Interpolating a rate invents a
    ramp the athlete never rode; holding the last reading is what the device
    itself is doing when it reports one value per four seconds.
    """

    HOLD = "hold"
    INTERPOLATE = "interpolate"


#: Per-channel gap rule (A4.1's "document the resampling rule per channel").
FILL_RULE: Mapping[StreamChannel, FillRule] = MappingProxyType(
    {
        StreamChannel.POWER: FillRule.HOLD,
        StreamChannel.HR: FillRule.HOLD,
        StreamChannel.CADENCE: FillRule.HOLD,
        StreamChannel.SPEED: FillRule.HOLD,
        StreamChannel.TEMP: FillRule.HOLD,
        StreamChannel.ELEVATION: FillRule.INTERPOLATE,
        StreamChannel.LAT: FillRule.INTERPOLATE,
        StreamChannel.LON: FillRule.INTERPOLATE,
    }
)

#: A gap between consecutive samples longer than this many seconds is a
#: recording stop, not a dropout: the grid stays null across it and the
#: duration is subtracted from recording time (A4.4). Thirty seconds is long
#: enough that no sampling scheme produces it by accident and short enough that
#: a traffic light does not become training time.
GAP_THRESHOLD_S = 30

#: Speed at or above which the athlete counts as moving, in m/s (1 km/h).
#: The line between moving time and standing still: every *average* in the
#: metric set is divided by the moving time this defines (D194), while training
#: load's duration term stays recording time (A5.1). Two different questions,
#: two different denominators, and this constant separates them.
MOVING_SPEED_MS = 1000 / 3600

#: Per-channel plausible range, inclusive on both ends. Power, heart rate and
#: speed are the build plan's (WP-4.1); the rest are typo guards of the same
#: kind, wide enough that no real recording touches them — a value outside one
#: of these is a sensor fault or a unit error, never a hard effort.
PLAUSIBLE_RANGE: Mapping[StreamChannel, tuple[float, float]] = MappingProxyType(
    {
        StreamChannel.POWER: (0.0, 2500.0),
        StreamChannel.HR: (25.0, 230.0),
        StreamChannel.CADENCE: (0.0, 250.0),
        StreamChannel.SPEED: (0.0, 35.0),
        StreamChannel.ELEVATION: (-500.0, 9000.0),
        StreamChannel.TEMP: (-40.0, 60.0),
        StreamChannel.LAT: (-90.0, 90.0),
        StreamChannel.LON: (-180.0, 180.0),
    }
)


# --- what a parser hands over (work order A-1) --------------------------------


@dataclass(frozen=True, slots=True)
class RawSample:
    """One instant as the device recorded it.

    Args:
        t: When, aware UTC. Naive values are rejected here rather than three
            layers later, where the failure is a silent 1-2 hour date shift.
        values: Only the channels this sample actually carried — a FIT record
            with no power field is a sample without ``POWER``, not a sample
            with zero watts.
    """

    t: dt.datetime
    values: Mapping[StreamChannel, float]

    def __post_init__(self) -> None:
        """Reject a naive timestamp."""
        if self.t.tzinfo is None:
            raise ValueError("a RawSample timestamp must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class ParsedActivity:
    """One sport within one file — a file yields a *list* of these (A4.5).

    The interchange shape between `app.ingest`'s parsers and everything else.
    Multisport files are not what we train, but the cardinality reaches into
    the dedup key and the quarantine record, and those are the two things
    hardest to change once real files exist.

    Args:
        file_sport_index: Ordinal of this sport within the file, from 0. Half
            of the dedup key (the other half is the file's sha256).
        sport: The raw sport string the file carried, if any. Kept verbatim —
            :func:`app.domain.activity.classify_discipline` interprets it, and
            the recording row stores it so a later re-reading of the rule does
            not need the original file.
        start_time: First sample's instant, aware UTC.
        local_offset: The athlete-local offset the device wrote, when it wrote
            one; `app.domain.activity.timezone_label` turns it into the
            session's stored timezone.
        samples: Ordered by time, irregularly spaced. May carry different
            channels from sample to sample.
        laps: ``(start, end)`` instants of the file's own lap markers.
        power_source_candidates: Every plausible power field found in the file
            (A4.3) — a crank meter, a smart trainer and an estimator can all be
            present, and they can differ by 15 %.
        power_source: The one that produced the ``power`` channel.
        power_source_rule: Why that one; ``"only candidate"`` when there was
            no choice to make.
        hr_source_candidates: The same, for heart rate (strap vs. wrist).
        hr_source: The heart-rate field used.
        hr_source_rule: Why.
    """

    file_sport_index: int
    sport: str | None
    start_time: dt.datetime
    local_offset: dt.timedelta | None
    samples: Sequence[RawSample]
    laps: Sequence[tuple[dt.datetime, dt.datetime]] = ()
    power_source_candidates: Sequence[str] = ()
    power_source: str | None = None
    power_source_rule: str | None = None
    hr_source_candidates: Sequence[str] = ()
    hr_source: str | None = None
    hr_source_rule: str | None = None


def channels_present(samples: Sequence[RawSample]) -> frozenset[StreamChannel]:
    """Every channel at least one sample carried.

    What the recording row stores as its channel list, and what
    `app.domain.activity.classify_discipline` is asked about.
    """
    return frozenset(channel for sample in samples for channel in sample.values)


def ordered_samples(samples: Sequence[RawSample]) -> list[RawSample]:
    """The samples sorted by time, stably.

    Stable so that two samples sharing a timestamp keep the order the file put
    them in — :func:`validate` refuses such a file, but everything downstream
    of it still has to behave deterministically.
    """
    return sorted(samples, key=lambda sample: sample.t)


# --- the 1 Hz grid (work order A-2, addenda A4.1) ------------------------------


@dataclass(frozen=True, slots=True)
class StreamFrame:
    """A uniform 1 Hz grid. Row ``i`` of every column describes ``t0 + i`` seconds.

    Args:
        t0: The grid origin — the first sample's instant, aware UTC. Row ``i``
            covers ``[t0 + i, t0 + i + 1)`` seconds.
        device_t: The original device timestamp of the sample that landed in
            each row, or ``None`` where no sample did. This is what makes the
            grid honest: a row whose ``device_t`` is null holds values the grid
            manufactured, and every consumer can see that per row.
        columns: Channel -> column. Every column has the same length as
            ``device_t``; a row with no data for a channel is ``None``.
    """

    t0: dt.datetime
    device_t: tuple[dt.datetime | None, ...]
    columns: Mapping[StreamChannel, tuple[float | None, ...]]

    def __post_init__(self) -> None:
        """Enforce the invariant every consumer is allowed to assume.

        Raises:
            ValueError: When a column's length differs from the row count.
        """
        for channel, column in self.columns.items():
            if len(column) != len(self.device_t):
                raise ValueError(
                    f"every column must have one entry per row: {channel.value} "
                    f"has {len(column)}, the frame has {len(self.device_t)} rows"
                )

    @property
    def row_count(self) -> int:
        """Number of one-second rows in the grid."""
        return len(self.device_t)

    def instant(self, index: int) -> dt.datetime:
        """The grid instant row ``index`` describes (not the device's)."""
        return self.t0 + dt.timedelta(seconds=index)


@dataclass(frozen=True, slots=True)
class ResampleResult:
    """The grid plus the four numbers only the original samples can answer (A4.4).

    Args:
        frame: The 1 Hz grid.
        elapsed_time_s: Last sample's instant minus the first's.
        recording_time_s: ``elapsed_time_s`` minus the rows the recording stops
            cover — **exactly** ``elapsed_time_s - Σ(end_index - start_index)``
            over :attr:`recording_stops`, so the athlete-facing "paused" total
            a consumer derives by subtracting the two agrees with the ranges it
            can see. **The duration term in training load** (A5.1) — not moving
            time, not elapsed.
        recording_stops: The ``[start_index, end_index)`` row ranges that were
            subtracted, in order.
        median_time_delta_s: Median spacing of the original samples — the
            one-number answer to "how irregular was this file", which is the
            first thing worth knowing when a derived value looks wrong.
        moving_time_s: Time spent at or above :data:`MOVING_SPEED_MS`, counting
            only samples whose speed is inside :data:`PLAUSIBLE_RANGE`. The
            basis every average in the metric set is taken over (D194); never
            the load's duration term.
    """

    frame: StreamFrame
    elapsed_time_s: float
    recording_time_s: float
    recording_stops: tuple[tuple[int, int], ...]
    median_time_delta_s: float
    moving_time_s: float


def _row_index(sample_t: dt.datetime, t0: dt.datetime, row_count: int) -> int:
    """The grid row a device timestamp falls in.

    Floor of the elapsed seconds, because row ``i`` covers ``[i, i + 1)``. The
    clamp catches the last sample when the elapsed time has a fractional part:
    the grid's last row is the one that sample defines.
    """
    return min(int((sample_t - t0).total_seconds()), row_count - 1)


def _stop_ranges(
    ordered: Sequence[RawSample], t0: dt.datetime, row_count: int, gap_threshold_s: int
) -> list[tuple[int, int]]:
    """The ``[start_index, end_index)`` row ranges no sample covers.

    One range per gap longer than ``gap_threshold_s``, spanning the rows
    strictly between the two samples that bracket it — the earlier sample's own
    row was recorded, and so was the later one's.

    The single definition of a recording stop: :func:`resample` reports these
    ranges and subtracts their **row counts** from recording time, and
    :func:`validate` re-derives the same number before it decides a file is too
    short. Subtracting anything else (the raw inter-sample delta, say) would
    make ``elapsed - recording`` disagree with the ranges by a second per stop.
    """
    stops: list[tuple[int, int]] = []
    for earlier, later in pairwise(ordered):
        if (later.t - earlier.t).total_seconds() <= gap_threshold_s:
            continue
        start = _row_index(earlier.t, t0, row_count) + 1
        end = _row_index(later.t, t0, row_count)
        if end > start:
            stops.append((start, end))
    return stops


def resample(
    samples: Sequence[RawSample], *, gap_threshold_s: int = GAP_THRESHOLD_S
) -> ResampleResult:
    """Put irregular device samples on the uniform 1 Hz grid (A4.1).

    One row per elapsed second from the first sample to the last, inclusive of
    both — ``floor(elapsed_time_s) + 1`` rows. Where several samples share a
    second the last one wins, so ``device_t`` keeps pointing at a real recorded
    instant rather than at an average no device ever wrote.

    **Gaps.** Consecutive samples more than ``gap_threshold_s`` apart are a
    recording stop: the rows between them stay null in every channel and the
    range is reported in :attr:`ResampleResult.recording_stops`. Nothing is
    interpolated across a stop — a coffee stop is not 600 seconds of zero
    watts, and the two must never be confused. What is subtracted from
    recording time is the **length of that row range**, not the raw gap
    between the two samples: the rows either side of the stop were recorded,
    so counting them as paused would leave ``elapsed - recording`` a second
    larger than the ranges the same result reports.

    **Sub-threshold gaps** are filled per channel by :data:`FILL_RULE`:
    ``lat``/``lon``/``elevation`` linearly, ``power``/``hr``/``cadence``/
    ``speed``/``temp`` by holding the previous reading — at most
    ``gap_threshold_s - 1`` missing rows, so the two readings a fill bridges
    are never more than ``gap_threshold_s`` seconds apart. The rule is applied
    per *channel*, so a file that reports power every second and temperature
    every minute leaves temperature null across its own longer gaps rather
    than holding a reading for a minute.

    **Moving time** counts the intervals whose earlier sample reports a speed
    at or above :data:`MOVING_SPEED_MS`. A speed outside
    :data:`PLAUSIBLE_RANGE` — the 900 m/s a GPS glitch writes — is not
    evidence of movement and is skipped rather than believed.

    Rows before a channel's first sample and after its last stay null: the
    frame spans the activity, not each channel's own coverage.

    Args:
        samples: The parser's samples. Sorted here if they are not already.
        gap_threshold_s: Seconds above which a gap is a recording stop.

    Returns:
        The grid and A4.4's numbers.

    Raises:
        ValueError: When ``samples`` is empty. A file with no samples has no
            grid origin and no duration; `validate` refuses it first
            (`QuarantineReason.NO_SAMPLES`), so reaching here means the
            pipeline skipped that step.
    """
    ordered = ordered_samples(samples)
    if not ordered:
        raise ValueError("cannot resample an activity with no samples")

    t0 = ordered[0].t
    elapsed_time_s = (ordered[-1].t - t0).total_seconds()
    row_count = int(elapsed_time_s) + 1

    device_t: list[dt.datetime | None] = [None] * row_count
    observed: dict[StreamChannel, dict[int, float]] = {}
    for sample in ordered:
        index = _row_index(sample.t, t0, row_count)
        device_t[index] = sample.t
        for channel, value in sample.values.items():
            observed.setdefault(channel, {})[index] = value

    pairs = list(pairwise(ordered))
    deltas = [(later.t - earlier.t).total_seconds() for earlier, later in pairs]
    stops = _stop_ranges(ordered, t0, row_count, gap_threshold_s)
    stopped_s = float(sum(end - start for start, end in stops))

    stopped_rows = bytearray(row_count)
    for start, end in stops:
        stopped_rows[start:end] = b"\x01" * (end - start)

    columns = {
        channel: _fill(
            points, row_count, FILL_RULE[channel], stopped_rows, gap_threshold_s
        )
        for channel, points in observed.items()
    }

    low_speed, high_speed = PLAUSIBLE_RANGE[StreamChannel.SPEED]
    moving_time_s = 0.0
    for (earlier, _later), delta in zip(pairs, deltas, strict=True):
        speed = earlier.values.get(StreamChannel.SPEED)
        if speed is None or not low_speed <= speed <= high_speed:
            continue  # a 900 m/s GPS glitch is not thirty seconds of riding
        if delta <= gap_threshold_s and speed >= MOVING_SPEED_MS:
            moving_time_s += delta

    return ResampleResult(
        frame=StreamFrame(
            t0=t0, device_t=tuple(device_t), columns=MappingProxyType(columns)
        ),
        elapsed_time_s=elapsed_time_s,
        recording_time_s=elapsed_time_s - stopped_s,
        recording_stops=tuple(stops),
        median_time_delta_s=statistics.median(deltas) if deltas else 0.0,
        moving_time_s=moving_time_s,
    )


def _fill(
    points: Mapping[int, float],
    row_count: int,
    rule: FillRule,
    stopped_rows: bytearray,
    gap_threshold_s: int,
) -> tuple[float | None, ...]:
    """Place one channel's observations on the grid and close its short gaps.

    **The line:** a hole of at most ``gap_threshold_s - 1`` **missing rows** is
    closed — equivalently, the two readings it lies between are at most
    ``gap_threshold_s`` seconds apart, which is the same distance that makes a
    gap a recording stop rather than a dropout. With the default threshold that
    is 29 missing rows, and :func:`clean` repairs to the same line.

    A gap is closed only when it also holds no recording-stop row. Both tests
    are needed: a stop of 30.5 s spans 30 rows, which the width test alone
    would let through.
    """
    column: list[float | None] = [None] * row_count
    for index, value in points.items():
        column[index] = value
    for (start, first), (end, last) in pairwise(sorted(points.items())):
        span = end - start
        if span <= 1 or span > gap_threshold_s or any(stopped_rows[start + 1 : end]):
            continue
        if rule is FillRule.INTERPOLATE:
            step = (last - first) / span
            for offset in range(1, span):
                column[start + offset] = first + step * offset
        else:
            for offset in range(1, span):
                column[start + offset] = first
    return tuple(column)


# --- cleaning and anomalies (work order A-3, addenda A4.2) ---------------------


class AnomalyKind(StrEnum):
    """What was done to a region of a channel, and therefore what it now is.

    ``SPIKE_CLIPPED`` — a short out-of-range excursion replaced by the last
    good reading. ``DROPOUT_HELD`` — a longer missing or out-of-range run
    carried forward at the last good reading. ``GAP_INTERPOLATED`` — the same
    run on a positional channel, filled with a straight line.

    ``DROPPED`` — an implausible **value** the repair rules declined to repair,
    set to null: one in a run too long to repair honestly, one in a run that
    touches a recording stop, or one outside the channel's own believable
    readings (before its first, after its last). It is a repair like the others
    precisely because a null is a claim ("there is no data here") replacing a
    value the file did contain, and A4.2's rule is that every such claim is
    recorded. A row that was **already** null is not dropped and never appears
    here: nothing was substituted for it, and a channel the device samples once
    a minute would otherwise arrive as an hour of anomalies about rows no
    device ever wrote.

    ``RESAMPLED_ONLY`` — nothing was repaired. One row per untouched channel,
    spanning the whole frame, so "this channel needed no cleaning" can be told
    apart from "the cleaner never ran". The grid's own filling of sub-threshold
    gaps is *not* an anomaly: it is the storage contract (A4.1), it is visible
    per row through :attr:`StreamFrame.device_t`, and one row per four-second
    sample would put tens of thousands of anomalies on an ordinary ride.
    """

    GAP_INTERPOLATED = "gap_interpolated"
    SPIKE_CLIPPED = "spike_clipped"
    DROPOUT_HELD = "dropout_held"
    DROPPED = "dropped"
    RESAMPLED_ONLY = "resampled_only"


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One repaired region of one channel.

    Args:
        channel: Which channel was repaired.
        start_index: First row repaired.
        end_index: One past the last — ``[start, end)``, the same convention
            as recording stops and everything else addressed by row.
        kind: What was done.
        substituted_value: The value that was put there, when one value was.
            ``None`` for interpolation and for :attr:`AnomalyKind.DROPPED`,
            where there is no single number to name.
    """

    channel: StreamChannel
    start_index: int
    end_index: int
    kind: AnomalyKind
    substituted_value: float | None = None


@dataclass(frozen=True, slots=True)
class CleanResult:
    """The ``*_fixed`` columns and the record of how they differ from the raw.

    Args:
        fixed: Channel -> cleaned column, one entry for **every** channel in
            the frame even when nothing was repaired. Analysis reads these and
            only these, so "which columns exist" must not depend on how dirty
            the file was.
        anomalies: Every repair, in channel then row order.
    """

    fixed: Mapping[StreamChannel, tuple[float | None, ...]]
    anomalies: tuple[Anomaly, ...] = ()


#: Longest out-of-range run treated as a spike — a dropped magnet, a radio
#: glitch — and clipped to the last good reading.
SPIKE_MAX_S = 3

#: Furthest apart the two believable readings flanking a bad run may be for it
#: to be repaired at all — so at most ``REPAIR_MAX_S - 1`` **missing rows** are
#: repaired (29 with the default), which is the line :func:`_fill` bridges a
#: hole in the grid on. Beyond it the data is missing, not noisy: nothing is
#: invented, and any implausible number inside the run is nulled and recorded.
#: Equal to :data:`GAP_THRESHOLD_S` by construction: the distance that makes a
#: gap a recording stop is the distance that makes a run unrepairable.
REPAIR_MAX_S = GAP_THRESHOLD_S


def _runs(flags: Sequence[bool], start: int, end: int) -> Iterator[tuple[int, int]]:
    """Yield the maximal ``[start, end)`` ranges within which ``flags`` is true."""
    run_start: int | None = None
    for index in range(start, end):
        if flags[index]:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            yield run_start, index
            run_start = None
    if run_start is not None:
        yield run_start, end


def clean(
    frame: StreamFrame,
    *,
    recording_stops: Sequence[tuple[int, int]] = (),
    spike_max_s: int = SPIKE_MAX_S,
    repair_max_s: int = REPAIR_MAX_S,
) -> CleanResult:
    """Produce the ``*_fixed`` columns and the anomaly record behind them (A4.2).

    **The invariant:** a ``_fixed`` column never holds a value outside
    :data:`PLAUSIBLE_RANGE`. Every entry is either a believable number or
    ``None``. Analysis reads these columns and nothing else, so a single
    implausible value surviving here is the silent corruption the raw/fixed
    split exists to prevent — and it would be worse than the raw column, which
    at least does not claim to have been checked.

    A row is **bad** when its value is missing or outside the range. Bad rows
    are grouped into maximal runs, and each run is treated by its length in
    seconds:

    * ``<= spike_max_s`` — clipped to the last good reading
      (:attr:`AnomalyKind.SPIKE_CLIPPED`). A 1 900 W spike from a dropped
      magnet is three seconds of nonsense between two believable readings.
    * at most ``repair_max_s - 1`` **missing rows** — that is, the believable
      readings either side of it are at most ``repair_max_s`` seconds apart —
      held at the last good reading (:attr:`AnomalyKind.DROPOUT_HELD`), or
      filled linearly for the positional channels
      (:attr:`AnomalyKind.GAP_INTERPOLATED`), per :data:`FILL_RULE`. With the
      default that is 29 rows, the same line :func:`resample` bridges a hole in
      the grid on, so the two passes never disagree about one row.
    * longer — not repaired. Never invented.

    Four regions are therefore **not** repaired. Rows *inside a recording stop*
    are already null and stay null; a run that so much as touches one is not
    repaired at all, because a reading taken as the device stopped or resumed
    is no more trustworthy than the gap itself. Rows *before a channel's first
    believable reading or after its last* are not repaired either — holding
    backwards would fabricate a warm-up, and there is no second endpoint to
    interpolate towards. And a run longer than the line above is left alone.

    In each of those cases an implausible **value** is replaced by ``None`` and
    recorded as :attr:`AnomalyKind.DROPPED`; a row that was already ``None``
    stays ``None`` and is **not** an anomaly, because nothing was substituted
    for it. An anomaly is always a claim that recorded data was altered, so a
    declined run made only of nulls — a channel sampled once a minute, a strap
    that dropped out for two — produces none at all, and a channel is certified
    :attr:`AnomalyKind.RESAMPLED_ONLY` when and only when its fixed column is
    byte-identical to its raw one. "Declined to repair" still never means
    "passed a bad number through".

    The raw columns are returned untouched — the spike stays in ``power``, and
    ``power_fixed`` is what a metric reads.

    Args:
        frame: The resampled grid.
        recording_stops: The stops :func:`resample` reported for it.
        spike_max_s: Longest run treated as a spike.
        repair_max_s: Longest run repaired at all.

    Returns:
        A cleaned column for every channel in the frame, plus the anomalies.
    """
    stopped_rows = bytearray(frame.row_count)
    for start, end in recording_stops:
        stopped_rows[start:end] = b"\x01" * (end - start)

    fixed: dict[StreamChannel, tuple[float | None, ...]] = {}
    anomalies: list[Anomaly] = []
    for channel in sorted(frame.columns, key=lambda member: member.value):
        column, channel_anomalies = _clean_channel(
            channel,
            frame.columns[channel],
            stopped_rows=stopped_rows,
            spike_max_s=spike_max_s,
            repair_max_s=repair_max_s,
        )
        fixed[channel] = column
        anomalies.extend(channel_anomalies)
    return CleanResult(fixed=MappingProxyType(fixed), anomalies=tuple(anomalies))


def _clean_channel(
    channel: StreamChannel,
    raw: Sequence[float | None],
    *,
    stopped_rows: bytearray,
    spike_max_s: int,
    repair_max_s: int,
) -> tuple[tuple[float | None, ...], list[Anomaly]]:
    """Clean one channel's column; see :func:`clean` for the rules."""
    low, high = PLAUSIBLE_RANGE[channel]

    def believable(value: float | None) -> bool:
        return value is not None and low <= value <= high

    good = [believable(value) for value in raw]
    good_indices = [index for index, ok in enumerate(good) if ok]
    column = list(raw)
    anomalies: list[Anomaly] = []

    # Pass one: repair what can be repaired — the runs that lie between two
    # believable readings and touch no recording stop. A channel with no
    # believable reading at all has nothing to repair *from* and skips this
    # entirely; pass two is what keeps its column honest.
    if good_indices:
        first, last = good_indices[0], good_indices[-1]
        bad = [not ok for ok in good]
        for start, end in _runs(bad, first + 1, last):
            if any(stopped_rows[start:end]):
                continue
            # The run is maximal inside ``(first, last)``, so both neighbours
            # are good rows and therefore real numbers; `cast` states that
            # rather than adding a branch nothing can reach.
            before = cast(float, raw[start - 1])
            after = cast(float, raw[end])
            span = end - start
            if span <= spike_max_s:
                kind, substituted = AnomalyKind.SPIKE_CLIPPED, before
            elif span + 1 > repair_max_s:
                # The flanking readings are more than ``repair_max_s`` seconds
                # apart, which is where `_fill` stops bridging too. Leave the
                # run alone: pass two nulls whatever numbers are in it and
                # records exactly those rows, so a run made only of nulls
                # produces no anomaly at all.
                continue
            elif FILL_RULE[channel] is FillRule.INTERPOLATE:
                kind, substituted = AnomalyKind.GAP_INTERPOLATED, None
            else:
                kind, substituted = AnomalyKind.DROPOUT_HELD, before

            if kind is AnomalyKind.GAP_INTERPOLATED:
                step = (after - before) / (span + 1)
                for offset in range(span):
                    column[start + offset] = before + step * (offset + 1)
            else:
                for index in range(start, end):
                    column[index] = substituted
            anomalies.append(
                Anomaly(
                    channel=channel,
                    start_index=start,
                    end_index=end,
                    kind=kind,
                    substituted_value=substituted,
                )
            )

    # Pass two: whatever pass one declined to repair — an over-long run, a run
    # touching a recording stop, a region outside the channel's own believable
    # readings — must not survive as a number. Declining is a judgement about
    # the *repair* (there is nothing to interpolate towards, or the
    # neighbouring reading is as suspect as the gap), never a judgement that
    # the value is fine. Nulling it is a substitution like any other, so it is
    # recorded; a row that was already null is not, because nothing was
    # substituted for it, and the anomaly rows therefore cover exactly the rows
    # this pass changed.
    surviving = [not believable(value) and value is not None for value in column]
    for start, end in _runs(surviving, 0, len(column)):
        for index in range(start, end):
            column[index] = None
        anomalies.append(
            Anomaly(
                channel=channel,
                start_index=start,
                end_index=end,
                kind=AnomalyKind.DROPPED,
            )
        )
    anomalies.sort(key=lambda anomaly: (anomaly.start_index, anomaly.end_index))

    if not anomalies:
        anomalies.append(
            Anomaly(
                channel=channel,
                start_index=0,
                end_index=len(raw),
                kind=AnomalyKind.RESAMPLED_ONLY,
            )
        )
    return tuple(column), anomalies


# --- validation / quarantine triggers (work order A-4) ------------------------


@dataclass(frozen=True, slots=True)
class QuarantineVerdict:
    """Why an activity may not be ingested.

    Args:
        reason: Machine-readable; the inbox UI branches on it and the
            confirm/reject actions differ by it.
        detail: The same fact in the athlete's terms, with the numbers that
            triggered it.
    """

    reason: QuarantineReason
    detail: str


#: Shortest activity worth a session row. Below this it is a device switched on
#: by accident or a file truncated on transfer, and either way there is nothing
#: to score (build plan WP-4.1).
MIN_DURATION_S = 120

#: Above this fraction of a channel's samples being outside
#: :data:`PLAUSIBLE_RANGE`, the channel is not noisy but wrong — a unit error, a
#: mis-decoded field, a broken sensor. Repairing it would mean substituting for
#: most of it, so the file is quarantined and the athlete is told instead.
MAX_IMPLAUSIBLE_FRACTION = 0.10


def validate(
    activity: ParsedActivity,
    *,
    min_duration_s: int = MIN_DURATION_S,
    max_implausible_fraction: float = MAX_IMPLAUSIBLE_FRACTION,
    gap_threshold_s: int = GAP_THRESHOLD_S,
) -> QuarantineVerdict | None:
    """Decide whether an activity can be ingested at all.

    ``None`` means yes. Anything else is a file the pipeline moves to
    ``data/quarantine/`` with the verdict attached, rather than a file it
    repairs — the distinction :func:`clean` draws one level down, at the scale
    of a whole recording.

    The checks, in the order they are reported:

    1. **No samples.** A file whose parser found nothing.
    2. **Non-monotonic timestamps.** Samples are stable-sorted first, so what
       survives sorting is readings claiming the same instant. A few of those
       are ordinary — a head unit writes a record either side of a pause, and
       :func:`resample` collapses them by the same last-wins rule it applies to
       any second carrying several samples — so they are tolerated up to
       ``max_implausible_fraction``. Above it the file's clock is broken rather
       than chatty, and last-wins would be silently discarding a large part of
       the recording.
    3. **Too short.** Under ``min_duration_s`` of elapsed time, *or* under
       ``min_duration_s`` of recording time — the two are different files. Two
       samples 200 s apart clear the elapsed gate and would become a session
       whose recording time is a second, which is the number WP-5 divides by;
       the recording-time analogue is computed from :func:`_stop_ranges`, the
       same function :func:`resample` subtracts, so a file this admits cannot
       resample to less than ``min_duration_s``.
    4. **An implausible channel.** More than ``max_implausible_fraction`` of
       the samples carrying a channel are outside :data:`PLAUSIBLE_RANGE`.
       Channels are examined in :class:`StreamChannel` order, so the verdict is
       deterministic for a file with more than one bad channel.

    Checks 2 and 4 share a threshold on purpose: both draw the same line
    between an isolated defect, which the grid or the cleaner absorbs and
    records, and a systemic one, which no repair can honestly cover.

    Args:
        activity: One parsed sport within one file.
        min_duration_s: Shortest duration accepted, applied to elapsed time and
            to recording time alike.
        max_implausible_fraction: Share of a channel's samples that may be out
            of range — and share of samples that may repeat a timestamp —
            before the file is refused.
        gap_threshold_s: Seconds above which a gap is a recording stop; must be
            the value :func:`resample` will be called with, or the two disagree
            about how much of the file was recorded.

    Returns:
        The verdict, or ``None`` when the activity is fit to ingest.
    """
    samples = activity.samples
    if not samples:
        return QuarantineVerdict(
            reason=QuarantineReason.NO_SAMPLES,
            detail="the file parsed, but this activity carried no samples",
        )

    ordered = ordered_samples(samples)
    duplicates = sum(1 for earlier, later in pairwise(ordered) if earlier.t == later.t)
    if duplicates / len(ordered) > max_implausible_fraction:
        return QuarantineVerdict(
            reason=QuarantineReason.NON_MONOTONIC_TIMESTAMPS,
            detail=(
                f"{duplicates / len(ordered):.0%} of the {len(ordered)} samples "
                "repeat a timestamp already used; that is a broken clock, not a "
                "pause record, and resampling would discard most of the file"
            ),
        )

    elapsed_s = (ordered[-1].t - ordered[0].t).total_seconds()
    if elapsed_s < min_duration_s:
        return QuarantineVerdict(
            reason=QuarantineReason.TOO_SHORT,
            detail=(
                f"the activity lasts {elapsed_s:.0f} s; at least {min_duration_s} s "
                "is needed for a session"
            ),
        )

    stops = _stop_ranges(ordered, ordered[0].t, int(elapsed_s) + 1, gap_threshold_s)
    recording_s = elapsed_s - sum(end - start for start, end in stops)
    if recording_s < min_duration_s:
        return QuarantineVerdict(
            reason=QuarantineReason.TOO_SHORT,
            detail=(
                f"the activity spans {elapsed_s:.0f} s but only {recording_s:.0f} s "
                f"of it was recorded — the rest is {len(stops)} recording stop(s); "
                f"at least {min_duration_s} s of recording time is needed for a "
                "session"
            ),
        )

    for channel in StreamChannel:
        present = [
            sample.values[channel] for sample in ordered if channel in sample.values
        ]
        if not present:
            continue
        low, high = PLAUSIBLE_RANGE[channel]
        implausible = sum(1 for value in present if not low <= value <= high)
        fraction = implausible / len(present)
        if fraction > max_implausible_fraction:
            return QuarantineVerdict(
                reason=QuarantineReason.IMPLAUSIBLE_CHANNEL,
                detail=(
                    f"{fraction:.0%} of the {len(present)} {channel.value} samples "
                    f"are outside {low:g}-{high:g}; that is a broken channel, not "
                    "a spike"
                ),
            )
    return None
