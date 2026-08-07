"""The plan's calendar arithmetic: week windows and plan state."""

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.plan import WEEK_DAYS, PlanState, week_dates, week_start


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 8, 10), dt.date(2026, 8, 10)),  # a Monday is its own start
        (dt.date(2026, 8, 16), dt.date(2026, 8, 10)),  # Sunday belongs to it
        (dt.date(2026, 8, 17), dt.date(2026, 8, 17)),  # the next Monday starts anew
        (dt.date(2026, 1, 1), dt.date(2025, 12, 29)),  # weeks cross the year
    ],
)
def test_week_start_is_the_monday_of_that_week(day: dt.date, expected: dt.date) -> None:
    assert week_start(day) == expected


@given(st.dates())
def test_every_day_starts_a_week_that_is_a_monday_no_later_than_it(
    day: dt.date,
) -> None:
    start = week_start(day)

    assert start.weekday() == 0
    assert start <= day < start + dt.timedelta(days=WEEK_DAYS)


@given(st.dates(max_value=dt.date(9999, 12, 20)))
def test_a_week_is_seven_consecutive_days_from_where_it_is_asked_to_start(
    start: dt.date,
) -> None:
    days = week_dates(start)

    assert len(days) == WEEK_DAYS
    assert days[0] == start
    assert days[-1] == start + dt.timedelta(days=WEEK_DAYS - 1)
    assert all(
        later - earlier == dt.timedelta(days=1)
        for earlier, later in zip(days, days[1:], strict=False)
    )


def test_week_dates_does_not_snap_its_start() -> None:
    # `week_start` is where Monday is decided; `week_dates` renders whatever
    # window it is handed, so a caller can page the calendar by a day.
    wednesday = dt.date(2026, 8, 12)

    assert week_dates(wednesday)[0] == wednesday


def test_a_plan_is_active_or_paused() -> None:
    assert [state.value for state in PlanState] == ["active", "paused"]
