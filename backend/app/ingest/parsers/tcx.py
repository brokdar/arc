"""Reading TCX activity files.

TCX is Garmin's XML training format: laps containing trackpoints, with heart
rate and cadence in the schema proper and speed and power in a per-trackpoint
extension. `tcxreader` resolves all of that into plain objects, so the work
here is mapping its names onto channels and refusing what is not a recording.

Two of its defaults are wrong for us and are overridden: ``only_gps=True``
would throw away the start and end of an indoor session, and the linear
null-filling would invent samples that the domain — which owns every repair
and records each one as an anomaly (A4.2) — must be the only thing producing.

Like GPX, one file is one activity: TCX writes several laps, never several
sports.
"""

import datetime as dt
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from tcxreader.tcxreader import TCXReader

from app.domain.streams import (
    ParsedActivity,
    RawSample,
    StreamChannel,
    channels_present,
)
from app.ingest.parsers.base import (
    UnreadableFileError,
    as_utc,
    choose_source,
    sample_values,
)

#: `null_value_handling`: leave a missing reading missing. See the module
#: docstring — filling it here would be an unrecorded repair.
NULLS_STAY_NULL = 1

#: Trackpoint extension key (lowercased) -> channel. Garmin writes ``Speed``
#: in m/s and ``Watts`` in watts inside the TPX extension.
EXTENSION_CHANNELS: Mapping[str, StreamChannel] = {
    "speed": StreamChannel.SPEED,
    "watts": StreamChannel.POWER,
    "cadence": StreamChannel.CADENCE,
}


def parse_tcx(path: Path) -> Sequence[ParsedActivity]:
    """Parse a TCX file into a single activity.

    Raises:
        UnreadableFileError: When the document does not parse or carries no
            timestamped trackpoint.
    """
    try:
        exercise = TCXReader().read(
            str(path), only_gps=False, null_value_handling=NULLS_STAY_NULL
        )
    except Exception as exc:
        raise UnreadableFileError(f"the file is not readable TCX ({exc})") from exc

    samples = sorted(_samples(exercise.trackpoints or ()), key=lambda s: s.t)
    if not samples:
        raise UnreadableFileError(
            "the TCX document parsed, but none of its trackpoints carried a time"
        )
    present = channels_present(samples)
    _, power_source, power_rule = choose_source(
        (), channel=StreamChannel.POWER, present=StreamChannel.POWER in present
    )
    _, hr_source, hr_rule = choose_source(
        (), channel=StreamChannel.HR, present=StreamChannel.HR in present
    )
    return [
        ParsedActivity(
            file_sport_index=0,
            sport=exercise.activity_type or None,
            start_time=samples[0].t,
            # TCX timestamps are UTC and the format carries no offset.
            local_offset=None,
            samples=tuple(samples),
            laps=tuple(_laps(exercise)),
            power_source_candidates=(),
            power_source=power_source,
            power_source_rule=power_rule,
            hr_source_candidates=(),
            hr_source=hr_source,
            hr_source_rule=hr_rule,
        )
    ]


def _samples(trackpoints: Sequence[Any]) -> Iterator[RawSample]:
    """Turn trackpoints into samples, skipping any without a time."""
    for point in trackpoints:
        moment = getattr(point, "time", None)
        if not isinstance(moment, dt.datetime):
            continue
        values: dict[StreamChannel, float | int | None] = {
            StreamChannel.LAT: point.latitude,
            StreamChannel.LON: point.longitude,
            StreamChannel.ELEVATION: point.elevation,
            StreamChannel.HR: point.hr_value,
            StreamChannel.CADENCE: point.cadence,
        }
        for key, value in (point.tpx_ext or {}).items():
            channel = EXTENSION_CHANNELS.get(str(key).lower())
            if channel is not None and isinstance(value, int | float):
                values[channel] = value
        yield RawSample(t=as_utc(moment), values=sample_values(values))


def _laps(exercise: Any) -> Iterator[tuple[dt.datetime, dt.datetime]]:
    """The file's own laps as ``(start, end)`` instants."""
    for lap in exercise.laps or ():
        start, end = getattr(lap, "start_time", None), getattr(lap, "end_time", None)
        if isinstance(start, dt.datetime) and isinstance(end, dt.datetime):
            yield as_utc(start), as_utc(end)
