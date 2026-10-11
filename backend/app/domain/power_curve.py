"""Mean-maximal power: a ride's best average watts for each duration.

The one curve every cycling tool shows first, and the base of everything later
built on it (records, the power page, the critical-power fit, effort search).
Three functions, composed by :func:`power_curve`:

* :func:`duration_grid` — which durations are answered;
* :func:`fill_gaps` — what a second with no reading is taken to be;
* :func:`mmp` — the exact best window for each grid duration.

A pure function of the stream: it reads the cleaned 1 Hz power column and the
cleaner's repaired regions, and nothing else — no anchor, no athlete. That is
why its stored row is replaced on recompute rather than versioned
(`app.persistence.power_curve.SessionMmpRow`).

Ranges follow the system convention: a window of ``d`` seconds starting at
``s`` is ``[s, s + d)``, never ``s + d + 1``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import accumulate
from operator import sub

#: The version of :func:`duration_grid` a curve was computed on.
#:
#: The grid is **GoldenCheetah's**: every second below 2 min, every 2 s below
#: 10 min, every 5 s below 20 min, every 20 s below 60 min, every 2 min below
#: 2 h, and every 5 min beyond. Only grid points are computed and stored —
#: nothing is interpolated between them — and every stored row carries this
#: number, so a reader can tell which durations a row is able to answer.
#:
#: Chosen over a dense every-second curve (a 5 h ride would store 18 000
#: points to draw a line nobody reads past the knee at that resolution) and
#: over a per-athlete grid (two athletes' — or two seasons' — curves would
#: then not share the durations a comparison needs). Matching GoldenCheetah
#: also means the durations line up with the incumbent tools arc is checked
#: against. Changing a step changes what a stored row means: bump this, and
#: `test_domain_power_curve.py::test_grid_is_pinned` fails until you do.
GRID_VERSION = 1

#: ``(below, step)``: below ``below`` seconds the grid steps by ``step``.
_GRID_STEPS: tuple[tuple[int, int], ...] = (
    (120, 1),
    (600, 2),
    (1_200, 5),
    (3_600, 20),
    (7_200, 120),
)

#: The step beyond the last bound in :data:`_GRID_STEPS`.
_GRID_STEP_BEYOND = 300

#: The longest run of missing samples that is linearly interpolated, seconds.
#:
#: A head unit in "smart recording" writes a sample every few seconds; on the
#: 1 Hz grid that is a run of ``None`` between two readings, and reading those
#: seconds as zero watts would clip the short end of every such ride's curve.
#: So a short run is bridged — eight seconds covers a 4 s-sampled file with
#: room for a dropped packet — and anything longer is a real absence (a
#: dropout, a recording stop) and is zero-filled.
MMP_INTERP_MAX_GAP_S = 8

#: Fixed-point scale the search sums in: centiwatts. Exact integers rather
#: than floats, so two windows over the same samples compare equal however
#: they were added up, and "earliest start wins ties" is decidable.
_SCALE = 100


@dataclass(frozen=True, slots=True)
class MmpPoint:
    """The best window of one duration.

    Args:
        duration_s: The window length, a :func:`duration_grid` point.
        watts: The window's mean power, rounded half-up to 0.1 W — the
            precision it is stored at, so the domain and the stored row agree
            exactly.
        start_index: First row of the window on the session's 1 Hz grid; the
            window is ``[start_index, start_index + duration_s)``.
        clean: Whether every second in that window was measured: false when it
            contains a zero-filled second or a region the cleaner repaired.
    """

    duration_s: int
    watts: float
    start_index: int
    clean: bool


@dataclass(frozen=True, slots=True)
class PowerCurve:
    """One ride's mean-maximal power at every grid duration it is long enough for."""

    grid_version: int
    points: tuple[MmpPoint, ...]


@dataclass(frozen=True, slots=True)
class FilledPower:
    """A power column with every gap decided.

    Args:
        watts: One value per row; no ``None`` left.
        zero_filled: Per row, whether the value is a zero arc wrote in for a
            run too long to bridge — the seconds a best must not silently
            stand on.
    """

    watts: tuple[float, ...]
    zero_filled: tuple[bool, ...]


def _step(duration_s: int) -> int:
    """The grid step at ``duration_s``."""
    for below, step in _GRID_STEPS:
        if duration_s < below:
            return step
    return _GRID_STEP_BEYOND


def duration_grid(length_s: int) -> tuple[int, ...]:
    """The grid durations a ride of ``length_s`` rows can answer.

    Every point up to and including ``length_s``; nothing above it — a
    duration longer than the ride is absent, never padded. See
    :data:`GRID_VERSION` for the steps.
    """
    grid: list[int] = []
    duration = 1
    while duration <= length_s:
        grid.append(duration)
        duration += _step(duration)
    return tuple(grid)


def fill_gaps(values: Sequence[float | None]) -> FilledPower:
    """Decide every missing sample: bridge a short run, zero a long one.

    A run of ``None`` no longer than :data:`MMP_INTERP_MAX_GAP_S` with a
    reading on **both** sides is filled on the straight line between those two
    readings: for a run of ``n`` between ``a`` and ``b``, row ``k`` (1…n) is
    ``a + (b − a)·k/(n + 1)``. Every other run — longer than that, or touching
    either end of the column, where there is no second reading to draw a line
    to — is filled with zero and flagged in
    :attr:`FilledPower.zero_filled`. Two runs separated by a single reading are
    two runs, each judged on its own length.

    The rule is on length alone because the stored stream cannot tell a gap
    from a present-but-empty timestamp: both are ``None`` on the 1 Hz grid.
    Treating every ``None`` as zero was the alternative, and it clips the
    curve of every ride a head unit sampled every few seconds. A recording
    stop is always far longer than the bridge, so it is zero-filled whatever
    its length.
    """
    watts: list[float] = []
    zero_filled: list[bool] = []
    length = len(values)
    index = 0
    while index < length:
        value = values[index]
        if value is not None:
            watts.append(float(value))
            zero_filled.append(False)
            index += 1
            continue
        end = index
        while end < length and values[end] is None:
            end += 1
        run = end - index
        before = values[index - 1] if index > 0 else None
        after = values[end] if end < length else None
        if before is not None and after is not None and run <= MMP_INTERP_MAX_GAP_S:
            watts.extend(
                before + (after - before) * k / (run + 1) for k in range(1, run + 1)
            )
            zero_filled.extend([False] * run)
        else:
            watts.extend([0.0] * run)
            zero_filled.extend([True] * run)
        index = end
    return FilledPower(watts=tuple(watts), zero_filled=tuple(zero_filled))


def _rounded_tenths(total: int, duration_s: int) -> float:
    """``total`` centiwatt-seconds over ``duration_s``, in watts, half-up to 0.1."""
    scale = _SCALE // 10
    tenths = (2 * total + scale * duration_s) // (2 * scale * duration_s)
    return tenths / 10


def mmp(watts: Sequence[float], dirty: Sequence[bool] = ()) -> tuple[MmpPoint, ...]:
    """The best mean power for every grid duration of a gap-free column.

    Exact, not approximate: the column is summed once into integer prefix
    sums ``cs`` (in centiwatts) and the window ``[s, s + d)`` totals
    ``cs[s + d] − cs[s]``, so every window of every grid duration is compared
    exactly and the **earliest start wins a tie** — two equal efforts report
    the first one, every time. ``O(n)`` per duration, in C-level builtins.

    Chosen over GoldenCheetah's window skipping, whose pruning bound is only
    valid for non-negative power and which a cleaned column does not promise,
    and over any FFT-based search, whose float sums make "equal" undecidable.
    Pinned by `test_domain_power_curve.py::test_ties_go_to_the_earliest_start`.

    Args:
        watts: One reading per second, no gaps (see :func:`fill_gaps`).
        dirty: Per row, whether that second was invented or repaired; a
            window containing one is reported with ``clean=False``. Empty
            means every second is clean.
    """
    scaled = [round(value * _SCALE) for value in watts]
    sums = list(accumulate(scaled, initial=0))
    flagged = (
        list(accumulate((int(flag) for flag in dirty), initial=0)) if dirty else []
    )
    points: list[MmpPoint] = []
    for duration in duration_grid(len(scaled)):
        totals = list(map(sub, sums[duration:], sums[:-duration], strict=True))
        best = max(totals)
        start = totals.index(best)
        points.append(
            MmpPoint(
                duration_s=duration,
                watts=_rounded_tenths(best, duration),
                start_index=start,
                clean=not flagged or flagged[start + duration] == flagged[start],
            )
        )
    return tuple(points)


def power_curve(
    column: Sequence[float | None] | None,
    *,
    anomalies: Iterable[tuple[int, int]] = (),
) -> PowerCurve | None:
    """One ride's power curve, or ``None`` when it recorded no power at all.

    The curve spans the meter's **first to last reading**: leading and
    trailing runs of ``None`` are a meter that paired late or died early, not
    zero watts, so they are trimmed rather than zero-filled — a ride with
    power for its first four seconds answers 1–4 s and nothing longer. What
    lies between is filled by :func:`fill_gaps`, and every ``start_index`` is
    on the session's own grid.

    Args:
        column: The cleaned 1 Hz power column, ``None`` where nothing was
            recorded; ``None`` for a session with no power channel.
        anomalies: ``[start, end)`` row ranges of the power channel the
            cleaner repaired. A best standing on one is not clean.
    """
    if not column:
        return None
    present = [index for index, value in enumerate(column) if value is not None]
    if not present:
        return None
    first, last = present[0], present[-1] + 1
    filled = fill_gaps(column[first:last])
    dirty = list(filled.zero_filled)
    for start, end in anomalies:
        for index in range(max(start, first), min(end, last)):
            dirty[index - first] = True
    return PowerCurve(
        grid_version=GRID_VERSION,
        points=tuple(
            MmpPoint(
                duration_s=point.duration_s,
                watts=point.watts,
                start_index=point.start_index + first,
                clean=point.clean,
            )
            for point in mmp(filled.watts, dirty)
        ),
    )
