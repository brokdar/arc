"""Mean-maximal power: the grid, the exact search, and how gaps are filled.

Pure domain, so every assertion here is on the arrays and points
`app.domain.power_curve` returns — the stored row and the endpoint are the
same numbers projected, and are tested at their own layers.
"""

import math
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.power_curve import (
    GRID_VERSION,
    MMP_INTERP_MAX_GAP_S,
    MmpPoint,
    duration_grid,
    fill_gaps,
    mmp,
    power_curve,
)


def by_duration(points: tuple[MmpPoint, ...]) -> dict[int, MmpPoint]:
    """The points keyed by their duration."""
    return {point.duration_s: point for point in points}


# --- AC-10: the grid ---------------------------------------------------------------


def test_grid_is_pinned() -> None:
    # The grid is GoldenCheetah's, and every stored row carries the version
    # it was computed on. Changing a step changes what a stored row means, so
    # this test is the tripwire: a new grid is a new GRID_VERSION.
    grid = duration_grid(7_300)

    expected = [
        *range(1, 120),
        *range(120, 600, 2),
        *range(600, 1200, 5),
        *range(1200, 3600, 20),
        *range(3600, 7200, 120),
        7200,
    ]
    assert list(grid) == expected
    assert max(grid) == 7200
    assert all(duration in grid for duration in (5, 60, 300, 1200, 3600, 7200))
    assert GRID_VERSION == 1


def test_the_grid_steps_change_where_goldencheetahs_do() -> None:
    grid = duration_grid(7_300)

    assert grid[grid.index(119) : grid.index(119) + 3] == (119, 120, 122)
    assert grid[grid.index(598) : grid.index(598) + 3] == (598, 600, 605)
    assert grid[grid.index(1195) : grid.index(1195) + 3] == (1195, 1200, 1220)
    assert grid[grid.index(3580) : grid.index(3580) + 3] == (3580, 3600, 3720)
    assert 2 in grid
    assert 121 not in grid
    assert 599 not in grid


def test_the_grid_beyond_two_hours_steps_by_five_minutes() -> None:
    assert duration_grid(8_000)[-4:] == (7080, 7200, 7500, 7800)


def test_a_one_second_ride_has_one_point_and_an_empty_one_has_none() -> None:
    assert duration_grid(1) == (1,)
    assert duration_grid(0) == ()


def test_a_duration_equal_to_the_ride_is_included() -> None:
    assert duration_grid(600)[-1] == 600
    assert duration_grid(601)[-1] == 600
    assert duration_grid(3_600)[-1] == 3_600


# --- AC-11: the search -------------------------------------------------------------


def test_a_constant_ride_has_its_watts_at_every_duration_from_the_start() -> None:
    points = mmp([200.0] * 3_600)

    assert [point.duration_s for point in points] == list(duration_grid(3_600))
    assert all(point.watts == 200.0 for point in points)
    assert all(point.start_index == 0 for point in points)


def test_one_hard_block_is_the_short_best_and_dilutes_the_longer_one() -> None:
    watts = [150.0] * 600
    watts[100:105] = [500.0] * 5

    points = by_duration(mmp(watts))

    assert points[5].watts == 500.0
    assert points[5].start_index == 100
    # (500·5 + 150·5) / 10, first reached by the window [95, 105).
    assert points[10].watts == 325.0
    assert points[10].start_index == 95


def test_ties_go_to_the_earliest_start() -> None:
    # Two identical efforts: the first one is the answer, every time. Exact
    # integer sums are what make "identical" decidable — two float window sums
    # of the same numbers in a different order need not compare equal.
    watts = [100.0] * 400
    watts[50:60] = [400.0] * 10
    watts[300:310] = [400.0] * 10

    points = by_duration(mmp(watts))

    assert points[10].watts == 400.0
    assert points[10].start_index == 50
    assert points[3].start_index == 50


def test_ties_between_fractional_samples_are_still_exact() -> None:
    # 0.1 + 0.2 != 0.3 in floating point; the search must not care.
    watts = [0.1, 0.2, 0.0, 0.3, 0.0]

    points = by_duration(mmp(watts))

    assert points[2].start_index == 0
    assert points[2].watts == pytest.approx(0.2)


def test_a_duration_longer_than_the_ride_is_absent_not_padded() -> None:
    points = mmp([180.0] * 90)

    assert max(point.duration_s for point in points) == 90
    assert 92 not in by_duration(points)
    assert 120 not in by_duration(points)


def test_spans_are_half_open() -> None:
    # A window of d seconds starting at s covers [s, s+d): the sample at
    # s+d is *not* in it. A `+1` would let the 3 s best at index 0 swallow the
    # 900 W sample at index 3.
    watts = [300.0, 300.0, 300.0, 900.0, 0.0, 0.0]

    points = by_duration(mmp(watts))

    assert points[1].watts == 900.0
    assert points[1].start_index == 3
    assert points[3].watts == 500.0  # [1, 4): 300 + 300 + 900
    assert points[3].start_index == 1
    assert points[6].watts == 300.0  # all six, (900 + 900) / 6


def test_watts_are_rounded_to_a_tenth_of_a_watt() -> None:
    points = by_duration(mmp([100.0, 100.0, 101.0]))

    assert points[3].watts == 100.3
    assert points[2].watts == 100.5


@given(
    st.lists(
        st.integers(min_value=0, max_value=2_000).map(float),
        min_size=1,
        max_size=200,
    )
)
def test_every_point_is_the_brute_force_best(watts: list[float]) -> None:
    for point in mmp(watts):
        d = point.duration_s
        sums = [sum(watts[s : s + d]) for s in range(len(watts) - d + 1)]
        best = max(sums)
        assert point.start_index == sums.index(best)
        # Half-up to a tenth, computed exactly: `round` is half-to-even.
        assert point.watts == math.floor(Fraction(best * 10) / d + Fraction(1, 2)) / 10


# --- AC-12: gaps -------------------------------------------------------------------


def test_gap_of_eight_is_interpolated_nine_is_zero() -> None:
    assert MMP_INTERP_MAX_GAP_S == 8

    eight = fill_gaps([100.0, *([None] * 8), 200.0])
    assert eight.watts[1:9] == pytest.approx([100 + 100 * k / 9 for k in range(1, 9)])
    assert eight.watts[0] == 100.0
    assert eight.watts[9] == 200.0
    assert not any(eight.zero_filled)

    nine = fill_gaps([100.0, *([None] * 9), 200.0])
    assert nine.watts[1:10] == (0.0,) * 9
    assert nine.zero_filled == (False, *([True] * 9), False)


def test_a_run_at_either_end_of_the_ride_is_zero() -> None:
    filled = fill_gaps([None, None, 150.0, 160.0, None])

    assert filled.watts == (0.0, 0.0, 150.0, 160.0, 0.0)
    assert filled.zero_filled == (True, True, False, False, True)


def test_two_short_runs_one_sample_apart_are_each_interpolated() -> None:
    filled = fill_gaps([100.0, *([None] * 5), 160.0, *([None] * 5), 100.0])

    assert filled.watts[1:6] == pytest.approx([110.0, 120.0, 130.0, 140.0, 150.0])
    assert filled.watts[6] == 160.0
    assert filled.watts[7:12] == pytest.approx([150.0, 140.0, 130.0, 120.0, 110.0])
    assert not any(filled.zero_filled)


def test_a_recording_stop_is_zero_filled_whatever_its_length() -> None:
    for length in (31, 600, 3_600):
        filled = fill_gaps([200.0, *([None] * length), 200.0])

        assert filled.watts[1:-1] == (0.0,) * length
        assert all(filled.zero_filled[1:-1])


def test_clean_flag() -> None:
    # The winning span either contains a second arc invented (a zero-filled
    # gap, or a region the cleaner repaired) or it does not, and the flag is
    # computed now, from the stream the curve was computed from — a later
    # rebuild cannot change what a stored best was standing on.
    column: list[float | None] = [150.0] * 400
    column[50:55] = [400.0] * 5  # a real effort, nowhere near a gap
    column[200:220] = [None] * 20  # a dropout: zero-filled
    curve = power_curve(column)
    assert curve is not None
    points = by_duration(curve.points)

    assert points[5].start_index == 50
    assert points[5].clean is True
    # The whole ride necessarily spans the zero-filled seconds.
    assert points[400].clean is False


def test_a_repaired_region_in_the_winning_span_is_not_clean() -> None:
    column: list[float | None] = [150.0] * 300
    column[100:105] = [600.0] * 5

    unrepaired = power_curve(column)
    repaired = power_curve(column, anomalies=[(102, 103)])
    assert unrepaired is not None
    assert repaired is not None

    assert by_duration(unrepaired.points)[5].clean is True
    assert by_duration(repaired.points)[5].clean is False
    assert by_duration(repaired.points)[1].start_index == 100
    assert by_duration(repaired.points)[1].clean is True


# --- the whole curve ---------------------------------------------------------------


def test_a_column_with_no_power_has_no_curve() -> None:
    assert power_curve(None) is None
    assert power_curve([]) is None
    assert power_curve([None] * 600) is None


def test_the_curve_covers_the_seconds_the_meter_recorded() -> None:
    # A meter that paired late or died early recorded nothing, not zero watts:
    # the curve spans first to last reading, and indices stay on the ride's
    # own grid.
    column: list[float | None] = [None] * 10 + [300.0] * 4 + [None] * 3_000

    curve = power_curve(column)
    assert curve is not None

    assert [point.duration_s for point in curve.points] == [1, 2, 3, 4]
    assert all(point.watts == 300.0 for point in curve.points)
    assert all(point.start_index == 10 for point in curve.points)
    assert curve.grid_version == GRID_VERSION


def test_an_interpolated_dropout_counts_as_clean_power() -> None:
    column: list[float | None] = [200.0] * 100
    column[40:44] = [None] * 4

    curve = power_curve(column)
    assert curve is not None

    assert all(point.clean for point in curve.points)
    assert by_duration(curve.points)[100].watts == 200.0
