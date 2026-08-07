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
from app.domain.streams import (
    ParsedActivity,
    StreamChannel,
    channels_present,
    clean,
    resample,
    validate,
)
from app.ingest.parsers import parse
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
        "elevation",
        "hr",
        "lat",
        "lon",
        "power",
        "speed",
        "temp",
    ],
    "laps": 2,
    "elapsed_time_s": 2400.0,
    "recording_time_s": 1800.0,
    "recording_stops": [[601, 1200]],
    "median_time_delta_s": 1.0,
    "rows": 2401,
    "power_source_candidates": ["srm/7 #1"],
    "power_source": "srm/7 #1",
    "power_source_rule": "only candidate",
    "hr_source_candidates": ["garmin/1234 #2"],
    "hr_source": "garmin/1234 #2",
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
    assert validate(ride) is None
    assert validate(gym) is None


def test_the_coffee_stop_is_subtracted_from_recording_time() -> None:
    # A4.4's "done when": elapsed exceeds recording time by the stop, and the
    # stop is reported as one row range, not spread over the columns.
    [activity] = parse(golden("outdoor_ride.fit"))

    resampled = resample(activity.samples)

    assert resampled.elapsed_time_s - resampled.recording_time_s == OUTDOOR_STOP_S
    [(start, end)] = resampled.recording_stops
    # One row shorter than the gap: the rows holding the samples either side of
    # the stop carry real readings and are not part of it.
    assert end - start == OUTDOOR_STOP_S - 1
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
