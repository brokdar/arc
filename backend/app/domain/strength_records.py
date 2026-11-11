"""Personal bests and the estimated max of one exercise, from what was logged.

Two different questions, deliberately kept apart:

* **A personal best is something lifted.** A set is a best when no earlier set
  of the exercise *dominates* it — none was at least as heavy **and** at least
  as many reps. That one rule covers both kinds an athlete recognises:
  102.5 kg × 7 beats 100 kg × 7 (heavier for the reps), 100 kg × 8 beats
  100 kg × 7 (more reps at the weight), and 100 kg × 6 does not. The
  "heaviest for 1, 3, 5, 8 and 10 reps" table is read off the same frontier
  (the heaviest best with at least that many reps), so the table and the list
  can never disagree.
* **The estimated max is a rough guide**, ``kg × (1 + (reps + reps left) / 30)``
  (Epley's formula with the reported reps in reserve added to the reps done).
  It only exists for a set whose effort was *reported as a count*: ``rir``
  or a set RPE, never "4+" (a floor, not a count), never a Simple-mode
  answer, never the planned target. Above ten reps-to-failure the linear
  formula is no longer trusted (:data:`app.domain.rules.E1RM_MAX_REPS`).
  The line is the median of the last three sessions' best estimates
  (:data:`app.domain.rules.E1RM_LINE_WINDOW`), so one great or awful day does
  not move it; with fewer than three it reports ``not_enough_data`` rather
  than a number built on one session.

**An estimate change is never a personal best.** A day when 100 kg × 5 felt
easier raises the estimate and lifts nothing new; calling that a best would
teach the athlete that the guide is the record. :func:`new_bests` reads only
the frontier.

What every record leaves out:

* **warm-ups and timed sets** — a warm-up is not an attempt, and a hold has
  no reps to compare;
* **bodyweight sets** (no ``load_kg``) — "heaviest" needs a weight, and
  inventing one from body mass would put a made-up number beside measured
  ones (the reason `strength_volume` gives too);
* **don't-learn sessions, from the estimate only.** "Don't learn from today"
  keeps an off day out of what arc *learns* — estimates and progression. A
  best lifted that day still happened, so it still counts as a best.

**One-side lifts record the weaker side.** Left and right sets of one session
pair up in the order they were logged, and the pair counts as the set both
sides achieved: the lighter weight and the fewer reps, with the effort rating
of the side that did fewer (L 10 and R 8 is a set of 8). A lone side with no
partner counts as itself — nothing weaker was logged.

**A first session announces nothing.** Every set of an exercise's first
session is trivially undominated; the finish summary saying "New best" for a
lift done once would make the word mean "logged". :func:`new_bests` needs at
least one earlier set to beat.

Derived on every read rather than stored: there is no table to backfill when a
set is edited, deleted or imported, and the API test pins the cost at 20,000
sets.
"""

import datetime as dt
import statistics
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from app.domain.activity import LoggedSetKind, SetSide
from app.domain.metrics import PerformedSet
from app.domain.rules import E1RM_LINE_WINDOW, E1RM_MAX_REPS

#: The rep counts the "heaviest for N reps" table reports — the counts
#: athletes and programmes name (a single, a triple, fives, eights, tens).
REP_MAX_TARGETS = (1, 3, 5, 8, 10)
#: Epley's divisor: each rep to failure is worth one thirtieth of the load.
EPLEY_DIVISOR = 30
#: The RPE scale's top: RPE 10 is zero reps left (Zourdos et al. 2016).
RPE_AT_FAILURE = 10
#: Estimates are shown to 0.1 kg; finer would claim a precision a rough
#: guide does not have.
ESTIMATE_DECIMALS = 1

type EstimateStatus = Literal["ok", "not_enough_data"]


@dataclass(frozen=True, slots=True)
class Exposure:
    """One session's sets of one exercise, in logged order."""

    session_id: uuid.UUID
    date: dt.date
    sets: Sequence[PerformedSet]
    dont_learn: bool = False
    #: Fewer working sets were logged than the line now prescribes — the
    #: session lost sets. Records ignore it; `app.domain.progression` never
    #: raises a weight from such a session, because effort after fewer sets
    #: reads easier than it was.
    trimmed: bool = False


@dataclass(frozen=True, slots=True)
class Best:
    """One undominated set: its weight, reps, and when it was first lifted."""

    load_kg: float
    reps: int
    date: dt.date
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RepMax:
    """The heaviest weight lifted for at least ``target_reps`` reps."""

    target_reps: int
    load_kg: float
    #: The reps that set actually did (≥ ``target_reps``).
    reps: int
    date: dt.date
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EstimatePoint:
    """One session's best estimate, and the line's value after it."""

    session_id: uuid.UUID
    date: dt.date
    estimate_kg: float
    #: The median of the last :data:`E1RM_LINE_WINDOW` estimates up to here,
    #: or ``None`` before there are that many.
    median_kg: float | None


@dataclass(frozen=True, slots=True)
class EstimateLine:
    """The estimated-max line over an exercise's history."""

    status: EstimateStatus
    #: The line's latest value; ``None`` while there is not enough data.
    current_kg: float | None
    points: tuple[EstimatePoint, ...]


@dataclass(frozen=True, slots=True)
class ExerciseRecords:
    """Everything the history view and the coach read for one exercise."""

    bests: tuple[Best, ...]
    rep_maxes: tuple[RepMax, ...]
    estimate: EstimateLine
    #: Sessions in which the exercise was logged at all.
    exposures: int


# --- the estimate --------------------------------------------------------------


def reps_left(entry: PerformedSet) -> float | None:
    """Reps in reserve as a count, or ``None`` when none was reported.

    "4+" (``rir_capped``) is a floor, so it is not a count either. A set RPE
    converts as ``10 - RPE``, which is what the logging rules made it agree
    with when both are given.
    """
    if entry.rir_capped:
        return None
    if entry.rir is not None:
        return float(entry.rir)
    if entry.rpe is not None:
        return RPE_AT_FAILURE - entry.rpe
    return None


def estimated_max(entry: PerformedSet) -> float | None:
    """The set's estimated one-rep max in kg, unrounded, or ``None``.

    ``None`` for a warm-up, a timed or bodyweight set, an unrated or "4+"
    set, and a set whose reps plus reps left exceed
    :data:`app.domain.rules.E1RM_MAX_REPS`.
    """
    if entry.kind is LoggedSetKind.WARMUP or entry.reps is None:
        return None
    if entry.load_kg is None or entry.load_kg <= 0:
        return None
    left = reps_left(entry)
    if left is None:
        return None
    to_failure = entry.reps + left
    if to_failure > E1RM_MAX_REPS.value:
        return None
    return entry.load_kg * (1 + to_failure / EPLEY_DIVISOR)


def _rounded(kg: float) -> float:
    return round(kg, ESTIMATE_DECIMALS)


def estimate_line(exposures: Iterable[Exposure]) -> EstimateLine:
    """The median line over each session's best estimate, oldest first."""
    return _line(_prepared(exposures))


type _Prepared = list[tuple[Exposure, list[PerformedSet]]]


def _prepared(exposures: Iterable[Exposure]) -> _Prepared:
    """Each exposure with its :func:`record_sets`, computed once for all readers."""
    return [(exposure, record_sets(exposure.sets)) for exposure in exposures]


def _line(prepared: _Prepared) -> EstimateLine:
    points: list[EstimatePoint] = []
    window = E1RM_LINE_WINDOW.value
    estimates: list[float] = []
    for exposure, sets in prepared:
        if exposure.dont_learn:
            continue
        candidates = [value for value in map(estimated_max, sets) if value is not None]
        if not candidates:
            continue
        estimates.append(_rounded(max(candidates)))
        median = (
            _rounded(statistics.median(estimates[-window:]))
            if len(estimates) >= window
            else None
        )
        points.append(
            EstimatePoint(
                session_id=exposure.session_id,
                date=exposure.date,
                estimate_kg=estimates[-1],
                median_kg=median,
            )
        )
    if len(estimates) < window:
        return EstimateLine(
            status="not_enough_data", current_kg=None, points=tuple(points)
        )
    return EstimateLine(
        status="ok", current_kg=points[-1].median_kg, points=tuple(points)
    )


# --- the sets a record reads ------------------------------------------------------


def _pair(left: PerformedSet, right: PerformedSet) -> PerformedSet:
    """The set both sides achieved: lighter weight, fewer reps (module doc)."""
    fewer = left if (left.reps or 0) <= (right.reps or 0) else right
    loads = [s.load_kg for s in (left, right)]
    load = None if None in loads else min(x for x in loads if x is not None)
    return replace(fewer, load_kg=load, side=SetSide.BOTH)


def record_sets(sets: Iterable[PerformedSet]) -> list[PerformedSet]:
    """The rep sets a record may read, with one-side sets paired.

    Warm-ups and timed sets are dropped; left and right sets pair in logged
    order into the weaker side's set; a lone side stays as it is.
    """
    out: list[PerformedSet] = []
    lefts: list[PerformedSet] = []
    rights: list[PerformedSet] = []
    for entry in sets:
        if entry.kind is LoggedSetKind.WARMUP or entry.reps is None:
            continue
        if entry.side is SetSide.LEFT:
            lefts.append(entry)
        elif entry.side is SetSide.RIGHT:
            rights.append(entry)
        else:
            out.append(entry)
    pairs = min(len(lefts), len(rights))
    out.extend(_pair(lefts[i], rights[i]) for i in range(pairs))
    out.extend(lefts[pairs:])
    out.extend(rights[pairs:])
    return out


def _grams(kg: float) -> int:
    """Compare loads in whole grams, so 102.5 and 102.50000001 are one weight."""
    return round(kg * 1000)


def _frontier(prepared: _Prepared) -> tuple[Best, ...]:
    """The undominated sets, heaviest first; a tie keeps its earliest lift.

    One pass keeps only the heaviest set seen for each rep count (strictly
    heavier replaces, so of two identical sets the earlier one stays), then
    the sweep keeps each one that does more reps than every heavier one.
    Every frontier set is the heaviest for its reps, so nothing is lost by
    discarding the rest early — and a :class:`Best` is built only for those
    few, not for each of 20,000 sets of a long history
    (`test_history_over_20000_sets_answers_in_under_200ms`).
    """
    heaviest: dict[int, tuple[int, float, Exposure]] = {}
    for exposure, sets in prepared:
        for entry in sets:
            if entry.load_kg is None or entry.reps is None:
                continue
            grams = _grams(entry.load_kg)
            held = heaviest.get(entry.reps)
            if held is None or grams > held[0]:
                heaviest[entry.reps] = (grams, entry.load_kg, exposure)
    out: list[Best] = []
    most_reps = 0
    for reps, (_, load_kg, exposure) in sorted(
        heaviest.items(), key=lambda item: (-item[1][0], -item[0])
    ):
        if reps > most_reps:
            out.append(
                Best(
                    load_kg=load_kg,
                    reps=reps,
                    date=exposure.date,
                    session_id=exposure.session_id,
                )
            )
            most_reps = reps
    return tuple(out)


def _dominated(best: Best, by: Sequence[Best]) -> bool:
    return any(
        _grams(other.load_kg) >= _grams(best.load_kg) and other.reps >= best.reps
        for other in by
    )


# --- the records ---------------------------------------------------------------------


def exercise_records(exposures: Sequence[Exposure]) -> ExerciseRecords:
    """Bests, the rep-max table and the estimate line, oldest exposure first."""
    prepared = _prepared(exposures)
    bests = _frontier(prepared)
    rep_maxes: list[RepMax] = []
    for target in REP_MAX_TARGETS:
        heaviest = next((b for b in bests if b.reps >= target), None)
        if heaviest is not None:
            rep_maxes.append(
                RepMax(
                    target_reps=target,
                    load_kg=heaviest.load_kg,
                    reps=heaviest.reps,
                    date=heaviest.date,
                    session_id=heaviest.session_id,
                )
            )
    return ExerciseRecords(
        bests=bests,
        rep_maxes=tuple(rep_maxes),
        estimate=_line(prepared),
        exposures=len(exposures),
    )


def new_bests(prior: Sequence[Exposure], session: Exposure) -> tuple[Best, ...]:
    """The session's sets no earlier set dominates — its new personal bests.

    Only the session's own frontier is reported (100 × 7 and 100 × 8 on one
    day is one best, the 8), and nothing at all when there is no earlier set
    to beat (the module docstring says why).
    """
    before = _frontier(_prepared(prior))
    if not before:
        return ()
    return tuple(
        best for best in _frontier(_prepared([session])) if not _dominated(best, before)
    )
