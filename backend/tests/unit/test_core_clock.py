"""The athlete's clock: one answer to "what day is it", and which one.

`app.core.clock` exists because the answer used to be given four ways in one
process — UTC, the container's `TZ`, the browser's, and the configured
`MATCHING__TIMEZONE` — and `get_coaching_context` built a single payload out of
three of them (issue #62). These tests pin the one that is left.

Every case here moves the athlete off UTC. The setting defaults to ``UTC``, so
a test that does not is a test that cannot tell a correct clock from a wrong
one — which is exactly how the other three survived a green suite.
"""

import datetime as dt
from collections.abc import Callable

import pytest

from app.core.clock import athlete_now, athlete_timezone, athlete_today

#: Tuesday 23:00 UTC — already Wednesday in Auckland (+12/+13), still Tuesday
#: afternoon in New York (-4/-5). One instant, two calendar days.
ACROSS_MIDNIGHT = dt.datetime(2026, 8, 11, 23, 0, tzinfo=dt.UTC)


def test_today_is_the_configured_zones_day_not_the_utc_one(
    athlete_zone: Callable[[str], None],
) -> None:
    athlete_zone("Pacific/Auckland")
    assert athlete_today(ACROSS_MIDNIGHT) == dt.date(2026, 8, 12)

    athlete_zone("America/New_York")
    assert athlete_today(ACROSS_MIDNIGHT) == dt.date(2026, 8, 11)

    # The UTC answer, which is neither athlete's, and was what the plan week
    # and the anchor histories used to give.
    assert ACROSS_MIDNIGHT.date() == dt.date(2026, 8, 11)


def test_the_zone_is_resolved_through_the_database_not_read_as_an_offset(
    athlete_zone: Callable[[str], None],
) -> None:
    """A region name is not an offset, and Auckland's changes by an hour.

    Same clock time on both instants — 11:00 UTC — six months apart, either
    side of the southern DST boundary. Auckland is +13 in April and +12 in
    September, so the *same* UTC hour is two different calendar days there.
    Anything that resolved the zone once and reused the offset, or that read
    two digits off a string, gets exactly one of these two right.
    """
    athlete_zone("Pacific/Auckland")

    april = dt.datetime(2026, 4, 4, 11, 0, tzinfo=dt.UTC)  # NZDT, +13
    september = dt.datetime(2026, 9, 26, 11, 0, tzinfo=dt.UTC)  # NZST, +12

    assert athlete_today(april) == dt.date(2026, 4, 5)
    assert athlete_today(september) == dt.date(2026, 9, 26)
    # A fixed offset would agree with one and not the other, whichever is
    # chosen — which is why `MATCHING__TIMEZONE` asks for the region name.
    assert athlete_today(april) != (april + dt.timedelta(hours=12)).date()
    assert athlete_today(september) != (september + dt.timedelta(hours=13)).date()


def test_the_local_hour_survives_a_spring_forward(
    athlete_zone: Callable[[str], None],
) -> None:
    """17:00 UTC is 18:00 in Berlin on the 28th and 19:00 on the 29th.

    Europe/Berlin springs forward at 01:00 UTC on 2026-03-29. The evening
    arrives an hour earlier in UTC terms from that instant on, and
    `WELLNESS__PROMPT_HOUR_LOCAL` is a statement about the athlete's evening —
    so this hour, not the UTC one, is what decides whether the day's question
    has been asked yet.
    """
    athlete_zone("Europe/Berlin")

    before = athlete_now(dt.datetime(2026, 3, 28, 17, 0, tzinfo=dt.UTC))
    after = athlete_now(dt.datetime(2026, 3, 29, 17, 0, tzinfo=dt.UTC))

    assert (before.hour, before.utcoffset()) == (18, dt.timedelta(hours=1))
    assert (after.hour, after.utcoffset()) == (19, dt.timedelta(hours=2))


def test_the_local_hour_survives_a_fall_back(
    athlete_zone: Callable[[str], None],
) -> None:
    """And back again: 17:00 UTC is 19:00 on 24 October and 18:00 on the 25th."""
    athlete_zone("Europe/Berlin")

    before = athlete_now(dt.datetime(2026, 10, 24, 17, 0, tzinfo=dt.UTC))
    after = athlete_now(dt.datetime(2026, 10, 25, 17, 0, tzinfo=dt.UTC))

    assert (before.hour, before.utcoffset()) == (19, dt.timedelta(hours=2))
    assert (after.hour, after.utcoffset()) == (18, dt.timedelta(hours=1))


def test_a_fixed_offset_and_plain_utc_are_both_accepted(
    athlete_zone: Callable[[str], None],
) -> None:
    """The three forms `parse_timezone` takes, since this is where they land."""
    athlete_zone("UTC+05:30")
    assert athlete_today(ACROSS_MIDNIGHT) == dt.date(2026, 8, 12)
    assert athlete_timezone() == "UTC+05:30"

    athlete_zone("UTC")
    assert athlete_today(ACROSS_MIDNIGHT) == dt.date(2026, 8, 11)


def test_an_unresolvable_zone_is_loud_rather_than_defaulted(
    athlete_zone: Callable[[str], None],
) -> None:
    """No silent fall back to UTC — that is the bug, not the recovery.

    A sweep that quietly used UTC would mark sessions missed up to a day early
    for anybody east of it, and nothing would say so.
    """
    athlete_zone("Not/AZone")

    with pytest.raises(ValueError, match="timezone"):
        athlete_today(ACROSS_MIDNIGHT)
    with pytest.raises(ValueError, match="timezone"):
        athlete_timezone()


def test_a_naive_instant_is_refused(athlete_zone: Callable[[str], None]) -> None:
    """The failure this whole module exists to prevent, at its own door."""
    athlete_zone("Europe/Berlin")

    with pytest.raises(ValueError, match="aware"):
        athlete_now(dt.datetime(2026, 8, 11, 23, 0))  # noqa: DTZ001


def test_changing_the_setting_changes_the_answer(
    athlete_zone: Callable[[str], None],
) -> None:
    """The resolved zone is cached by *name*, so a re-read cannot go stale.

    `parse_timezone` scans the whole zone database on its IANA branch and this
    runs on every dated read, so the resolution is memoised. Keyed on anything
    coarser than the name, a deployment that changed the setting — or the test
    above it — would keep getting the previous zone's day.
    """
    athlete_zone("Pacific/Auckland")
    first = athlete_today(ACROSS_MIDNIGHT)
    athlete_zone("America/New_York")
    second = athlete_today(ACROSS_MIDNIGHT)
    athlete_zone("Pacific/Auckland")
    third = athlete_today(ACROSS_MIDNIGHT)

    assert first == third == dt.date(2026, 8, 12)
    assert second == dt.date(2026, 8, 11)


def test_today_and_now_agree_with_each_other(
    athlete_zone: Callable[[str], None],
) -> None:
    """Read from the live clock, since every production call site does."""
    athlete_zone("Pacific/Auckland")

    moment = athlete_now()
    assert moment.tzinfo is not None
    assert athlete_today(moment.astimezone(dt.UTC)) == moment.date()
