"""Discipline classification and the athlete-local day a session belongs to.

The date is the join key WP-6 matches on and the column the session list pages
by, so getting it wrong by a timezone is getting every match and every week
total wrong for the sessions near midnight — which is most evening rides in
summer. Hypothesis covers the offsets rather than the three we would think to
write down.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.activity import (
    UTC_TIMEZONE,
    ClassificationSource,
    SessionDiscipline,
    as_planned_discipline,
    classify_discipline,
    fixed_offset_label,
    parse_timezone,
    session_date,
    timezone_label,
)
from app.domain.athlete import Discipline

# --- the vocabularies line up -------------------------------------------------


def test_every_planned_discipline_is_also_a_recorded_one() -> None:
    # The two enums are separate on purpose (a file can hold a sport we never
    # plan), but the members they share must share their values or WP-6's
    # candidate query silently matches nothing.
    for discipline in Discipline:
        assert SessionDiscipline(discipline.value)
        assert as_planned_discipline(SessionDiscipline(discipline.value)) is discipline


def test_other_maps_to_no_planned_discipline() -> None:
    assert as_planned_discipline(SessionDiscipline.OTHER) is None


# --- classification (A-5) -----------------------------------------------------

CASES = [
    # (name, sport, has_power, has_speed, has_gps, duration_s, discipline, source)
    (
        "outdoor ride names itself",
        "cycling",
        True,
        True,
        True,
        7200.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.SPORT_FIELD,
    ),
    (
        "the sport field wins over the channels",
        "strength_training",
        False,
        True,
        False,
        3000.0,
        SessionDiscipline.STRENGTH,
        ClassificationSource.SPORT_FIELD,
    ),
    (
        "sport strings are matched case- and space-insensitively",
        "  Cycling ",
        False,
        False,
        False,
        3000.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.SPORT_FIELD,
    ),
    (
        "strava spells a trainer ride VirtualRide",
        "VirtualRide",
        True,
        False,
        False,
        3600.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.SPORT_FIELD,
    ),
    (
        "every other source spells it virtual_ride",
        "virtual_ride",
        True,
        False,
        False,
        3600.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.SPORT_FIELD,
    ),
    (
        "power without a sport field is a ride",
        None,
        True,
        False,
        False,
        3600.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.HEURISTIC,
    ),
    (
        "an unknown sport falls through to the channels",
        "e_bike_fitness",
        False,
        True,
        True,
        3600.0,
        SessionDiscipline.CYCLING,
        ClassificationSource.HEURISTIC,
    ),
    (
        "short, indoors, heart rate only is the gym",
        None,
        False,
        False,
        False,
        2700.0,
        SessionDiscipline.STRENGTH,
        ClassificationSource.HEURISTIC,
    ),
    (
        "a long recording with no channels is not guessed at",
        None,
        False,
        False,
        False,
        9000.0,
        SessionDiscipline.OTHER,
        ClassificationSource.HEURISTIC,
    ),
    (
        "a GPS walk is not a gym session",
        None,
        False,
        False,
        True,
        2700.0,
        SessionDiscipline.OTHER,
        ClassificationSource.HEURISTIC,
    ),
]


@pytest.mark.parametrize(
    ("sport", "has_power", "has_speed", "has_gps", "duration_s", "expected", "source"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_classification_table(
    sport: str | None,
    has_power: bool,
    has_speed: bool,
    has_gps: bool,
    duration_s: float,
    expected: SessionDiscipline,
    source: ClassificationSource,
) -> None:
    assert classify_discipline(
        sport=sport,
        has_power=has_power,
        has_speed=has_speed,
        has_gps=has_gps,
        duration_s=duration_s,
    ) == (expected, source)


# --- timezone labels ----------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (dt.timedelta(0), "UTC"),
        (dt.timedelta(hours=2), "UTC+02:00"),
        (dt.timedelta(hours=-5), "UTC-05:00"),
        (dt.timedelta(hours=5, minutes=45), "UTC+05:45"),
        (dt.timedelta(hours=-3, minutes=-30), "UTC-03:30"),
    ],
)
def test_fixed_offset_label(offset: dt.timedelta, expected: str) -> None:
    assert fixed_offset_label(offset) == expected


def test_a_sub_minute_offset_is_refused() -> None:
    with pytest.raises(ValueError, match="whole number of minutes"):
        fixed_offset_label(dt.timedelta(seconds=90))


def test_no_local_offset_means_the_file_did_not_say() -> None:
    assert timezone_label(None) == UTC_TIMEZONE


@given(st.integers(min_value=-12 * 60, max_value=14 * 60).filter(lambda m: m % 15 == 0))
def test_a_label_round_trips_through_parse(minutes: int) -> None:
    offset = dt.timedelta(minutes=minutes)

    resolved = parse_timezone(timezone_label(offset))

    assert resolved.utcoffset(None) == offset


def test_an_iana_name_resolves() -> None:
    # Rare from a device file, but the column accepts one, so it must work —
    # and on alpine it only does because the image installs tzdata.
    assert parse_timezone("Europe/Berlin").utcoffset(
        dt.datetime(2026, 7, 1, 12)  # noqa: DTZ001
    ) == dt.timedelta(hours=2)


@pytest.mark.parametrize("bad", ["", "UTC+2", "utc+02:00", "Mars/Olympus", "+02:00"])
def test_an_unresolvable_timezone_fails_loudly(bad: str) -> None:
    with pytest.raises(ValueError, match="IANA|offset"):
        parse_timezone(bad)


@pytest.mark.parametrize("special", ["localtime", "Factory"])
def test_a_zone_database_key_that_is_not_an_iana_name_is_refused(special: str) -> None:
    # `zoneinfo` resolves both — they are TZif files on its search path — but
    # `localtime` is whatever the container's clock is set to and `Factory` is
    # the database's "unset" placeholder. The browser's `Intl` resolves
    # neither, so a PATCH that stored one would give the UI a timezone it
    # cannot render and silently re-derive the session's local date from
    # /etc/localtime.
    assert ZoneInfo(special), "the standard library accepts it; we must not"

    with pytest.raises(ValueError, match="IANA"):
        parse_timezone(special)


def test_a_real_region_name_still_resolves() -> None:
    assert parse_timezone("Europe/Zurich").utcoffset(
        dt.datetime(2026, 7, 1, 12)  # noqa: DTZ001
    ) == dt.timedelta(hours=2)


# --- session_date (A-6) -------------------------------------------------------


def test_a_midnight_crosser_belongs_to_the_day_it_began() -> None:
    # 23:40 local on the 4th, still riding at 00:20 on the 5th.
    start = dt.datetime(2026, 5, 4, 21, 40, tzinfo=dt.UTC)

    assert session_date(start, "UTC+02:00") == dt.date(2026, 5, 4)


def test_the_local_day_can_differ_from_the_utc_day_in_both_directions() -> None:
    late_evening = dt.datetime(2026, 5, 4, 23, 30, tzinfo=dt.UTC)
    early_morning = dt.datetime(2026, 5, 4, 0, 30, tzinfo=dt.UTC)

    assert session_date(late_evening, "UTC+02:00") == dt.date(2026, 5, 5)
    assert session_date(early_morning, "UTC-05:00") == dt.date(2026, 5, 3)
    assert session_date(late_evening, UTC_TIMEZONE) == dt.date(2026, 5, 4)


def test_a_naive_start_is_refused() -> None:
    with pytest.raises(ValueError, match="aware start time"):
        session_date(dt.datetime(2026, 5, 4, 7, 30), UTC_TIMEZONE)  # noqa: DTZ001


@given(
    start=st.datetimes(
        min_value=dt.datetime(2000, 1, 1),
        max_value=dt.datetime(2100, 1, 1),
        timezones=st.just(dt.UTC),
    ),
    minutes=st.integers(min_value=-12 * 60, max_value=14 * 60).filter(
        lambda m: m % 15 == 0
    ),
)
def test_the_session_date_is_the_local_calendar_date_of_the_start(
    start: dt.datetime, minutes: int
) -> None:
    tz = timezone_label(dt.timedelta(minutes=minutes))

    derived = session_date(start, tz)

    local = start + dt.timedelta(minutes=minutes)
    assert derived == local.date()
    # Never more than a day either side of the UTC date — the property that
    # catches an offset applied twice or in the wrong direction.
    assert abs((derived - start.date()).days) <= 1
