"""The performance-management chart: fitness, fatigue and form, by the day.

Pure-domain tests over `app.domain.pmc`. The numbers asserted here are the
EWMA's own arithmetic, written out beside each assertion so a reader can check
them with a calculator rather than trusting a constant: `α = 1 − e^(−1/τ)`,
`X_d = X_{d−1} + α·(load_d − X_{d−1})`.
"""

import datetime as dt
import math

import pytest

from app.domain.activity import SessionDiscipline
from app.domain.pmc import (
    ALPHA_ATL,
    ALPHA_CTL,
    MATURITY_SEED_WEIGHT,
    TAU_ATL,
    TAU_CTL,
    DailyLoad,
    Seed,
    SessionLoad,
    alpha,
    daily_loads,
    fitness_trend,
    form,
    matures_on_day,
    maturity,
    pmc,
)

CYCLING = SessionDiscipline.CYCLING
STRENGTH = SessionDiscipline.STRENGTH

FIRST = dt.date(2026, 3, 2)


def ride(day: dt.date, load: float | None) -> SessionLoad:
    """A cycling session's load on a local date."""
    return SessionLoad(local_date=day, discipline=CYCLING, load=load)


def on(offset: int) -> dt.date:
    """The date ``offset`` days after :data:`FIRST`."""
    return FIRST + dt.timedelta(days=offset)


# --- the smoothing -----------------------------------------------------------


def test_alpha_is_one_minus_exp() -> None:
    # The exponential form, not the linear `1/τ` weight: they differ in the
    # fourth decimal at τ = 42 and in the third at τ = 7, which is the size of
    # disagreement a verification run against intervals.icu would flag.
    assert alpha(42) == pytest.approx(1 - math.exp(-1 / 42), abs=1e-15)
    assert alpha(7) == pytest.approx(1 - math.exp(-1 / 7), abs=1e-15)
    # The plan this was built from quoted α_C = 0.023530 and α_A = 0.133071
    # beside the same formula. Those are not its values — 1 − e^(−1/42) is
    # 0.0235283 and 1 − e^(−1/7) is 0.1331221 — so the formula, which is the
    # decision, wins over the arithmetic slip, and the constants below are
    # the formula's own.
    assert round(ALPHA_CTL, 6) == 0.023528
    assert round(ALPHA_ATL, 6) == 0.133122
    assert pytest.approx(1 / 42, abs=1e-6) != ALPHA_CTL
    assert (TAU_CTL, TAU_ATL) == (42, 7)


def test_one_day_of_load_then_rest() -> None:
    points = pmc(daily_loads([ride(FIRST, 100.0)], through=on(1)))

    day1, day2 = points
    # 100 × α on day 1 (to 4 decimals: 100 × 0.0235283, 100 × 0.1331221).
    assert round(day1.ctl, 4) == 2.3528
    assert round(day1.atl, 4) == 13.3122
    # 2.3528 × (1 − 0.0235283): a rest day decays rather than holds.
    assert round(day2.ctl, 4) == 2.2975
    assert day2.ctl == pytest.approx(day1.ctl * (1 - ALPHA_CTL), abs=1e-12)


def test_days_without_a_session_are_in_the_series_and_decay() -> None:
    # A session, thirty empty days, a session: the thirty are present with
    # their decayed values, not skipped (skipping them would hold CTL flat
    # across a month off).
    points = pmc(daily_loads([ride(FIRST, 100.0), ride(on(31), 50.0)], through=on(31)))

    assert [point.local_date for point in points] == [on(i) for i in range(32)]
    ctl_0 = points[0].ctl
    assert points[30].ctl == pytest.approx(ctl_0 * (1 - ALPHA_CTL) ** 30, abs=1e-9)
    assert all(point.load.fatigue_load == 0.0 for point in points[1:31])


def test_an_empty_history_is_an_empty_series() -> None:
    assert daily_loads([], through=on(10)) == []
    assert pmc([]) == []
    trend = fitness_trend([], start=None, end=None, today=on(10))
    assert trend.points == ()


def test_the_series_starts_on_the_first_session_not_the_window() -> None:
    trend = fitness_trend([ride(on(5), 60.0)], start=on(0), end=None, today=on(8))

    assert trend.points[0].local_date == on(5)
    assert trend.points[0].day == 1
    assert trend.points[-1].local_date == on(8)


def test_a_window_slices_a_series_computed_from_the_first_session() -> None:
    # The window is a view: the values inside it are the ones the whole
    # history produces, never a fresh EWMA restarted at the window's edge.
    sessions = [ride(on(0), 100.0), ride(on(3), 80.0)]
    whole = fitness_trend(sessions, start=None, end=None, today=on(6))
    window = fitness_trend(sessions, start=on(2), end=on(5), today=on(6))

    assert [point.local_date for point in window.points] == [on(2), on(3), on(4)]
    assert window.points == whole.points[2:5]


def test_a_window_reaching_past_today_stops_at_today() -> None:
    trend = fitness_trend([ride(on(0), 50.0)], start=None, end=on(40), today=on(3))

    assert trend.points[-1].local_date == on(3)
    assert trend.as_of == on(3)


# --- the daily load ------------------------------------------------------------


def test_cycling_feeds_fitness_and_every_discipline_feeds_fatigue() -> None:
    [day] = daily_loads(
        [ride(FIRST, 80.0), SessionLoad(FIRST, STRENGTH, 30.0)], through=FIRST
    )

    assert day == DailyLoad(
        local_date=FIRST, fitness_load=80.0, fatigue_load=110.0, n=2, uncounted=0
    )
    [point] = pmc([day])
    assert point.ctl == pytest.approx(80 * ALPHA_CTL, abs=1e-12)
    assert point.atl == pytest.approx(110 * ALPHA_ATL, abs=1e-12)


def test_an_unpriced_session_marks_its_day_incomplete_and_adds_nothing() -> None:
    [day] = daily_loads(
        [ride(FIRST, 40.0), SessionLoad(FIRST, STRENGTH, None)], through=FIRST
    )

    assert day.load_incomplete is True
    assert (day.fatigue_load, day.n, day.uncounted) == (40.0, 1, 1)


def test_a_day_with_only_unpriced_sessions_has_no_load_not_a_zero() -> None:
    [day] = daily_loads([SessionLoad(FIRST, STRENGTH, None)], through=FIRST)

    assert day.load_incomplete is True
    assert day.load is None
    # A rest day, by contrast, is a real zero.
    [_, rest] = daily_loads([ride(FIRST, 10.0)], through=on(1))
    assert rest.load == 0.0
    assert rest.load_incomplete is False


def test_two_rides_on_one_day_add_before_smoothing() -> None:
    [day] = daily_loads([ride(FIRST, 30.0), ride(FIRST, 50.0)], through=FIRST)
    [point] = pmc([day])

    assert day.fitness_load == 80.0
    assert point.ctl == pytest.approx(80 * ALPHA_CTL, abs=1e-12)


# --- form ---------------------------------------------------------------------


def test_form_lag_is_a_parameter() -> None:
    points = pmc(daily_loads([ride(FIRST, 100.0), ride(on(1), 60.0)], through=on(2)))
    ctl = [point.ctl for point in points]
    atl = [point.atl for point in points]

    same_day = form(ctl, atl, lag_days=0)
    yesterday = form(ctl, atl, lag_days=1)

    assert same_day == [c - a for c, a in zip(ctl, atl, strict=True)]
    # Lag 1 is TrainingPeaks' convention: today's form is yesterday's balance,
    # and before the first day that is the seed's (0 − 0).
    assert yesterday == [0.0, ctl[0] - atl[0], ctl[1] - atl[1]]
    seeded = form(ctl, atl, lag_days=1, seed=Seed(ctl=50.0, atl=40.0))
    assert seeded[0] == 10.0


def test_the_series_carries_form_at_its_declared_lag() -> None:
    sessions = [ride(on(0), 100.0)]
    lagged = fitness_trend(sessions, start=None, end=None, today=on(2), form_lag_days=1)

    assert lagged.form_lag_days == 1
    assert lagged.points[1].tsb == pytest.approx(
        lagged.points[0].ctl - lagged.points[0].atl, abs=1e-12
    )


# --- maturity -----------------------------------------------------------------


def test_maturity_days() -> None:
    # The seed's remaining weight after n updates is (1 − α)ⁿ = e^(−n/τ). The
    # series is maturing while that is above 10 %: CTL through day 96
    # (e^(−96/42) = 0.1017), ATL through day 16 (e^(−16/7) = 0.1017).
    assert MATURITY_SEED_WEIGHT == 0.10
    assert matures_on_day(TAU_CTL, seeded=False) == 97
    assert matures_on_day(TAU_ATL, seeded=False) == 17
    assert (1 - ALPHA_CTL) ** 96 > 0.10 >= (1 - ALPHA_CTL) ** 97
    assert (1 - ALPHA_ATL) ** 16 > 0.10 >= (1 - ALPHA_ATL) ** 17

    assert maturity(96, tau=TAU_CTL, seeded=False).mature is False
    assert maturity(97, tau=TAU_CTL, seeded=False).mature is True
    assert maturity(16, tau=TAU_ATL, seeded=False).mature is False
    assert maturity(17, tau=TAU_ATL, seeded=False).mature is True


def test_day_one_is_maturing_for_both() -> None:
    assert maturity(1, tau=TAU_CTL, seeded=False).mature is False
    assert maturity(1, tau=TAU_ATL, seeded=False).mature is False


def test_a_supplied_seed_is_mature_from_day_one() -> None:
    assert maturity(1, tau=TAU_CTL, seeded=True).mature is True
    assert maturity(1, tau=TAU_ATL, seeded=True).mature is True
    assert matures_on_day(TAU_CTL, seeded=True) == 1

    trend = fitness_trend(
        [ride(FIRST, 50.0)],
        start=None,
        end=None,
        today=FIRST,
        seed=Seed(ctl=60.0, atl=55.0),
    )
    assert trend.maturity.ctl.mature is True
    assert trend.maturity.atl.mature is True
    assert trend.points[0].ctl == pytest.approx(60 + ALPHA_CTL * (50 - 60), abs=1e-12)


def test_a_seed_for_fitness_only_leaves_fatigue_maturing() -> None:
    seed = Seed(ctl=60.0)
    for day, mature in ((1, False), (16, False), (17, True)):
        trend = fitness_trend(
            [ride(FIRST, 50.0)], start=None, end=None, today=on(day - 1), seed=seed
        )
        assert trend.maturity.day == day
        assert trend.maturity.ctl.mature is True
        assert trend.maturity.atl.mature is mature, day


def test_the_series_reports_its_maturity_by_day() -> None:
    trend = fitness_trend([ride(FIRST, 50.0)], start=None, end=None, today=on(95))

    assert trend.maturity.day == 96
    assert trend.maturity.first_session == FIRST
    assert trend.maturity.ctl.mature is False
    assert trend.maturity.ctl.matures_on_day == 97
    assert trend.maturity.ctl.matures_on == on(96)
    assert trend.maturity.atl.mature is True
    assert trend.maturity.atl.matures_on == on(16)

    later = fitness_trend([ride(FIRST, 50.0)], start=None, end=None, today=on(96))
    assert later.maturity.day == 97
    assert later.maturity.ctl.mature is True


def test_a_window_after_day_97_counts_from_the_first_session() -> None:
    # The window starts on day 120; the count does not restart there.
    trend = fitness_trend(
        [ride(FIRST, 50.0)], start=on(119), end=on(130), today=on(200)
    )

    assert trend.points[0].day == 120
    assert trend.maturity.day == 130
    assert trend.maturity.ctl.mature is True
    assert trend.maturity.atl.mature is True


def test_a_window_before_the_first_session_has_no_points_and_a_maturity() -> None:
    trend = fitness_trend([ride(on(10), 50.0)], start=on(0), end=on(5), today=on(20))

    assert trend.points == ()
    assert trend.maturity.day is None
    assert trend.maturity.first_session == on(10)
    assert trend.maturity.ctl.mature is False
    assert trend.maturity.ctl.matures_on == on(10 + 96)


def test_first_priced_is_the_first_priced_session_the_read_reaches() -> None:
    sessions = [
        ride(FIRST, None),
        SessionLoad(local_date=on(1), discipline=STRENGTH, load=None),
        ride(on(3), 40.0),
    ]

    whole = fitness_trend(sessions, start=None, end=None, today=on(5))
    assert whole.maturity.first_session == FIRST
    assert whole.maturity.first_priced == on(3)

    # Read as of a day before the priced ride, nothing priced is behind it:
    # its zeros are an absence of load, not a reading of none.
    before = fitness_trend(sessions, start=None, end=on(3), today=on(5))
    assert [point.ctl for point in before.points] == [0.0, 0.0, 0.0]
    assert before.maturity.first_priced is None


def test_no_history_has_a_maturity_with_nothing_to_count() -> None:
    trend = fitness_trend([], start=None, end=None, today=FIRST)

    assert trend.maturity.day is None
    assert trend.maturity.first_session is None
    assert trend.maturity.ctl.matures_on is None
    assert trend.maturity.ctl.matures_on_day == 97
