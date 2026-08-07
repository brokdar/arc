"""Reading GPX tracks.

GPX is a route format that grew sensors: position, time and elevation are in
the schema, and heart rate, cadence, power and temperature arrive as vendor
extension elements hanging off each track point. `gpxpy` parses the document
and leaves the extensions as XML elements, so they are read here by local tag
name — the namespace differs per vendor (Garmin's TrackPointExtension, Strava's
gpxtpx, the de-facto `power` element) while the tag names do not.

A GPX file is always **one** activity: the format has no notion of a sport
boundary, so A4.5's sequence has one element.
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import gpxpy

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

#: Extension tag (lowercased, namespace stripped) -> channel. `atemp` is
#: Garmin's air temperature; `temp` is what several other writers use.
EXTENSION_CHANNELS: Mapping[str, StreamChannel] = {
    "hr": StreamChannel.HR,
    "heartrate": StreamChannel.HR,
    "cad": StreamChannel.CADENCE,
    "cadence": StreamChannel.CADENCE,
    "power": StreamChannel.POWER,
    "watts": StreamChannel.POWER,
    "speed": StreamChannel.SPEED,
    "atemp": StreamChannel.TEMP,
    "temp": StreamChannel.TEMP,
}


def parse_gpx(path: Path) -> Sequence[ParsedActivity]:
    """Parse a GPX file into a single activity.

    Raises:
        UnreadableFileError: When the document does not parse, or carries no
            track point with a timestamp. A GPX route (a plan, not a
            recording) has no times, and it is not a session.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            document = gpxpy.parse(handle)
    except Exception as exc:
        raise UnreadableFileError(f"the file is not readable GPX ({exc})") from exc

    samples = sorted(_samples(document), key=lambda sample: sample.t)
    if not samples:
        raise UnreadableFileError(
            "the GPX document parsed, but none of its track points carried a "
            "time; a route without timestamps is not a recording"
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
            sport=_sport(document),
            start_time=samples[0].t,
            # GPX writes UTC instants and no offset at all, so there is
            # nothing better than `UTC` to store (§0 decision 5).
            local_offset=None,
            samples=tuple(samples),
            laps=(),
            power_source_candidates=(),
            power_source=power_source,
            power_source_rule=power_rule,
            hr_source_candidates=(),
            hr_source=hr_source,
            hr_source_rule=hr_rule,
        )
    ]


def _sport(document: Any) -> str | None:
    """The track's declared type, when it has one."""
    for track in document.tracks:
        if track.type:
            return str(track.type)
    return None


def _samples(document: Any) -> Iterator[RawSample]:
    """Every timestamped track point in the document, in document order."""
    for track in document.tracks:
        for segment in track.segments:
            for point in segment.points:
                if point.time is None:
                    continue
                values: dict[StreamChannel, float | int | None] = {
                    StreamChannel.LAT: point.latitude,
                    StreamChannel.LON: point.longitude,
                    StreamChannel.ELEVATION: point.elevation,
                }
                values |= _extensions(point)
                yield RawSample(t=as_utc(point.time), values=sample_values(values))


def _extensions(point: Any) -> dict[StreamChannel, float | None]:
    """Read the sensor channels a vendor hung off this track point.

    Namespaces are stripped: the tag names are stable across vendors even
    though the namespace URIs are not, and a value that does not parse as a
    number is dropped rather than guessed at.
    """
    found: dict[StreamChannel, float | None] = {}
    for element in _elements(point.extensions):
        tag = str(element.tag).rpartition("}")[2].lower()
        channel = EXTENSION_CHANNELS.get(tag)
        if channel is None or element.text is None:
            continue
        try:
            found[channel] = float(element.text.strip())
        except ValueError:
            continue
    return found


def _elements(elements: Any) -> Iterator[Any]:
    """Flatten an extension tree — vendors nest their sensors one level deep."""
    for element in elements or ():
        yield element
        yield from _elements(list(element))
