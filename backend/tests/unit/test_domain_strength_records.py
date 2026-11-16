"""Personal bests and the estimated max (AC-64, AC-65).

Pure domain: every case is a `PerformedSet` history in, a record out. The
API and MCP tests prove the same numbers reach the athlete and the coach.
"""

import datetime as dt
import uuid

import pytest

from app.domain.activity import LoggedSetKind, SetSide
from app.domain.metrics import PerformedSet
from app.domain.rules import E1RM_MAX_REPS
from app.domain.strength_records import (
    Exposure,
    estimate_line,
    estimated_max,
    exercise_records,
    new_bests,
)

DAY = dt.date(2026, 9, 1)


def lift(load: float | None, reps: int | None, **extra: object) -> PerformedSet:
    """One rep set; extra fields (rir, kind, side, ...) pass through."""
    return PerformedSet(load_kg=load, reps=reps, **extra)  # type: ignore[arg-type]


def exposure(*sets: PerformedSet, day: int = 0, dont_learn: bool = False) -> Exposure:
    """One session's sets of one exercise, ``day`` days after :data:`DAY`."""
    return Exposure(
        session_id=uuid.uuid7(),
        date=DAY + dt.timedelta(days=day),
        sets=sets,
        dont_learn=dont_learn,
    )


# --- AC-64: the estimated max --------------------------------------------------------


def test_100kg_for_5_with_2_left_estimates_123_3kg() -> None:
    estimate = estimated_max(lift(100.0, 5, rir=2))

    assert estimate is not None
    assert round(estimate, 1) == 123.3


def test_an_rpe_rating_counts_as_reps_left() -> None:
    # RPE 8 is 2 reps left (Zourdos): the same set as `rir=2`.
    estimate = estimated_max(lift(100.0, 5, rpe=8.0))

    assert estimate is not None
    assert round(estimate, 1) == 123.3


def test_reps_plus_reps_left_of_exactly_ten_still_estimates() -> None:
    assert E1RM_MAX_REPS.value == 10
    estimate = estimated_max(lift(100.0, 8, rir=2))

    assert estimate is not None
    assert round(estimate, 1) == 133.3


@pytest.mark.parametrize(
    ("label", "entry"),
    [
        ("reps + reps left = 11", lift(100.0, 9, rir=2)),
        ("unrated", lift(100.0, 5)),
        ("4+", lift(100.0, 5, rir=4, rir_capped=True)),
        ("warm-up", lift(100.0, 5, rir=2, kind=LoggedSetKind.WARMUP)),
        ("timed", PerformedSet(duration_s=40, load_kg=20.0, rir=2)),
        ("bodyweight", lift(None, 5, rir=2)),
    ],
)
def test_ineligible_sets_estimate_nothing(label: str, entry: PerformedSet) -> None:
    assert estimated_max(entry) is None, label


def test_ineligible_sets_and_a_dont_learn_session_contribute_nothing_to_the_line() -> (
    None
):
    valid = [exposure(lift(100.0, 5, rir=2), day=day) for day in range(3)]
    noise = [
        exposure(lift(200.0, 9, rir=2), day=10),
        exposure(lift(200.0, 5), day=11),
        exposure(lift(200.0, 5, rir=4, rir_capped=True), day=12),
        exposure(lift(200.0, 5, rir=2, kind=LoggedSetKind.WARMUP), day=13),
        exposure(lift(200.0, 5, rir=2), day=14, dont_learn=True),
    ]

    line = estimate_line([*valid, *noise])

    assert line.status == "ok"
    assert [round(p.estimate_kg, 1) for p in line.points] == [123.3, 123.3, 123.3]
    assert line.current_kg == 123.3


def test_the_line_is_the_median_of_the_last_three_exposures() -> None:
    # Exposure estimates 116.7, 133.3, 123.3, 100 + 0 left ×5 = 116.7.
    history = [
        exposure(lift(100.0, 5, rir=0), day=0),
        exposure(lift(100.0, 8, rir=2), day=1),
        exposure(lift(100.0, 5, rir=2), lift(90.0, 5, rir=2), day=2),
        exposure(lift(100.0, 5, rir=0), day=3),
    ]

    line = estimate_line(history)

    assert [p.estimate_kg for p in line.points] == [116.7, 133.3, 123.3, 116.7]
    assert [p.median_kg for p in line.points] == [None, None, 123.3, 123.3]
    assert line.current_kg == 123.3
    assert line.status == "ok"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fewer_than_three_exposures_with_an_estimate_is_not_enough_data(
    count: int,
) -> None:
    # Unrated exposures in between do not count towards the three.
    history = [exposure(lift(100.0, 5, rir=2), day=d) for d in range(count)]
    history.append(exposure(lift(100.0, 5), day=9))

    line = estimate_line(history)

    assert line.status == "not_enough_data"
    assert line.current_kg is None
    assert all(p.median_kg is None for p in line.points)


# --- AC-65: personal bests -----------------------------------------------------------


def test_heavier_for_the_same_reps_is_a_new_best() -> None:
    prior = [exposure(lift(100.0, 7))]

    found = new_bests(prior, exposure(lift(102.5, 7), day=1))

    assert [(b.load_kg, b.reps) for b in found] == [(102.5, 7)]


def test_more_reps_at_the_same_weight_is_a_new_best() -> None:
    prior = [exposure(lift(100.0, 7))]

    found = new_bests(prior, exposure(lift(100.0, 8), day=1))

    assert [(b.load_kg, b.reps) for b in found] == [(100.0, 8)]


@pytest.mark.parametrize("reps", [6, 7])
def test_fewer_or_equal_reps_at_the_same_weight_is_not(reps: int) -> None:
    prior = [exposure(lift(100.0, 7))]

    assert new_bests(prior, exposure(lift(100.0, reps), day=1)) == ()


def test_an_estimate_rise_alone_is_never_a_personal_best() -> None:
    # Same 100 kg × 5, now with 3 left instead of 0: the estimate climbs from
    # 116.7 to 126.7 kg, but nothing heavier or longer was lifted.
    prior = [exposure(lift(100.0, 5, rir=0), day=d) for d in range(3)]
    session = exposure(lift(100.0, 5, rir=3), day=5)
    before = estimate_line(prior).points[-1].estimate_kg
    after = estimate_line([*prior, session]).points[-1].estimate_kg
    assert after > before

    assert new_bests(prior, session) == ()


def test_a_session_reports_only_its_own_frontier() -> None:
    # 100 × 7 and 100 × 8 are both above the old 100 × 6; only 8 is the best.
    prior = [exposure(lift(100.0, 6))]

    found = new_bests(prior, exposure(lift(100.0, 7), lift(100.0, 8), day=1))

    assert [(b.load_kg, b.reps) for b in found] == [(100.0, 8)]


def test_a_first_ever_session_announces_nothing() -> None:
    assert new_bests([], exposure(lift(100.0, 8), day=1)) == ()


def test_warmups_and_timed_sets_never_set_a_best() -> None:
    prior = [exposure(lift(100.0, 7))]
    session = exposure(
        lift(120.0, 8, kind=LoggedSetKind.WARMUP),
        PerformedSet(duration_s=60, load_kg=150.0),
        day=1,
    )

    assert new_bests(prior, session) == ()


def test_a_one_side_lift_records_its_weaker_side() -> None:
    # L 10 and R 8 at 20 kg: the record is 8 reps — what both sides did.
    session = exposure(
        lift(20.0, 10, side=SetSide.LEFT), lift(20.0, 8, side=SetSide.RIGHT)
    )

    records = exercise_records([session])

    assert [(b.load_kg, b.reps) for b in records.bests] == [(20.0, 8)]


def test_a_one_side_best_is_judged_on_the_weaker_side() -> None:
    prior = [
        exposure(lift(20.0, 8, side=SetSide.LEFT), lift(20.0, 8, side=SetSide.RIGHT))
    ]
    session = exposure(
        lift(20.0, 10, side=SetSide.LEFT), lift(20.0, 8, side=SetSide.RIGHT), day=1
    )

    assert new_bests(prior, session) == ()


def test_rep_maxes_are_the_heaviest_weight_for_at_least_that_many_reps() -> None:
    history = [
        exposure(lift(140.0, 1), lift(120.0, 5), day=0),
        exposure(lift(110.0, 8), lift(125.0, 3), day=1),
    ]

    records = exercise_records(history)

    assert {m.target_reps: (m.load_kg, m.reps) for m in records.rep_maxes} == {
        1: (140.0, 1),
        3: (125.0, 3),
        5: (120.0, 5),
        8: (110.0, 8),
    }
    # The dominance frontier, heaviest first.
    assert [(b.load_kg, b.reps) for b in records.bests] == [
        (140.0, 1),
        (125.0, 3),
        (120.0, 5),
        (110.0, 8),
    ]


def test_a_tied_best_keeps_the_day_it_was_first_lifted() -> None:
    first = exposure(lift(100.0, 5), day=0)
    again = exposure(lift(100.0, 5), day=7)

    records = exercise_records([first, again])

    assert [b.date for b in records.bests] == [first.date]


def test_no_history_is_empty_records() -> None:
    records = exercise_records([])

    assert records.bests == ()
    assert records.rep_maxes == ()
    assert records.estimate.points == ()
    assert records.estimate.status == "not_enough_data"
