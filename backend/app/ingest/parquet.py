"""The stream store: one parquet file per recording, on the 1 Hz grid.

`data/streams/<recording_id>.parquet` is the only place samples live — the
database holds the session, the recording's metadata and the anomalies, and
nothing else. A four-hour ride is 14 400 rows per channel, which is a file and
not a table (see `app.persistence.activity`).

**The column contract** (A4.1, A4.2):

* ``t`` — the *original device timestamp* of the sample that landed in each
  row, null where the grid filled the row itself. It is not the row's instant:
  that is ``t0 + row_index`` seconds and needs no column.
* one column per recorded channel, holding the raw readings, spikes included.
* one ``<channel>_fixed`` column per channel, holding what analysis reads. The
  cleaner writes a fixed column for **every** channel, so which columns exist
  never depends on how dirty the file was.

**The file metadata** carries ``t0`` (the grid origin, ISO 8601) and one
``source.<channel>`` entry per channel whose source was resolved (A4.3), so a
frame taken out of this directory alone still says when it started and which
meter produced it.

Everything downstream addresses rows by index. Nothing here interprets a
value: the repairs happened in `app.domain.streams` and are already recorded.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.domain.streams import CleanResult, StreamChannel, StreamFrame

#: Column holding each row's original device timestamp.
DEVICE_TIME_COLUMN = "t"

#: Suffix of the cleaned counterpart of a channel column.
FIXED_SUFFIX = "_fixed"

#: File-metadata key holding the grid origin, ISO 8601 with its offset.
T0_KEY = "t0"

#: File-metadata key prefix for the per-channel source label (A4.3).
SOURCE_PREFIX = "source."

#: Millisecond timestamps: FIT, GPX and TCX all record whole seconds, and
#: milliseconds leave room for a device that does not without doubling the
#: column's width.
TIME_TYPE = pa.timestamp("ms", tz="UTC")

#: Parquet extension used for stream files.
STREAM_SUFFIX = ".parquet"


def stream_path(streams_root: Path, recording_id: uuid.UUID) -> Path:
    """Where one recording's samples are stored."""
    return streams_root / f"{recording_id}{STREAM_SUFFIX}"


@dataclass(frozen=True, slots=True)
class StoredStreams:
    """What :func:`read_streams` gives back — the frame as it was written.

    Args:
        t0: The grid origin, aware UTC.
        device_t: One entry per row: the original device timestamp, or
            ``None`` where the grid filled the row.
        raw: Channel -> the readings as recorded.
        fixed: Channel -> the cleaned readings analysis reads.
        sources: Channel -> the source label recorded for it, for the channels
            that had one.
    """

    t0: dt.datetime
    device_t: tuple[dt.datetime | None, ...]
    raw: Mapping[StreamChannel, tuple[float | None, ...]]
    fixed: Mapping[StreamChannel, tuple[float | None, ...]]
    sources: Mapping[StreamChannel, str]

    @property
    def row_count(self) -> int:
        """Number of one-second rows in the stored grid."""
        return len(self.device_t)


def write_streams(
    path: Path,
    *,
    frame: StreamFrame,
    cleaned: CleanResult,
    sources: Mapping[StreamChannel, str] | None = None,
) -> None:
    """Write one recording's grid to parquet.

    Args:
        path: Destination; its parent is created if needed.
        frame: The resampled grid — the raw columns and ``device_t``.
        cleaned: The ``*_fixed`` columns for the same frame.
        sources: Per-channel source label to record in the file metadata.

    Raises:
        ValueError: When a cleaned column is not the same height as the frame,
            or names a channel the frame does not have. Both mean the caller
            paired a frame with another frame's cleaning, and a parquet file
            whose columns disagree on their row count is exactly the corruption
            the equal-length invariant exists to prevent.
    """
    arrays: list[pa.Array] = [pa.array(list(frame.device_t), type=TIME_TYPE)]
    names: list[str] = [DEVICE_TIME_COLUMN]
    for channel in _ordered(frame.columns):
        arrays.append(pa.array(list(frame.columns[channel]), type=pa.float64()))
        names.append(channel.value)
    for channel in _ordered(cleaned.fixed):
        column = cleaned.fixed[channel]
        if channel not in frame.columns:
            raise ValueError(
                f"cleaned column {channel.value!r} has no raw counterpart in the frame"
            )
        if len(column) != frame.row_count:
            raise ValueError(
                f"cleaned column {channel.value!r} has {len(column)} rows, the "
                f"frame has {frame.row_count}"
            )
        arrays.append(pa.array(list(column), type=pa.float64()))
        names.append(f"{channel.value}{FIXED_SUFFIX}")

    metadata = {T0_KEY: frame.t0.isoformat()} | {
        f"{SOURCE_PREFIX}{channel.value}": label
        for channel, label in sorted(
            (sources or {}).items(), key=lambda item: item[0].value
        )
    }
    table = pa.Table.from_arrays(
        arrays, schema=pa.schema(zip(names, (a.type for a in arrays), strict=True))
    ).replace_schema_metadata(metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def read_streams(path: Path) -> StoredStreams:
    """Read one recording's grid back.

    Raises:
        FileNotFoundError: When no stream file exists at ``path``.
        ValueError: When the file carries no ``t0`` — it was not written by
            :func:`write_streams`, and the rows cannot be placed in time.
    """
    table = pq.read_table(path)
    metadata = {
        key.decode(): value.decode()
        for key, value in (table.schema.metadata or {}).items()
    }
    origin = metadata.get(T0_KEY)
    if origin is None:
        raise ValueError(f"{path} carries no {T0_KEY!r} metadata")

    raw: dict[StreamChannel, tuple[float | None, ...]] = {}
    fixed: dict[StreamChannel, tuple[float | None, ...]] = {}
    for name in table.column_names:
        if name == DEVICE_TIME_COLUMN:
            continue
        target, channel_name = (
            (fixed, name.removesuffix(FIXED_SUFFIX))
            if name.endswith(FIXED_SUFFIX)
            else (raw, name)
        )
        target[StreamChannel(channel_name)] = tuple(table.column(name).to_pylist())
    return StoredStreams(
        t0=dt.datetime.fromisoformat(origin),
        device_t=tuple(table.column(DEVICE_TIME_COLUMN).to_pylist()),
        raw=raw,
        fixed=fixed,
        sources={
            StreamChannel(key.removeprefix(SOURCE_PREFIX)): value
            for key, value in metadata.items()
            if key.startswith(SOURCE_PREFIX)
        },
    )


def _ordered(columns: Mapping[StreamChannel, object]) -> Sequence[StreamChannel]:
    """Channels in a stable order, so two runs write identical column lists."""
    return sorted(columns, key=lambda channel: channel.value)
