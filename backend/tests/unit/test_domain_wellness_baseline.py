"""The baseline maths: maturity, bands, deviations and the joint state.

Pure-domain tests, because everything under test here is arithmetic over a
sequence of days with no I/O in it. The HTTP and MCP shapes of the same numbers
are `test_wellness_api.py` and `test_mcp_wellness.py`; what lives here is the
statistics, the exclusion rules and the abstention.

Every fixture is built from an explicit list of readings and every expected
number is either exact or hand-computed and pasted in with its arithmetic
stated, so these tests fail when the implementation changes rather than
agreeing with it.
"""

import datetime as dt
import math
from typing import Any

import pytest

from app.domain.wellness import (
    OBJECTIVE_FIELDS,
    SUBJECTIVE_FIELDS,
    WELLNESS_LATE_ENTRY_DAYS,
    Confounder,
    HrvContext,
    HrvMetric,
    WellnessDay,
    is_late_entry,
)
from app.domain.wellness_baseline import (
    MARKERS,
    MARKERS_BY_METRIC,
    MIN_BASELINE_READINGS,
    MIN_BASELINE_SPAN_DAYS,
    READINESS_MARKERS,
    Abstention,
    Baseline,
    DaySample,
    Direction,
    JointStateKey,
    MetricTrend,
    Space,
    readiness,
    trend_for,
    unmarked_fields,
)

#: A fixed "today". The domain is pure, so the clock is an argument.
TODAY = dt.date(2026, 8, 14)


def at(offset: int) -> dt.date:
    """The date ``offset`` days before :data:`TODAY`."""
    return TODAY - dt.timedelta(days=offset)


def sample(offset: int, *, recalled: bool = False, **fields: Any) -> DaySample:
    """One day of the series, ``offset`` days ago."""
    return DaySample(
        day=WellnessDay(local_date=at(offset), **fields), subjective_recalled=recalled
    )


def hrv(
    offset: int,
    value: float,
    *,
    context: HrvContext = HrvContext.SLEEPING,
    metric: HrvMetric = HrvMetric.RMSSD,
    **fields: Any,
) -> DaySample:
    """One day carrying an HRV reading, with its statistic and context."""
    return sample(
        offset, hrv_ms=value, hrv_metric=metric, hrv_context=context, **fields
    )


def trend(
    metric: str,
    days: list[DaySample],
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> MetricTrend:
    """The trend for one metric over the days given, as of :data:`TODAY`."""
    return trend_for(
        MARKERS_BY_METRIC[metric],
        days,
        start=start or at(59),
        end=end or (TODAY + dt.timedelta(days=1)),
        on=TODAY,
    )


def mature(value: Baseline | Abstention) -> Baseline:
    """Narrow a baseline that the test expects to be mature."""
    assert isinstance(value, Baseline), f"expected a baseline, got {value}"
    return value


def abstains(value: Baseline | Abstention) -> Abstention:
    """Narrow a baseline that the test expects to abstain."""
    assert isinstance(value, Abstention), f"expected an abstention, got {value}"
    return value


# --- AC-9: maturity is two thresholds, and abstention names both --------------


def stable_hrv(
    count: int, *, every: int = 2, last: int | None = None
) -> list[DaySample]:
    """``count`` sleeping-RMSSD readings, one every ``every`` days.

    ``last`` forces the oldest reading's offset, which is how a fixture buys a
    span without buying more readings.
    """
    offsets = [index * every for index in range(count)]
    if last is not None:
        offsets[-1] = last
    return [hrv(offset, 55.0 + (offset % 5)) for offset in offsets]


def test_a_thin_hrv_series_abstains_and_names_both_counts() -> None:
    # Eleven readings over twenty-two days: short on both thresholds, and the
    # abstention has to say so on both — a coach told "not enough data" cannot
    # tell whether to wait three days or three weeks.
    days = [hrv(offset, 55.0) for offset in range(0, 22, 2)][:11]

    answer = abstains(trend("hrv_rmssd_ms", days).baseline)

    assert answer.readings.have == 11
    assert answer.readings.need == MIN_BASELINE_READINGS == 14
    assert answer.span_days.have == 21
    assert answer.span_days.need == MIN_BASELINE_SPAN_DAYS == 28
    assert "11 of 14" in answer.reason
    assert "21 of 28" in answer.reason


def test_an_abstention_carries_no_mean_no_band_and_no_deviation() -> None:
    answer = abstains(trend("hrv_rmssd_ms", stable_hrv(11)).baseline)

    # Absent from the object, not present-and-null: a null mean is a number
    # somebody will one day read as zero.
    for absent in ("mean", "band", "deviation_sd", "sd", "cv"):
        assert not hasattr(answer, absent), f"an abstention must not carry {absent}"


def test_fourteen_readings_over_exactly_twenty_eight_days_are_mature() -> None:
    # The inclusive boundary: first and last reading 27 days apart is a span of
    # 28 days, and 14 readings is exactly the bar.
    days = stable_hrv(14, last=27)
    assert len({entry.day.local_date for entry in days}) == 14

    answer = mature(trend("hrv_rmssd_ms", days).baseline)

    assert answer.n == 14
    assert answer.span_days == 28
    assert answer.mean > 0


def test_fourteen_readings_over_twenty_seven_days_still_abstain() -> None:
    # Count met, span not. A fortnight of readings crammed into under four
    # weeks describes a fortnight, whatever its n says.
    days = stable_hrv(14, last=26)

    answer = abstains(trend("hrv_rmssd_ms", days).baseline)

    assert answer.readings.have == 14
    assert answer.span_days.have == 27


def test_readings_crammed_into_ten_days_abstain_on_span_alone() -> None:
    # The plan's edge says "20 readings spanning 10 days". One row per
    # athlete-local date makes twenty readings in ten days unrepresentable, so
    # the edge is stated the only way it can be: every date in the span
    # answered, and the span still far short of four weeks.
    days = [hrv(offset, 55.0 + offset % 3) for offset in range(10)]

    answer = abstains(trend("hrv_rmssd_ms", days).baseline)

    assert answer.readings.have == 10
    assert answer.span_days.have == 10
    assert answer.span_days.have < MIN_BASELINE_SPAN_DAYS


def test_zero_readings_abstain_without_raising() -> None:
    answer = abstains(trend("hrv_rmssd_ms", []).baseline)

    assert answer.readings.have == 0
    assert answer.span_days.have == 0
    assert "0 of 14" in answer.reason


def test_crossing_both_thresholds_flips_the_same_call_to_a_baseline() -> None:
    thin = stable_hrv(13, last=27)
    assert isinstance(trend("hrv_rmssd_ms", thin).baseline, Abstention)

    thick = stable_hrv(14, last=27)
    answer = mature(trend("hrv_rmssd_ms", thick).baseline)

    assert answer.band is not None
    assert answer.deviation_sd is not None


# --- AC-10: today and the seven-day mean are different things -----------------


def test_the_deviation_is_the_seven_day_mean_against_the_baseline() -> None:
    # Sixty consecutive days of resting HR, so both windows are full.
    days = [sample(offset, resting_hr_bpm=48 + (offset % 3)) for offset in range(60)]

    answer = trend("resting_hr_bpm", days)
    baseline = mature(answer.baseline)
    rolling = answer.rolling_mean_7d.mean
    assert rolling is not None

    assert baseline.deviation_sd == pytest.approx(
        (rolling - baseline.mean) / baseline.sd
    )
    assert answer.today == 48
    assert answer.today != answer.rolling_mean_7d.mean


def test_a_single_day_spike_moves_the_deviation_by_at_most_a_seventh() -> None:
    # The over-claim this whole feature exists to prevent: one bad night read
    # as a trend. A 3 SD spike today may move a seven-day mean by 3/7 of an SD
    # and no more.
    # A perfectly flat series has an SD of zero, so the fixture carries real
    # day-to-day variation and the spike is measured in that series' own SD.
    varied = [
        sample(offset, resting_hr_bpm=50 + (1 if offset % 2 else -1))
        for offset in range(1, 60)
    ]
    quiet = trend("resting_hr_bpm", [sample(0, resting_hr_bpm=50), *varied])
    sd = mature(quiet.baseline).sd

    spiked = trend(
        "resting_hr_bpm",
        [sample(0, resting_hr_bpm=round(50 + 3 * sd)), *varied],
    )

    before = mature(quiet.baseline).deviation_sd
    after = mature(spiked.baseline).deviation_sd
    assert before is not None
    assert after is not None
    moved = abs(after - before)
    assert moved <= 3 / 7 + 1e-9
    assert moved > 0, "the spike does move the seven-day mean, just not by 3 SD"


def test_one_reading_in_the_seven_day_window_reports_n_of_one() -> None:
    days = [sample(offset, resting_hr_bpm=50) for offset in range(8, 60)]
    days.append(sample(3, resting_hr_bpm=61))

    answer = trend("resting_hr_bpm", days)

    assert answer.rolling_mean_7d.n == 1
    assert answer.rolling_mean_7d.mean == 61
    assert answer.today is None


def test_today_absent_still_returns_the_seven_day_mean() -> None:
    days = [sample(offset, resting_hr_bpm=50 + offset % 4) for offset in range(1, 60)]

    answer = trend("resting_hr_bpm", days)

    assert answer.today is None
    assert answer.rolling_mean_7d.n == 6
    assert answer.rolling_mean_7d.mean is not None


# --- AC-11: subjective maturity and HRV maturity are separate objects ---------


def test_a_mature_subjective_series_stands_beside_an_hrv_abstention() -> None:
    days = [sample(offset, motivation=3 + offset % 3) for offset in range(40)]
    for index, entry in enumerate(days[:9]):
        days[index] = DaySample(
            day=WellnessDay(
                local_date=entry.day.local_date,
                motivation=entry.day.motivation,
                hrv_ms=55.0,
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
            ),
            subjective_recalled=False,
        )

    motivation = mature(trend("motivation", days).baseline)
    hrv_answer = abstains(trend("hrv_rmssd_ms", days).baseline)

    assert motivation.metric == "motivation"
    assert motivation.mean == pytest.approx(
        sum(3 + offset % 3 for offset in range(40)) / 40
    )
    assert hrv_answer.readings.have == 9


def test_the_reverse_case_matures_hrv_and_abstains_on_subjective() -> None:
    days = [hrv(offset, 55.0 + offset % 4) for offset in range(40)]
    days[:9] = [
        DaySample(
            day=WellnessDay(
                local_date=at(offset),
                hrv_ms=55.0 + offset % 4,
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
                motivation=4,
            )
        )
        for offset in range(9)
    ]

    assert isinstance(trend("hrv_rmssd_ms", days).baseline, Baseline)
    assert abstains(trend("motivation", days).baseline).readings.have == 9


def test_both_immature_yields_two_abstentions_and_no_partial_number() -> None:
    days = [hrv(offset, 55.0, motivation=4) for offset in range(5)]

    hrv_answer = abstains(trend("hrv_rmssd_ms", days).baseline)
    motivation = abstains(trend("motivation", days).baseline)

    for answer in (hrv_answer, motivation):
        assert not hasattr(answer, "mean")
        assert answer.readings.have == 5


# --- AC-46: one baseline per HRV context, never a pooled mean ----------------


def test_an_hrv_write_with_no_context_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="hrv_context"):
        WellnessDay(local_date=TODAY, hrv_ms=58.0, hrv_metric=HrvMetric.RMSSD)


def test_two_contexts_two_baselines() -> None:
    # The AFib-History trap: turning on a setting in an unrelated app changes
    # how often daytime spot samples are taken. Pooled with the overnight mean,
    # the baseline moves under the athlete for a reason that is not about them.
    sleeping = [hrv(offset, 55.0 + offset % 4) for offset in range(0, 60, 2)]
    waking = [
        hrv(offset, 32.0 + offset % 3, context=HrvContext.WAKING_SPOT)
        for offset in range(1, 60, 2)
    ]

    answer = trend("hrv_rmssd_ms", [*sleeping, *waking])

    assert set(answer.by_context) == {HrvContext.SLEEPING, HrvContext.WAKING_SPOT}
    overnight = mature(answer.by_context[HrvContext.SLEEPING])
    spot = mature(answer.by_context[HrvContext.WAKING_SPOT])
    assert overnight.mean != spot.mean
    # Each names the context it reports on, so a reader never has to infer it.
    assert overnight.hrv_context is HrvContext.SLEEPING
    assert spot.hrv_context is HrvContext.WAKING_SPOT
    # And the headline baseline is the preferred context, named.
    assert mature(answer.baseline).hrv_context is HrvContext.SLEEPING


def test_only_waking_spot_readings_gives_no_sleeping_key() -> None:
    waking = [
        hrv(offset, 32.0 + offset % 3, context=HrvContext.WAKING_SPOT)
        for offset in range(0, 60, 2)
    ]

    answer = trend("hrv_rmssd_ms", waking)

    assert set(answer.by_context) == {HrvContext.WAKING_SPOT}
    assert HrvContext.SLEEPING not in answer.by_context
    assert mature(answer.by_context[HrvContext.WAKING_SPOT]).n == 30


def test_one_context_mature_the_other_not() -> None:
    sleeping = [hrv(offset, 55.0 + offset % 4) for offset in range(0, 60, 2)]
    waking = [
        hrv(offset, 32.0, context=HrvContext.WAKING_SPOT) for offset in range(1, 8, 2)
    ]

    answer = trend("hrv_rmssd_ms", [*sleeping, *waking])

    assert isinstance(answer.by_context[HrvContext.SLEEPING], Baseline)
    thin = abstains(answer.by_context[HrvContext.WAKING_SPOT])
    assert thin.hrv_context is HrvContext.WAKING_SPOT
    assert thin.readings.have == 4


# --- AC-47: the band is 0.5 x CV of ln(RMSSD) --------------------------------


#: Fourteen RMSSD readings over a 28-day span, and the statistics of their
#: natural logs computed **outside** this codebase (Python's `statistics` over
#: `math.log`, sample SD with n-1). The band asserted below is
#: `0.5 x CV x mean` of those logs — the smallest-worthwhile-change band the
#: HRV-monitoring literature uses (Plews et al., 2013), not an invented +-1 SD.
SWC_VALUES = [
    52.0,
    58.0,
    61.0,
    47.0,
    55.0,
    63.0,
    49.0,
    57.0,
    60.0,
    51.0,
    54.0,
    62.0,
    56.0,
    59.0,
]
SWC_MEAN_LN = 4.021658959555504
SWC_SD_LN = 0.08978488500895009
SWC_CV = 0.022325335368286327
SWC_HALF_WIDTH = 0.04489244250447504


def swc_days() -> list[DaySample]:
    """The hand-computed fixture, one reading every other day plus a 28th."""
    offsets = [index * 2 for index in range(14)]
    offsets[-1] = 27
    return [
        hrv(offset, value) for offset, value in zip(offsets, SWC_VALUES, strict=True)
    ]


def test_swc_against_hand_computed_fixture() -> None:
    answer = mature(trend("hrv_rmssd_ms", swc_days()).baseline)

    assert answer.space is Space.LN
    assert answer.n == 14
    assert answer.mean == pytest.approx(SWC_MEAN_LN)
    assert answer.sd == pytest.approx(SWC_SD_LN)
    assert answer.cv == pytest.approx(SWC_CV)
    assert answer.band is not None
    assert answer.band.half_width == pytest.approx(SWC_HALF_WIDTH)
    assert answer.band.low == pytest.approx(SWC_MEAN_LN - SWC_HALF_WIDTH)
    assert answer.band.high == pytest.approx(SWC_MEAN_LN + SWC_HALF_WIDTH)
    # And the same band in milliseconds, which is what a chart draws.
    assert answer.band.low_native == pytest.approx(
        math.exp(SWC_MEAN_LN - SWC_HALF_WIDTH)
    )
    assert answer.mean_native == pytest.approx(math.exp(SWC_MEAN_LN))


def test_an_identical_series_has_a_zero_width_band_and_divides_by_nothing() -> None:
    offsets = [index * 2 for index in range(14)]
    offsets[-1] = 27
    days = [hrv(offset, 55.0) for offset in offsets]

    answer = mature(trend("hrv_rmssd_ms", days).baseline)

    assert answer.sd == 0.0
    assert answer.cv == 0.0
    assert answer.band is not None
    assert answer.band.half_width == 0.0
    assert answer.band.low == answer.band.high
    # Every reading is the baseline, so the deviation is exactly zero rather
    # than a division that raised.
    assert answer.deviation_sd == 0.0
    assert answer.direction is Direction.WITHIN


def test_a_single_reading_abstains_rather_than_reporting_a_cv_of_zero() -> None:
    answer = abstains(trend("hrv_rmssd_ms", [hrv(0, 55.0)]).baseline)

    assert answer.readings.have == 1
    assert not hasattr(answer, "cv")


# --- AC-37: an invalidated day is not evidence, and its absence is visible ----


def clean_resting(count: int = 40) -> list[DaySample]:
    """A clean resting-HR series: 40 consecutive days alternating 49/51."""
    return [
        sample(offset, resting_hr_bpm=49 + (offset % 2) * 2) for offset in range(count)
    ]


def test_an_alcohol_day_changes_neither_the_mean_nor_the_n() -> None:
    clean = clean_resting()
    before = mature(trend("resting_hr_bpm", clean).baseline)

    two_sd_up = round(before.mean + 2 * before.sd)
    with_alcohol = [
        sample(45, resting_hr_bpm=two_sd_up, confounders=(Confounder.ALCOHOL,)),
        *clean,
    ]
    after = mature(trend("resting_hr_bpm", with_alcohol).baseline)

    assert after.n == before.n
    assert after.mean == pytest.approx(before.mean)


def test_a_confounder_that_does_not_invalidate_leaves_the_day_in() -> None:
    clean = clean_resting()
    before = mature(trend("resting_hr_bpm", clean).baseline)

    with_travel = [
        sample(45, resting_hr_bpm=60, confounders=(Confounder.TRAVEL,)),
        *clean,
    ]
    after = mature(trend("resting_hr_bpm", with_travel).baseline)

    assert after.n == before.n + 1


def test_every_day_invalidated_abstains_rather_than_averaging_zero_days() -> None:
    days = [
        sample(offset, resting_hr_bpm=50, confounders=(Confounder.HOT_ROOM,))
        for offset in range(40)
    ]

    answer = abstains(trend("resting_hr_bpm", days).baseline)

    assert answer.readings.have == 0
    assert not hasattr(answer, "mean")


def test_an_invalidated_day_still_appears_in_the_series_with_its_values() -> None:
    days = [
        sample(
            1,
            resting_hr_bpm=61,
            confounders=(Confounder.ALCOHOL, Confounder.SHORT_SLEEP),
        )
    ]

    answer = trend("resting_hr_bpm", days)
    point = next(item for item in answer.series if item.local_date == at(1))

    assert point.value == 61
    assert point.standing is not None
    assert point.standing.actionable is False
    assert point.standing.statement == "recorded, not actionable: alcohol, short_sleep"


# --- AC-38: the per-marker table, and its completeness -----------------------


BANDED_MARKERS = (
    "resting_hr_bpm",
    "hrv_rmssd_ms",
    "respiratory_rate_brpm",
    "wrist_temperature_delta_c",
    "spo2",
)


def marker_days(metric: str) -> list[DaySample]:
    """Forty consecutive days carrying one marker, with real variation."""
    marker = MARKERS_BY_METRIC[metric]
    if marker.hrv_metric is not None:
        return [hrv(offset, 55.0 + (offset % 5)) for offset in range(40)]
    base, delta = {
        "resting_hr_bpm": (49.0, 1.0),
        "respiratory_rate_brpm": (13.0, 0.2),
        "wrist_temperature_delta_c": (-0.2, 0.1),
        "spo2": (0.96, 0.002),
        "weight_kg": (78.0, 0.1),
        "sleep_duration_s": (25_000.0, 300.0),
    }[metric]
    whole = metric in {"resting_hr_bpm", "sleep_duration_s"}
    days: list[DaySample] = []
    for offset in range(40):
        value = base + delta * (offset % 4)
        fields: dict[str, Any] = {marker.field: int(value) if whole else value}
        days.append(DaySample(day=WellnessDay(local_date=at(offset), **fields)))
    return days


@pytest.mark.parametrize("metric", BANDED_MARKERS)
def test_every_banded_marker_reports_a_band_and_a_directed_deviation(
    metric: str,
) -> None:
    answer = trend(metric, marker_days(metric))
    baseline = mature(answer.baseline)

    assert baseline.n == 40
    assert baseline.band is not None
    assert baseline.deviation_sd is not None
    assert baseline.direction in set(Direction)
    assert answer.rolling_mean_7d.n == 7


def test_weight_reports_a_mean_and_a_trend_and_no_band() -> None:
    baseline = mature(trend("weight_kg", marker_days("weight_kg")).baseline)

    # Body weight moves on a scale of weeks, and a daily SD deviation from it
    # is a statement nobody should make. The trend is the honest statistic.
    assert baseline.mean == pytest.approx(78.15)
    assert baseline.trend is not None
    assert baseline.band is None
    assert baseline.deviation_sd is None
    assert baseline.direction is None


def test_a_marker_with_no_readings_abstains_rather_than_reporting_a_null_mean() -> None:
    answer = trend("spo2", clean_resting())

    empty = abstains(answer.baseline)
    assert empty.readings.have == 0
    assert not hasattr(empty, "mean")
    assert answer.rolling_mean_7d.mean is None
    assert answer.rolling_mean_7d.n == 0


def test_baseline_marker_completeness() -> None:
    # Every scalar the model stores has an entry in the per-marker table, so a
    # column added without one is a failing test rather than a metric that
    # silently never appears on any read.
    assert unmarked_fields((*OBJECTIVE_FIELDS, *SUBJECTIVE_FIELDS)) == ()


def test_the_completeness_test_notices_an_unmarked_field() -> None:
    # The check itself, checked: a completeness test that cannot fail proves
    # nothing about the table it guards.
    assert unmarked_fields((*OBJECTIVE_FIELDS, "grip_strength_kg")) == (
        "grip_strength_kg",
    )


def test_the_marker_table_has_no_duplicate_metric_names() -> None:
    names = [marker.metric for marker in MARKERS]
    assert len(names) == len(set(names))


# --- AC-31: backfill counts for the watch, not for the memory ----------------


def forty_days_backfilled() -> list[DaySample]:
    """Forty complete days imported in one batch, every one of them recalled."""
    return [
        DaySample(
            day=WellnessDay(
                local_date=at(offset),
                hrv_ms=55.0 + offset % 5,
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
                resting_hr_bpm=49 + offset % 3,
                motivation=3 + offset % 3,
            ),
            subjective_recalled=True,
        )
        for offset in range(40)
    ]


def test_a_batch_import_matures_hrv_and_resting_hr_at_full_weight() -> None:
    days = forty_days_backfilled()

    assert mature(trend("hrv_rmssd_ms", days).baseline).n == 40
    assert mature(trend("resting_hr_bpm", days).baseline).n == 40


def test_the_same_batch_does_not_mature_a_subjective_baseline() -> None:
    # Nobody accurately recalls last month's Tuesday motivation, and a baseline
    # matured out of guesses is worse than a shorter honest one.
    answer = abstains(trend("motivation", forty_days_backfilled()).baseline)

    assert answer.readings.have == 0


def test_a_day_both_recalled_and_invalidated_is_excluded_once() -> None:
    clean = clean_resting()
    before = mature(trend("resting_hr_bpm", clean).baseline)

    doubly = [
        DaySample(
            day=WellnessDay(
                local_date=at(45),
                resting_hr_bpm=70,
                motivation=1,
                confounders=(Confounder.ALCOHOL,),
            ),
            subjective_recalled=True,
        ),
        *clean,
    ]
    after = mature(trend("resting_hr_bpm", doubly).baseline)

    assert after.n == before.n
    # And the subjective half of the same day is excluded once, not twice.
    assert abstains(trend("motivation", doubly).baseline).readings.have == 0


def test_a_day_entered_exactly_at_the_late_entry_boundary_still_counts() -> None:
    # `is_late_entry` is `> WELLNESS_LATE_ENTRY_DAYS`, so a day entered exactly
    # two days after the morning it describes is a report, not a recollection.
    # The flag is derived here through the real predicate rather than typed in,
    # which is what makes this the boundary rather than a restatement of it.
    entered = dt.datetime(2026, 8, 14, 8, 0, tzinfo=dt.UTC)
    days = [
        DaySample(
            day=WellnessDay(local_date=at(offset), motivation=3 + offset % 3),
            subjective_recalled=is_late_entry(at(offset), entered, "UTC"),
        )
        for offset in range(40)
    ]
    on_the_boundary = days[WELLNESS_LATE_ENTRY_DAYS]
    assert on_the_boundary.subjective_recalled is False
    assert days[WELLNESS_LATE_ENTRY_DAYS + 1].subjective_recalled is True

    # Three days survive the recall gate — the boundary one among them — and a
    # baseline over three abstains rather than pretending.
    answer = abstains(trend("motivation", days).baseline)
    assert answer.readings.have == WELLNESS_LATE_ENTRY_DAYS + 1


# --- AC-49: HRV x resting HR is a named quadrant, and never a verdict --------


def quadrant_days(*, hrv_low: bool, rhr_low: bool) -> list[DaySample]:
    """Sixty days whose last week pushes each marker to one side of its mean."""
    settled = [
        DaySample(
            day=WellnessDay(
                local_date=at(offset),
                hrv_ms=55.0 + (offset % 4),
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
                resting_hr_bpm=50 + (offset % 4),
            )
        )
        for offset in range(7, 60)
    ]
    recent = [
        DaySample(
            day=WellnessDay(
                local_date=at(offset),
                hrv_ms=40.0 if hrv_low else 75.0,
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
                resting_hr_bpm=44 if rhr_low else 60,
            )
        )
        for offset in range(7)
    ]
    return [*settled, *recent]


def quadrant_for(*, hrv_low: bool, rhr_low: bool) -> Any:
    days = quadrant_days(hrv_low=hrv_low, rhr_low=rhr_low)
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(59), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }
    return readiness(trends, on=TODAY).joint_state


def test_the_hrv_low_resting_hr_high_quadrant_is_named() -> None:
    state = quadrant_for(hrv_low=True, rhr_low=False)

    assert state is not None
    assert state.key is JointStateKey.HRV_LOW_RHR_HIGH
    assert state.label == "HRV below baseline, resting HR above baseline"


def test_the_hrv_low_resting_hr_low_quadrant_carries_a_distinct_label() -> None:
    # The case that makes a single "HRV is down" reading useless on its own:
    # parasympathetic saturation and the alcohol artefact both live here.
    low_low = quadrant_for(hrv_low=True, rhr_low=True)
    low_high = quadrant_for(hrv_low=True, rhr_low=False)

    assert low_low is not None
    assert low_high is not None
    assert low_low.key is JointStateKey.HRV_LOW_RHR_LOW
    assert low_low.label != low_high.label


def test_the_hrv_high_resting_hr_low_quadrant_is_named() -> None:
    state = quadrant_for(hrv_low=False, rhr_low=True)

    assert state is not None
    assert state.key is JointStateKey.HRV_HIGH_RHR_LOW


def test_the_hrv_high_resting_hr_high_quadrant_is_named() -> None:
    state = quadrant_for(hrv_low=False, rhr_low=False)

    assert state is not None
    assert state.key is JointStateKey.HRV_HIGH_RHR_HIGH


def test_no_quadrant_at_all_when_hrv_is_absent() -> None:
    days = [sample(offset, resting_hr_bpm=49 + offset % 3) for offset in range(60)]
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(59), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }

    assert readiness(trends, on=TODAY).joint_state is None


def test_no_quadrant_when_one_of_the_two_baselines_is_immature() -> None:
    # A quadrant drawn over an unmatured marker is a verdict wearing a label.
    days = [sample(offset, resting_hr_bpm=49 + offset % 3) for offset in range(60)]
    days += [hrv(offset, 40.0 + offset % 3) for offset in range(60, 66)]
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(65), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }

    assert readiness(trends, on=TODAY).joint_state is None


# --- readiness counts, never scores ------------------------------------------


def test_readiness_counts_the_markers_outside_their_band_with_directions() -> None:
    days = quadrant_days(hrv_low=True, rhr_low=False)
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(59), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }

    outside = readiness(trends, on=TODAY).markers_outside_band

    assert outside.count == 2
    assert outside.of == 2, "only the two matured markers are in the denominator"
    assert {marker.metric for marker in outside.markers} == {
        "hrv_rmssd_ms",
        "resting_hr_bpm",
    }
    directions = {marker.metric.value: marker.direction for marker in outside.markers}
    assert directions["hrv_rmssd_ms"] is Direction.BELOW
    assert directions["resting_hr_bpm"] is Direction.ABOVE
    assert str(outside) == "2 of 2"


def test_the_denominator_excludes_an_immature_marker() -> None:
    days = quadrant_days(hrv_low=True, rhr_low=False)
    # Respiratory rate is present but thin: four readings, so it is neither
    # counted as outside nor counted in the denominator.
    days += [
        sample(offset, respiratory_rate_brpm=13.0 + offset % 2)
        for offset in (61, 62, 63, 64)
    ]
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(64), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }

    outside = readiness(trends, on=TODAY).markers_outside_band

    assert outside.of == 2
    assert outside.count == 2
    assert READINESS_MARKERS == BANDED_MARKERS


def test_zero_markers_outside_still_returns_the_projection() -> None:
    days = [
        DaySample(
            day=WellnessDay(
                local_date=at(offset),
                resting_hr_bpm=50 + offset % 3,
                hrv_ms=55.0 + offset % 3,
                hrv_metric=HrvMetric.RMSSD,
                hrv_context=HrvContext.SLEEPING,
            )
        )
        for offset in range(60)
    ]
    trends = {
        marker.metric: trend_for(
            marker, days, start=at(59), end=TODAY + dt.timedelta(days=1), on=TODAY
        )
        for marker in MARKERS
    }

    outside = readiness(trends, on=TODAY).markers_outside_band

    assert outside.count == 0
    assert outside.of == 2
    assert outside.markers == ()
