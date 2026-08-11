"""The committed golden FIT files, and what parsing them says.

Two guarantees, and both matter for a fixture that is a binary blob:

1. **The committed bytes are what the generator produces.** A `fit-tool`
   upgrade that changes the encoding fails here rather than silently leaving
   four files nobody can regenerate.
2. **Parsing them says what it said yesterday.** The snapshots below are the
   whole contract between `app.ingest.parsers` and everything downstream:
   sample counts, channels, the A4.4 durations, the A4.3 sources, and the
   discipline each file classifies to.

Real-file parse tests are **operator-pending** — no real exports exist yet.
When they do, add them beside these; nothing here is skipped in the meantime.
"""

from pathlib import Path

import pytest

from app.domain.activity import (
    ClassificationSource,
    SessionDiscipline,
    classify_discipline,
    timezone_label,
)
from app.domain.metrics import Measured, distance_km
from app.domain.streams import (
    ParsedActivity,
    StreamChannel,
    channels_present,
    clean,
    resample,
    validate,
)
from app.ingest.parsers import parse
from app.ingest.parsers.base import NO_DEVICE_INFO
from tests.unit.golden_fit import (
    BUILDERS,
    OUTDOOR_SPIKE_AT_S,
    OUTDOOR_SPIKE_S,
    OUTDOOR_SPIKE_W,
    OUTDOOR_STOP_S,
    build_all,
    golden,
)


def summarise(activity: ParsedActivity) -> dict[str, object]:
    """Everything a parsed activity claims, flattened for one assertion."""
    resampled = resample(activity.samples)
    channels = channels_present(activity.samples)
    discipline, source = classify_discipline(
        sport=activity.sport,
        has_power=StreamChannel.POWER in channels,
        has_speed=StreamChannel.SPEED in channels,
        has_gps=StreamChannel.LAT in channels,
        duration_s=resampled.elapsed_time_s,
    )
    return {
        "file_sport_index": activity.file_sport_index,
        "sport": activity.sport,
        "start_time": activity.start_time.isoformat(),
        "timezone": timezone_label(activity.local_offset),
        "samples": len(activity.samples),
        "channels": sorted(channel.value for channel in channels),
        "laps": len(activity.laps),
        "elapsed_time_s": resampled.elapsed_time_s,
        "recording_time_s": resampled.recording_time_s,
        "recording_stops": [list(stop) for stop in resampled.recording_stops],
        "median_time_delta_s": resampled.median_time_delta_s,
        "rows": resampled.frame.row_count,
        "power_source_candidates": list(activity.power_source_candidates),
        "power_source": activity.power_source,
        "power_source_rule": activity.power_source_rule,
        "hr_source_candidates": list(activity.hr_source_candidates),
        "hr_source": activity.hr_source,
        "hr_source_rule": activity.hr_source_rule,
        "distance_source": activity.distance_source,
        "discipline": discipline.value,
        "classification_source": source.value,
    }


OUTDOOR_RIDE = {
    "file_sport_index": 0,
    "sport": "cycling",
    "start_time": "2026-05-04T07:30:00+00:00",
    "timezone": "UTC+02:00",
    "samples": 902,
    "channels": [
        "cadence",
        # The head unit's own cumulative odometer (D197) — deliberately ahead
        # of what integrating this file's speed column gives, so a test can
        # tell which one the distance metric read.
        "distance",
        "elevation",
        "hr",
        "lat",
        "lon",
        "power",
        "speed",
        "temp",
    ],
    "laps": 2,
    # Elapsed minus the stop's 599 rows (D101): the samples flanking the
    # 600 s gap were themselves recorded, so the stop is one row narrower
    # than the gap and recording time is one second longer than 1800.
    "elapsed_time_s": 2400.0,
    "recording_time_s": 1801.0,
    "recording_stops": [[601, 1200]],
    "median_time_delta_s": 1.0,
    "rows": 2401,
    "power_source_candidates": ["srm/7 #1"],
    "power_source": "srm/7 #1",
    "power_source_rule": "only candidate",
    "hr_source_candidates": ["garmin/1234 #2"],
    "hr_source": "garmin/1234 #2",
    "hr_source_rule": "only candidate",
    "distance_source": "record.distance",
    "discipline": "cycling",
    "classification_source": "sport_field",
}

INDOOR_TRAINER = {
    "file_sport_index": 0,
    "sport": "cycling",
    "start_time": "2026-05-06T18:00:00+00:00",
    # The device wrote no local offset, so `UTC` is what the file actually
    # says — not a guess about where the athlete was (§0 decision 5).
    "timezone": "UTC",
    "samples": 3601,
    "channels": ["cadence", "hr", "power", "speed"],
    "laps": 0,
    "elapsed_time_s": 3600.0,
    "recording_time_s": 3600.0,
    "recording_stops": [],
    "median_time_delta_s": 1.0,
    "rows": 3601,
    # A4.3's case: two meters, and the file says nothing about which one fed
    # `record.power`. Both are recorded and the tie-break is spelled out.
    "power_source_candidates": ["srm/7 #1", "wahoo_fitness/42 #2"],
    "power_source": "srm/7 #1",
    "power_source_rule": "lowest device_index among 2 candidates",
    "hr_source_candidates": ["garmin/1234 #3"],
    "hr_source": "garmin/1234 #3",
    # One strap, so no tie-break — asserted for every file that carries HR, so
    # the HR rule cannot quietly stop being recorded while the power one is.
    "hr_source_rule": "only candidate",
    # No odometer at all: the trainer wrote none, which keeps the fall-back to
    # integrating speed covered by a whole file rather than a synthetic column.
    "distance_source": None,
    "discipline": "cycling",
    "classification_source": "sport_field",
}

STRENGTH_WATCH = {
    "file_sport_index": 0,
    "sport": "training",
    "start_time": "2026-05-07T17:00:00+00:00",
    "timezone": "UTC+02:00",
    "samples": 361,
    "channels": ["hr"],
    "laps": 0,
    "elapsed_time_s": 1800.0,
    "recording_time_s": 1800.0,
    "recording_stops": [],
    "median_time_delta_s": 5.0,
    "rows": 1801,
    "power_source_candidates": [],
    "power_source": None,
    "power_source_rule": None,
    "hr_source_candidates": ["garmin/1234 #1"],
    "hr_source": "garmin/1234 #1",
    "hr_source_rule": "only candidate",
    "distance_source": None,
    "discipline": "strength",
    "classification_source": "sport_field",
}


def test_the_committed_files_are_what_the_generator_produces(tmp_path: Path) -> None:
    regenerated = build_all(tmp_path)

    assert set(regenerated) == set(BUILDERS)
    for name, path in regenerated.items():
        assert path.read_bytes() == golden(name).read_bytes(), (
            f"{name} differs from the committed golden file. Regenerate them "
            "with `uv run python tests/unit/golden_fit.py` and review the diff "
            "— the snapshots below are derived from these bytes."
        )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("outdoor_ride.fit", OUTDOOR_RIDE),
        ("indoor_trainer.fit", INDOOR_TRAINER),
        ("strength_watch.fit", STRENGTH_WATCH),
    ],
)
def test_a_single_sport_file_parses_to_one_activity(
    name: str, expected: dict[str, object]
) -> None:
    [activity] = parse(golden(name))

    assert summarise(activity) == expected
    assert validate(activity) is None, "every golden file is fit to ingest"


def test_a_multisport_file_parses_to_one_activity_per_sport() -> None:
    # A4.5: the cardinality that reaches into the dedup key. The ride and the
    # gym session share one file and one hash, and are told apart by index.
    ride, gym = parse(golden("brick.fit"))

    assert (ride.file_sport_index, ride.sport) == (0, "cycling")
    assert (gym.file_sport_index, gym.sport) == (1, "training")
    assert ride.samples[-1].t < gym.start_time, "the sports do not overlap"
    assert channels_present(gym.samples) == {StreamChannel.HR}
    # Both sports record heart rate and the file names no strap, so the rule is
    # that there was no device_info to name — not a borrowed candidate.
    assert (ride.hr_source, ride.hr_source_rule) == ("record.hr", NO_DEVICE_INFO)
    assert (gym.hr_source, gym.hr_source_rule) == ("record.hr", NO_DEVICE_INFO)
    assert validate(ride) is None
    assert validate(gym) is None


def test_the_coffee_stop_is_subtracted_from_recording_time() -> None:
    # A4.4's "done when": elapsed exceeds recording time by exactly the stop's
    # row range (D101), reported as one range, not spread over the columns.
    [activity] = parse(golden("outdoor_ride.fit"))

    resampled = resample(activity.samples)

    [(start, end)] = resampled.recording_stops
    # One row shorter than the gap: the rows holding the samples either side of
    # the stop carry real readings and are not part of it.
    assert end - start == OUTDOOR_STOP_S - 1
    assert resampled.elapsed_time_s - resampled.recording_time_s == end - start
    power = resampled.frame.columns[StreamChannel.POWER]
    assert set(power[start:end]) == {None}, "a pause is null, never zero watts"
    assert resampled.frame.device_t[start + 1] is None


def test_the_spike_survives_raw_and_is_repaired_in_fixed() -> None:
    # A4.2's "done when", end to end: the raw column keeps the dropped
    # magnet's reading, the fixed column does not, and the repair is a row.
    [activity] = parse(golden("outdoor_ride.fit"))
    resampled = resample(activity.samples)

    cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)

    window = slice(OUTDOOR_SPIKE_AT_S, OUTDOOR_SPIKE_AT_S + OUTDOOR_SPIKE_S)
    assert set(resampled.frame.columns[StreamChannel.POWER][window]) == {
        float(OUTDOOR_SPIKE_W)
    }
    repaired = cleaned.fixed[StreamChannel.POWER][window]
    assert set(repaired) == {
        resampled.frame.columns[StreamChannel.POWER][OUTDOOR_SPIKE_AT_S - 1]
    }
    [spike] = [
        anomaly
        for anomaly in cleaned.anomalies
        if anomaly.channel is StreamChannel.POWER
        and anomaly.kind.value == "spike_clipped"
    ]
    assert (spike.start_index, spike.end_index) == (
        OUTDOOR_SPIKE_AT_S,
        OUTDOOR_SPIKE_AT_S + OUTDOOR_SPIKE_S,
    )


def test_the_odometer_channel_survives_cleaning_and_is_what_distance_reads() -> None:
    # D197, end to end over a real file rather than a hand-built column: the
    # ride's `distance` field runs ahead of its own speed column, the cleaner
    # leaves it non-decreasing, and the metric reports the odometer's span —
    # naming it — rather than the speed integral it would otherwise have used.
    [activity] = parse(golden("outdoor_ride.fit"))
    resampled = resample(activity.samples)
    cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)

    odometer = cleaned.fixed[StreamChannel.DISTANCE]
    speed = cleaned.fixed[StreamChannel.SPEED]
    assessed = distance_km(speed, odometer)

    readings = [value for value in odometer if value is not None]
    assert all(
        earlier <= later for earlier, later in zip(readings, readings[1:], strict=False)
    ), "a cumulative channel must come out of the cleaner non-decreasing"
    integrated_km = sum(value for value in speed if value is not None) / 1000
    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx((readings[-1] - readings[0]) / 1000)
    # Which is emphatically not the other number: the file was built so that
    # reading the wrong column fails rather than rounds.
    assert assessed.value > integrated_km * 1.01
    assert "odometer" in assessed.explanation.assumptions[0]


def test_a_file_without_an_odometer_integrates_speed_and_says_so() -> None:
    # The other half of D197, and the reason `indoor_trainer.fit` carries no
    # distance field: the fallback is an ordinary path, not an error, and it
    # names itself so a reader can tell the two kinds of kilometre apart.
    [activity] = parse(golden("indoor_trainer.fit"))
    resampled = resample(activity.samples)
    cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)

    speed = cleaned.fixed[StreamChannel.SPEED]
    assessed = distance_km(speed, cleaned.fixed.get(StreamChannel.DISTANCE, ()))

    assert isinstance(assessed, Measured)
    assert assessed.value == pytest.approx(
        sum(value for value in speed if value is not None) / 1000
    )
    assert (
        "integrated from the 1 Hz speed channel" in assessed.explanation.assumptions[0]
    )


#: What the generator wrote into two chosen samples of the outdoor ride.
#:
#: Every number below is worked out from `golden_fit.outdoor_ride`'s own
#: formulae at ``second``, by hand, and **not** by calling the parser's
#: conversions — that is the whole point. The snapshots above count samples and
#: name channels; they would stay green if `SEMICIRCLE_DEGREES` used ``2**32``
#: instead of ``2**31``, because half of 47.4°N is 23.7°N and that is a
#: perfectly plausible latitude. A literal is the only thing that catches it.
#:
#: For ``second = s``, with ``wave = sin(s / 240)``:
#:   power       = 210 + round(60 * wave)          W
#:   heart_rate  = 138 + round(14 * wave)          bpm
#:   cadence     =  88 + round(5 * wave)           rpm
#:   speed       = round(8.4 + 1.2 * wave, 3)      m/s  (FIT: mm/s, exact)
#:   distance    = round(1.015 * Σ_{i<s} speed(i), 2) m  (FIT: cm, exact)
#:   altitude    = round(412 + 60 * sin(s/900), 1) m    (FIT: 0.2 m steps)
#:   temperature = 17                              °C
#:   position    = 47.3769 + s * 2.0e-5 °N, 8.5417 + s * 1.5e-5 °E
#:                 (FIT: signed 32-bit semicircles, ~8.4e-8° per count)
OUTDOOR_SAMPLE_VALUES = {
    # second = 0: wave = sin(0) = 0, so every channel sits at its offset.
    0: {
        "lat": 47.3769,
        "lon": 8.5417,
        "elevation": 412.0,
        "speed": 8.4,
        "distance": 0.0,  # nothing has been ridden yet
        "power": 210.0,
        "hr": 138.0,
        "cadence": 88.0,
        "temp": 17.0,
    },
    # second = 120 (1 Hz, so also index 120): wave = sin(0.5) = 0.4794255…,
    # and sin(120/900) = sin(0.13333…) = 0.1329437…
    120: {
        "lat": 47.3793,  # 47.3769 + 120 * 2.0e-5
        "lon": 8.5435,  # 8.5417  + 120 * 1.5e-5
        "elevation": 420.0,  # round(412 + 60 * 0.1329437, 1) = 419.976 -> 420.0
        "speed": 8.975,  # round(8.4 + 1.2 * 0.4794255, 3)
        # 1.015 * 1042.969, the sum of the 120 speeds already written. The
        # ratio is what makes this ODOMETER and not a second copy of the speed
        # column: integrating those 120 readings gives 1 042.97 m, and the
        # device says 1 058.61 (D197).
        "distance": 1058.61,
        "power": 239.0,  # 210 + round(28.7655)
        "hr": 145.0,  # 138 + round(6.7120)
        "cadence": 90.0,  # 88 + round(2.3971)
        "temp": 17.0,
    },
}

#: Degrees of slack allowed on a coordinate: FIT stores position as integer
#: semicircles, so a round trip loses under 1e-7°. Wide enough for the
#: quantisation, nowhere near wide enough to hide a factor of two.
COORDINATE_TOLERANCE = 1e-6


@pytest.mark.parametrize("index", sorted(OUTDOOR_SAMPLE_VALUES))
def test_the_outdoor_rides_channels_carry_the_values_that_were_written(
    index: int,
) -> None:
    [activity] = parse(golden("outdoor_ride.fit"))

    values = activity.samples[index].values

    expected = OUTDOOR_SAMPLE_VALUES[index]
    for name in ("power", "hr", "cadence", "temp", "speed", "elevation", "distance"):
        assert values[StreamChannel(name)] == expected[name], name
    for name in ("lat", "lon"):
        assert values[StreamChannel(name)] == pytest.approx(
            expected[name], abs=COORDINATE_TOLERANCE
        ), name


def test_moving_time_is_computed_from_speed_read_as_metres_per_second() -> None:
    # `moving_time_s` is the one derived number whose unit nothing else pins:
    # speed read as km/h would still be above the moving threshold, still
    # plausible, and every snapshot above would still pass. So the file's own
    # speeds are asserted as metres per second first, and the total second.
    [activity] = parse(golden("outdoor_ride.fit"))

    resampled = resample(activity.samples)

    speeds = [sample.values[StreamChannel.SPEED] for sample in activity.samples]
    # round(8.4 + 1.2 * sin(s/240), 3) over the seconds actually sampled: the
    # crest at s = 377 (sin -> 1.0) and the trough at s = 1200 (sin(5) =
    # -0.958924), the first sample after the coffee stop.
    assert max(speeds) == 9.6
    assert min(speeds) == 7.249
    # Every sample is far above walking pace, so moving time is the whole of
    # the time the device was recording: 600 samples one second apart, then
    # 300 four seconds apart. The 600 s stop is a gap, not slow riding.
    assert resampled.moving_time_s == 600 * 1 + 300 * 4


def test_the_gym_recording_classifies_as_strength_from_the_sport_field() -> None:
    [activity] = parse(golden("strength_watch.fit"))
    resampled = resample(activity.samples)

    discipline, source = classify_discipline(
        sport=activity.sport,
        has_power=False,
        has_speed=False,
        has_gps=False,
        duration_s=resampled.elapsed_time_s,
    )

    assert (discipline, source) == (
        SessionDiscipline.STRENGTH,
        ClassificationSource.SPORT_FIELD,
    )
