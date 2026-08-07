"""The stream store round-trips a frame, invariants and all.

What is asserted here is the storage contract other work packages will read
against: equal column lengths, nulls across a pause, the raw column keeping
what the fixed one repaired, and the metadata that lets a file taken out of
this directory alone still say when it started and which meter it came from.
"""

import uuid
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from app.domain.streams import (
    CleanResult,
    ParsedActivity,
    ResampleResult,
    StreamChannel,
    clean,
    resample,
)
from app.ingest.parquet import (
    DEVICE_TIME_COLUMN,
    SOURCE_PREFIX,
    T0_KEY,
    read_streams,
    stream_path,
    write_streams,
)
from app.ingest.parsers import parse
from tests.unit.golden_fit import OUTDOOR_SPIKE_AT_S, OUTDOOR_SPIKE_W, golden


def ride_frame() -> tuple[ParsedActivity, ResampleResult, CleanResult]:
    """The outdoor ride, resampled and cleaned."""
    [activity] = parse(golden("outdoor_ride.fit"))
    resampled = resample(activity.samples)
    cleaned = clean(resampled.frame, recording_stops=resampled.recording_stops)
    return activity, resampled, cleaned


def test_a_written_frame_reads_back_identical(tmp_path: Path) -> None:
    activity, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())
    assert activity.power_source is not None
    assert activity.hr_source is not None

    write_streams(
        path,
        frame=resampled.frame,
        cleaned=cleaned,
        sources={
            StreamChannel.POWER: activity.power_source,
            StreamChannel.HR: activity.hr_source,
        },
    )
    stored = read_streams(path)

    assert stored.t0 == resampled.frame.t0
    assert stored.row_count == resampled.frame.row_count
    assert stored.device_t == resampled.frame.device_t
    assert set(stored.raw) == set(resampled.frame.columns)
    assert set(stored.fixed) == set(cleaned.fixed)
    for channel, column in resampled.frame.columns.items():
        assert stored.raw[channel] == column, channel.value
    for channel, column in cleaned.fixed.items():
        assert stored.fixed[channel] == column, channel.value
    assert stored.sources[StreamChannel.POWER] == activity.power_source


def test_every_stored_column_has_one_entry_per_row(tmp_path: Path) -> None:
    # A4.1's property, at the storage boundary rather than in memory: the
    # invariant is only useful if it survives being written down.
    _, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())

    write_streams(path, frame=resampled.frame, cleaned=cleaned)
    table = pq.read_table(path)

    assert table.num_rows == resampled.frame.row_count
    assert {column.null_count <= table.num_rows for column in table.columns} == {True}
    assert all(len(column) == table.num_rows for column in table.columns)


def test_a_pause_is_null_in_every_column_and_in_the_device_time(
    tmp_path: Path,
) -> None:
    _, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())
    [(start, end)] = resampled.recording_stops

    write_streams(path, frame=resampled.frame, cleaned=cleaned)
    stored = read_streams(path)

    assert set(stored.device_t[start:end]) == {None}
    for channel, column in stored.raw.items():
        assert set(column[start:end]) == {None}, channel.value
    for channel, column in stored.fixed.items():
        assert set(column[start:end]) == {None}, channel.value


def test_the_raw_column_keeps_the_spike_the_fixed_one_repaired(
    tmp_path: Path,
) -> None:
    _, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())

    write_streams(path, frame=resampled.frame, cleaned=cleaned)
    stored = read_streams(path)

    assert stored.raw[StreamChannel.POWER][OUTDOOR_SPIKE_AT_S] == float(OUTDOOR_SPIKE_W)
    assert stored.fixed[StreamChannel.POWER][OUTDOOR_SPIKE_AT_S] != float(
        OUTDOOR_SPIKE_W
    )


def test_the_file_says_when_it_started_and_where_its_numbers_came_from(
    tmp_path: Path,
) -> None:
    activity, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())
    assert activity.power_source is not None

    write_streams(
        path,
        frame=resampled.frame,
        cleaned=cleaned,
        sources={StreamChannel.POWER: activity.power_source},
    )
    metadata = pq.read_table(path).schema.metadata

    assert metadata[T0_KEY.encode()].decode() == resampled.frame.t0.isoformat()
    assert metadata[f"{SOURCE_PREFIX}power".encode()].decode() == activity.power_source
    assert DEVICE_TIME_COLUMN in pq.read_table(path).column_names


def test_a_cleaning_from_another_frame_is_refused(tmp_path: Path) -> None:
    # Pairing a frame with somebody else's cleaning would write a parquet file
    # whose columns disagree on their height — the one corruption the equal
    # length invariant exists to prevent.
    [activity] = parse(golden("outdoor_ride.fit"))
    [other] = parse(golden("indoor_trainer.fit"))
    resampled = resample(activity.samples)
    mismatched = clean(resample(other.samples).frame)

    with pytest.raises(ValueError, match="rows"):
        write_streams(
            stream_path(tmp_path, uuid.uuid7()),
            frame=resampled.frame,
            cleaned=mismatched,
        )


def test_a_file_without_a_grid_origin_is_refused(tmp_path: Path) -> None:
    _, resampled, cleaned = ride_frame()
    path = stream_path(tmp_path, uuid.uuid7())
    write_streams(path, frame=resampled.frame, cleaned=cleaned)
    table = pq.read_table(path).replace_schema_metadata({})
    pq.write_table(table, path)

    with pytest.raises(ValueError, match=T0_KEY):
        read_streams(path)
