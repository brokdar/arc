"""The 1 Hz grid, the cleaner and the quarantine triggers.

The grid is the one WP-4 decision that cannot be revisited without re-ingesting
every original file (addenda A4.1), so the properties below are the contract
rather than a regression net: every column has the same length, a row is a
second, a pause is null and never zero, and a repair is recorded.

Hypothesis is used where the invariant is more useful than the enumeration —
"whatever the sampling, the frame spans the activity" holds for every file,
and the alternative is a table of the four sampling schemes we happen to have
thought of.
"""

import datetime as dt

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.domain.activity import QuarantineReason
from app.domain.streams import (
    GAP_THRESHOLD_S,
    MOVING_SPEED_MS,
    PLAUSIBLE_RANGE,
    AnomalyKind,
    ParsedActivity,
    RawSample,
    StreamChannel,
    StreamFrame,
    channels_present,
    clean,
    resample,
    validate,
)

START = dt.datetime(2026, 5, 4, 7, 30, tzinfo=dt.UTC)


def sample(offset_s: float, **values: float) -> RawSample:
    """A sample this many seconds after START, carrying these channels."""
    return RawSample(
        t=START + dt.timedelta(seconds=offset_s),
        values={StreamChannel(name): value for name, value in values.items()},
    )


def activity(samples: list[RawSample], **kwargs: object) -> ParsedActivity:
    """A parsed activity around these samples, with defaults for the rest."""
    return ParsedActivity(
        file_sport_index=0,
        sport=kwargs.pop("sport", "cycling"),  # type: ignore[arg-type]
        start_time=samples[0].t if samples else START,
        local_offset=None,
        samples=samples,
    )


# --- strategies ---------------------------------------------------------------


#: Sample offsets in whole seconds: a strictly increasing series starting at 0,
#: with spacings that span the sub-second-regular, the irregular and the
#: recording-stop cases.
@st.composite
def offsets(draw: st.DrawFn, *, min_samples: int = 2) -> list[float]:
    """Strictly increasing sample offsets, in seconds from the first sample."""
    gaps = draw(
        st.lists(
            st.integers(min_value=1, max_value=90),
            min_size=min_samples - 1,
            max_size=40,
        )
    )
    offsets_s = [0.0]
    for gap in gaps:
        offsets_s.append(offsets_s[-1] + gap)
    return offsets_s


@st.composite
def power_samples(draw: st.DrawFn) -> list[RawSample]:
    """Samples carrying a plausible power value each."""
    offsets_s = draw(offsets())
    watts = draw(
        st.lists(
            st.floats(min_value=0, max_value=2000),
            min_size=len(offsets_s),
            max_size=len(offsets_s),
        )
    )
    return [
        sample(offset, power=value)
        for offset, value in zip(offsets_s, watts, strict=True)
    ]


# --- resample: the grid contract (A4.1) ---------------------------------------


@given(power_samples())
@settings(max_examples=200)
def test_every_column_has_one_entry_per_row(samples: list[RawSample]) -> None:
    frame = resample(samples).frame

    assert frame.row_count == len(frame.device_t)
    for column in frame.columns.values():
        assert len(column) == frame.row_count


@given(power_samples())
@settings(max_examples=200)
def test_the_grid_spans_the_activity_one_row_per_second(
    samples: list[RawSample],
) -> None:
    result = resample(samples)

    # One row per elapsed second, both endpoints included: a 10 s recording is
    # 11 rows. Pinned here because "elapsed" and "elapsed + 1" are both
    # defensible and only one of them can be the storage contract.
    assert result.frame.row_count == int(result.elapsed_time_s) + 1
    assert result.frame.t0 == samples[0].t
    assert result.frame.instant(result.frame.row_count - 1) <= samples[-1].t


@given(power_samples())
@settings(max_examples=200)
def test_recording_time_is_elapsed_minus_the_long_gaps(
    samples: list[RawSample],
) -> None:
    result = resample(samples)

    long_gaps = sum(
        (later.t - earlier.t).total_seconds()
        for earlier, later in zip(samples, samples[1:], strict=False)
        if (later.t - earlier.t).total_seconds() > GAP_THRESHOLD_S
    )
    assert result.recording_time_s == pytest.approx(result.elapsed_time_s - long_gaps)
    assert result.recording_time_s <= result.elapsed_time_s


@given(power_samples())
@settings(max_examples=200)
def test_every_recording_stop_is_null_in_every_channel(
    samples: list[RawSample],
) -> None:
    result = resample(samples)

    for start, end in result.recording_stops:
        assert start < end
        for column in result.frame.columns.values():
            assert all(value is None for value in column[start:end]), (
                "a pause is a hole in the data, never zero watts"
            )
        assert all(stamp is None for stamp in result.frame.device_t[start:end])


@given(power_samples())
@settings(max_examples=200)
def test_every_sample_lands_on_the_row_of_its_own_second(
    samples: list[RawSample],
) -> None:
    frame = resample(samples).frame

    for one in samples:
        index = int((one.t - frame.t0).total_seconds())
        assert frame.device_t[index] == one.t
        assert (
            frame.columns[StreamChannel.POWER][index] == one.values[StreamChannel.POWER]
        )


def test_a_pause_leaves_a_hole_and_the_grid_continues() -> None:
    # A4.4's coffee stop: ten minutes off the bike between two five-minute
    # halves. Elapsed and recording time must differ by the stop.
    samples = [sample(second, power=200.0, speed=8.0) for second in range(0, 300)] + [
        sample(second, power=210.0, speed=8.0) for second in range(900, 1200)
    ]

    result = resample(samples)

    assert result.elapsed_time_s == 1199
    # The gap runs from the last sample before the stop to the first after it:
    # 601 s, so elapsed exceeds recording time by the length of the stop.
    assert result.recording_time_s == pytest.approx(1199 - 601)
    assert result.elapsed_time_s - result.recording_time_s == pytest.approx(600, abs=2)
    assert result.recording_stops == ((300, 900),)
    assert result.frame.row_count == 1200
    power = result.frame.columns[StreamChannel.POWER]
    assert power[299] == 200.0
    assert set(power[300:900]) == {None}
    assert power[900] == 210.0


def test_short_gaps_hold_rates_and_interpolate_positions() -> None:
    samples = [
        sample(0, power=200.0, elevation=100.0),
        sample(4, power=240.0, elevation=104.0),
    ]

    frame = resample(samples).frame

    # Power holds the previous reading — interpolating a rate invents a ramp
    # the athlete never rode.
    assert frame.columns[StreamChannel.POWER] == (200.0, 200.0, 200.0, 200.0, 240.0)
    # Elevation is a position: the intermediate values really were between.
    assert frame.columns[StreamChannel.ELEVATION] == (
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
    )
    assert frame.device_t[1] is None, "no device sample landed on this row"


def test_a_channel_with_its_own_long_gap_is_not_held_across_it() -> None:
    # Power every second, temperature once a minute: the gap rule is applied
    # per channel, so temperature stays null rather than being held a minute.
    samples = [sample(second, power=200.0) for second in range(0, 121)]
    samples[0] = sample(0, power=200.0, temp=18.0)
    samples[120] = sample(120, power=200.0, temp=22.0)

    frame = resample(samples).frame

    assert frame.columns[StreamChannel.TEMP][0] == 18.0
    assert frame.columns[StreamChannel.TEMP][60] is None
    assert frame.columns[StreamChannel.TEMP][120] == 22.0
    assert all(value == 200.0 for value in frame.columns[StreamChannel.POWER])


def test_a_stop_just_over_the_threshold_still_leaves_a_hole() -> None:
    # 30.5 s spans 30 rows, which the width test alone would let a channel
    # fill. The stop wins.
    samples = [sample(0, power=200.0), sample(30.5, power=210.0)]

    result = resample(samples)

    assert result.recording_stops == ((1, 30),)
    assert set(result.frame.columns[StreamChannel.POWER][1:30]) == {None}


def test_moving_time_counts_only_the_seconds_above_walking_pace() -> None:
    samples = [
        sample(0, speed=MOVING_SPEED_MS * 2),
        sample(10, speed=0.0),
        sample(20, speed=MOVING_SPEED_MS * 2),
        sample(30, speed=5.0),
    ]

    result = resample(samples)

    assert result.moving_time_s == pytest.approx(20.0)
    assert result.elapsed_time_s == 30.0


def test_median_time_delta_reports_the_sampling_regularity() -> None:
    samples = [sample(second, power=200.0) for second in (0, 4, 8, 12, 16)]

    assert resample(samples).median_time_delta_s == 4.0


def test_resampling_nothing_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="no samples"):
        resample([])


def test_a_frame_with_a_short_column_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="one entry per row"):
        StreamFrame(
            t0=START,
            device_t=(START, None, None),
            columns={StreamChannel.POWER: (1.0, 2.0)},
        )


def test_a_naive_timestamp_is_refused_at_the_sample() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RawSample(t=dt.datetime(2026, 5, 4, 7, 30), values={})  # noqa: DTZ001


def test_channels_present_is_the_union_over_samples() -> None:
    samples = [sample(0, power=200.0), sample(1, hr=140.0), sample(2, power=210.0)]

    assert channels_present(samples) == {StreamChannel.POWER, StreamChannel.HR}


# --- clean: repairs are recorded (A4.2) ---------------------------------------


def test_a_spike_survives_in_raw_and_is_clipped_in_fixed() -> None:
    samples = [sample(second, power=200.0) for second in range(60)]
    samples[30] = sample(30, power=3900.0)  # dropped magnet
    samples[31] = sample(31, power=3950.0)

    result = resample(samples)
    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    raw = result.frame.columns[StreamChannel.POWER]
    fixed = cleaned.fixed[StreamChannel.POWER]
    assert raw[30] == 3900.0, "the raw column keeps the spike"
    assert fixed[30] == 200.0
    assert fixed[31] == 200.0
    (anomaly,) = [
        one for one in cleaned.anomalies if one.kind is AnomalyKind.SPIKE_CLIPPED
    ]
    assert (anomaly.start_index, anomaly.end_index) == (30, 32)
    assert anomaly.channel is StreamChannel.POWER
    assert anomaly.substituted_value == 200.0


def test_a_dropout_is_held_and_an_elevation_gap_is_interpolated() -> None:
    samples = [sample(second, hr=150.0, elevation=100.0) for second in range(60)]
    for second in range(20, 30):
        samples[second] = sample(second, hr=0.0, elevation=-9999.0)
    samples[30] = sample(30, hr=150.0, elevation=110.0)

    result = resample(samples)
    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    held = next(
        one for one in cleaned.anomalies if one.kind is AnomalyKind.DROPOUT_HELD
    )
    assert (held.start_index, held.end_index) == (20, 30)
    assert held.channel is StreamChannel.HR
    assert cleaned.fixed[StreamChannel.HR][25] == 150.0

    gap = next(
        one for one in cleaned.anomalies if one.kind is AnomalyKind.GAP_INTERPOLATED
    )
    assert (gap.start_index, gap.end_index) == (20, 30)
    assert gap.channel is StreamChannel.ELEVATION
    assert gap.substituted_value is None
    # Ten rows between 100 and 110, one metre each.
    assert cleaned.fixed[StreamChannel.ELEVATION][20] == pytest.approx(
        100.909, abs=1e-3
    )
    assert cleaned.fixed[StreamChannel.ELEVATION][29] == pytest.approx(
        109.091, abs=1e-3
    )


def test_a_run_too_long_to_repair_is_nulled_and_said_so() -> None:
    samples = [sample(second, power=200.0) for second in range(120)]
    for second in range(30, 90):
        samples[second] = sample(second, power=9999.0)

    result = resample(samples)
    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    dropped = next(one for one in cleaned.anomalies if one.kind is AnomalyKind.DROPPED)
    assert (dropped.start_index, dropped.end_index) == (30, 90)
    assert set(cleaned.fixed[StreamChannel.POWER][30:90]) == {None}
    assert result.frame.columns[StreamChannel.POWER][60] == 9999.0


def test_an_untouched_channel_says_it_was_only_resampled() -> None:
    samples = [sample(second, power=200.0) for second in range(60)]

    result = resample(samples)
    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    (anomaly,) = cleaned.anomalies
    assert anomaly.kind is AnomalyKind.RESAMPLED_ONLY
    assert (anomaly.start_index, anomaly.end_index) == (0, 60)
    assert (
        cleaned.fixed[StreamChannel.POWER] == result.frame.columns[StreamChannel.POWER]
    )


def test_a_recording_stop_is_never_repaired() -> None:
    samples = [sample(second, power=200.0) for second in range(0, 60)] + [
        sample(second, power=210.0) for second in range(600, 660)
    ]

    result = resample(samples)
    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    start, end = result.recording_stops[0]
    assert set(cleaned.fixed[StreamChannel.POWER][start:end]) == {None}
    assert all(one.kind is AnomalyKind.RESAMPLED_ONLY for one in cleaned.anomalies), (
        "the stop is not a repair, and nothing else was wrong"
    )


@given(power_samples())
@settings(max_examples=100)
def test_cleaning_never_changes_a_column_length_or_the_raw_frame(
    samples: list[RawSample],
) -> None:
    result = resample(samples)
    before = dict(result.frame.columns)

    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    assert set(cleaned.fixed) == set(result.frame.columns)
    for channel, column in cleaned.fixed.items():
        assert len(column) == result.frame.row_count
        assert result.frame.columns[channel] == before[channel]


@given(power_samples())
@settings(max_examples=100)
def test_every_anomaly_addresses_a_real_half_open_range(
    samples: list[RawSample],
) -> None:
    result = resample(samples)

    cleaned = clean(result.frame, recording_stops=result.recording_stops)

    for anomaly in cleaned.anomalies:
        assert 0 <= anomaly.start_index < anomaly.end_index <= result.frame.row_count


# --- validate: what is refused rather than repaired (A-4) ---------------------


def test_an_activity_with_no_samples_is_quarantined() -> None:
    verdict = validate(activity([]))

    assert verdict is not None
    assert verdict.reason is QuarantineReason.NO_SAMPLES


def test_repeated_timestamps_are_quarantined_not_sorted_away() -> None:
    samples = [sample(second, power=200.0) for second in range(300)]
    samples.append(sample(100, power=250.0))

    verdict = validate(activity(samples))

    assert verdict is not None
    assert verdict.reason is QuarantineReason.NON_MONOTONIC_TIMESTAMPS
    assert "timestamp" in verdict.detail


def test_out_of_order_samples_alone_are_not_a_reason() -> None:
    samples = [sample(second, power=200.0) for second in range(300)]
    samples.reverse()

    assert validate(activity(samples)) is None


def test_a_too_short_activity_is_quarantined() -> None:
    samples = [sample(second, power=200.0) for second in range(60)]

    verdict = validate(activity(samples))

    assert verdict is not None
    assert verdict.reason is QuarantineReason.TOO_SHORT
    assert "59" in verdict.detail


def test_a_systemically_broken_channel_is_quarantined_not_cleaned() -> None:
    _low, high = PLAUSIBLE_RANGE[StreamChannel.HR]
    samples = [sample(second, hr=150.0) for second in range(300)]
    for second in range(0, 300, 3):  # a third of them
        samples[second] = sample(second, hr=high + 100)

    verdict = validate(activity(samples))

    assert verdict is not None
    assert verdict.reason is QuarantineReason.IMPLAUSIBLE_CHANNEL
    assert "hr" in verdict.detail


def test_a_few_spikes_are_a_repair_not_a_quarantine() -> None:
    samples = [sample(second, power=200.0) for second in range(300)]
    samples[100] = sample(100, power=9999.0)
    samples[101] = sample(101, power=9999.0)

    assert validate(activity(samples)) is None


@given(offsets(min_samples=3))
@settings(max_examples=100)
def test_a_long_enough_plausible_activity_is_never_refused(
    offsets_s: list[float],
) -> None:
    assume(offsets_s[-1] >= 120)
    samples = [sample(offset, power=200.0, hr=150.0) for offset in offsets_s]

    assert validate(activity(samples)) is None
