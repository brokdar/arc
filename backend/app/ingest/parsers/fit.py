"""Reading FIT files: the Garmin SDK first, `fitdecode` when it will not.

Two decoders behind one function, because the failure they cover is real: the
official SDK is strict about the profile and gives up on a file a head unit
truncated mid-write, while `fitdecode` reads frame by frame and returns what
it got. A ride that ended with a flat battery is exactly the file an athlete
most wants back, so the strict reader is tried first (it resolves the profile
enums to names) and the tolerant one is the fallback.

Both decoders are normalised into the same plain dictionaries before a single
builder turns them into `app.domain.streams.ParsedActivity` values, so the two
paths cannot drift into producing different sessions from the same bytes.
"""

import datetime as dt
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import fitdecode
from garmin_fit_sdk import Decoder, Stream

from app.core.logging import get_logger
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
    semicircles_to_degrees,
)

logger = get_logger(__name__)

#: FIT counts its `date_time` fields in seconds from this instant. The SDK
#: resolves `timestamp` to a datetime for us but leaves `local_timestamp` as
#: the raw count, because its type is a *local* date-time: a wall-clock
#: reading with no zone attached.
FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.UTC)

#: ANT+ device types (by profile name and by number) that produce power.
POWER_DEVICE_TYPES = frozenset({"bike_power", 11})

#: ANT+ device types that produce heart rate.
HR_DEVICE_TYPES = frozenset({"heart_rate", 120})

#: `device_index` 0 is the head unit itself; the SDK renders it as this.
CREATOR_INDEX = "creator"

#: What a device label says when the file did not name the part.
UNKNOWN_DEVICE_PART = "unknown"

#: `device_info` fields naming the maker, in preference order.
MANUFACTURER_FIELDS = ("manufacturer",)

#: `device_info` fields naming the product, in preference order.
#:
#: FIT stores one product number, but the profile gives it manufacturer-
#: specific *subfields*: a Garmin sensor writes its number under
#: `garmin_product`, a Favero one under `favero_product`. The SDK reports the
#: base field **and** the subfield; `fitdecode` reports only the subfield. Read
#: the base field first and the same bytes would label a Garmin strap
#: ``garmin/1234`` through one decoder and ``garmin/unknown`` through the
#: other — provenance that depends on which reader ran. The subfields come
#: first, and both decoders resolve them through the same profile, so a
#: resolved name (``edge_530``) comes out of both or neither.
PRODUCT_FIELDS = ("garmin_product", "favero_product", "product")

#: Record fields read for each channel, in preference order. The `enhanced_`
#: variants are the wider-range ones a modern head unit writes; either may be
#: the only one present.
RECORD_FIELDS: Mapping[StreamChannel, tuple[str, ...]] = {
    StreamChannel.POWER: ("power",),
    StreamChannel.HR: ("heart_rate",),
    StreamChannel.CADENCE: ("cadence",),
    StreamChannel.SPEED: ("enhanced_speed", "speed"),
    StreamChannel.DISTANCE: ("distance",),
    StreamChannel.ELEVATION: ("enhanced_altitude", "altitude"),
    StreamChannel.TEMP: ("temperature",),
}

#: What the odometer's source label says when the FIT records carried one.
#: There is no device to name: `record.distance` is written by the head unit
#: itself, from whatever wheel or GPS source it decided to trust, and the file
#: says nothing more than that.
DISTANCE_SOURCE = "record.distance"


def parse_fit(path: Path) -> Sequence[ParsedActivity]:
    """Parse a FIT file into one activity per sport it contains (A4.5).

    Raises:
        UnreadableFileError: When neither decoder can make an activity out of
            the file.
    """
    try:
        messages = _read_with_sdk(path)
    except Exception as exc:  # noqa: BLE001 — the fallback is the point
        logger.info("fit_sdk_failed", path=str(path), error=str(exc))
        messages = _read_with_fitdecode(path, sdk_error=exc)
    return _activities(messages)


# --- decoding -----------------------------------------------------------------

#: The message groups the builder needs, in the SDK's naming.
type Messages = dict[str, list[dict[str, Any]]]


def _read_with_sdk(path: Path) -> Messages:
    """Decode with `garmin-fit-sdk`, which resolves the profile's enums.

    Raises:
        RuntimeError: When the decoder reports errors or finds no records —
            both are the signal to try the tolerant reader.
    """
    # `Stream.from_file` opens a reader the SDK never closes, so the file
    # descriptor is finalised by the garbage collector — noisily, and at a
    # point unrelated to the failure. Handing it a buffer we own keeps that
    # inside this function.
    stream = Stream.from_byte_array(bytearray(path.read_bytes()))
    decoder = Decoder(stream)
    if not decoder.is_fit():
        raise RuntimeError("not a FIT file")
    messages, errors = decoder.read()
    if errors:
        raise RuntimeError(f"the FIT decoder reported {len(errors)} error(s)")
    if not messages.get("record_mesgs"):
        raise RuntimeError("the FIT file carries no record messages")
    # The SDK types its result as its own `FitMessages` alias; it is the same
    # `{group: [field-dict, ...]}` shape the fallback builds by hand, and
    # `Messages` is the name this module reasons in.
    return cast(Messages, messages)


#: `fitdecode` frame names mapped onto the SDK's message-group keys.
FRAME_GROUPS = {
    "record": "record_mesgs",
    "session": "session_mesgs",
    "lap": "lap_mesgs",
    "device_info": "device_info_mesgs",
    "activity": "activity_mesgs",
}


def _read_with_fitdecode(path: Path, *, sdk_error: Exception) -> Messages:
    """Decode frame by frame, keeping whatever survived a truncated write.

    A head unit that stopped mid-write leaves a partial final frame, and
    `fitdecode` raises when it reaches it — but only *after* yielding every
    whole frame ahead of it. Those frames are the ride, so they are harvested
    and the decode error is fatal only when nothing came back. The file also
    loses its trailing `session`, `lap` and `activity` messages, which is why
    a recovered ride has no sport, no laps and no local offset: the parser
    reports what the bytes said, and nothing more.

    Whether what survived is *enough* is not decided here.
    :func:`app.domain.streams.validate` is the one place that judges a
    recording fit to ingest, and a parser that second-guessed it would
    quarantine files under a different rule than every other format.

    Raises:
        UnreadableFileError: When no record frame could be recovered. The
            message names the SDK's complaint as well, because that is the one
            a well-formed-but-unsupported file fails with.
    """
    messages: Messages = {group: [] for group in FRAME_GROUPS.values()}
    decode_error: Exception | None = None
    try:
        with fitdecode.FitReader(str(path)) as reader:
            for frame in reader:
                group = FRAME_GROUPS.get(getattr(frame, "name", ""))
                if group is None or not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                messages[group].append(
                    {field.name: field.value for field in frame.fields}
                )
    except Exception as exc:  # noqa: BLE001 — harvesting is the point
        decode_error = exc

    records = messages["record_mesgs"]
    if not records:
        detail = (
            f"{decode_error}; the Garmin decoder said: {sdk_error}"
            if decode_error is not None
            else f"the Garmin decoder said: {sdk_error}"
        )
        raise UnreadableFileError(
            "the file is not a readable FIT recording: no samples could be "
            f"decoded from it ({detail})"
        )
    if decode_error is not None:
        logger.info(
            "fit_partial_recovery",
            path=str(path),
            records=len(records),
            error=str(decode_error),
        )
    return messages


# --- building activities ------------------------------------------------------


def _activities(messages: Messages) -> Sequence[ParsedActivity]:
    """Split the decoded messages into one activity per sport.

    Raises:
        UnreadableFileError: When no sample carried a usable timestamp.
    """
    samples = list(_samples(messages.get("record_mesgs", ())))
    if not samples:
        raise UnreadableFileError(
            "the FIT file decoded, but none of its records carried a timestamp"
        )
    samples.sort(key=lambda sample: sample.t)

    offset = _local_offset(messages.get("activity_mesgs", ()))
    laps = _laps(messages.get("lap_mesgs", ()))
    power_candidates = _device_labels(
        messages.get("device_info_mesgs", ()), POWER_DEVICE_TYPES
    )
    hr_candidates = _device_labels(
        messages.get("device_info_mesgs", ()), HR_DEVICE_TYPES
    )

    boundaries = _sport_boundaries(messages.get("session_mesgs", ()))
    activities: list[ParsedActivity] = []
    for index, (start, sport) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else None
        # The first sport also owns anything recorded before its own start:
        # a head unit writes records while it acquires satellites, and
        # dropping them would move the session's start time.
        lower = start if index else None
        window = [
            sample
            for sample in samples
            if (lower is None or sample.t >= lower) and (end is None or sample.t < end)
        ]
        if not window:
            continue
        activities.append(
            _activity(
                index=len(activities),
                sport=sport,
                samples=window,
                offset=offset,
                laps=laps,
                power_candidates=power_candidates,
                hr_candidates=hr_candidates,
            )
        )
    if not activities:
        raise UnreadableFileError(
            "the FIT file decoded, but no sport in it carried any samples"
        )
    return activities


def _activity(
    *,
    index: int,
    sport: str | None,
    samples: Sequence[RawSample],
    offset: dt.timedelta | None,
    laps: Sequence[tuple[dt.datetime, dt.datetime]],
    power_candidates: Sequence[str],
    hr_candidates: Sequence[str],
) -> ParsedActivity:
    """Assemble one sport's activity, sources and all."""
    present = channels_present(samples)
    power_all, power_source, power_rule = choose_source(
        power_candidates,
        channel=StreamChannel.POWER,
        present=StreamChannel.POWER in present,
    )
    hr_all, hr_source, hr_rule = choose_source(
        hr_candidates, channel=StreamChannel.HR, present=StreamChannel.HR in present
    )
    start, end = samples[0].t, samples[-1].t
    return ParsedActivity(
        file_sport_index=index,
        sport=sport,
        start_time=start,
        local_offset=offset,
        samples=tuple(samples),
        laps=tuple(
            (lap_start, lap_end)
            for lap_start, lap_end in laps
            if lap_start >= start and lap_start <= end
        ),
        power_source_candidates=power_all,
        power_source=power_source,
        power_source_rule=power_rule,
        hr_source_candidates=hr_all,
        hr_source=hr_source,
        hr_source_rule=hr_rule,
        distance_source=(
            DISTANCE_SOURCE if StreamChannel.DISTANCE in present else None
        ),
    )


def _samples(records: Sequence[Mapping[str, Any]]) -> Iterator[RawSample]:
    """Turn record messages into samples, skipping any without a timestamp."""
    for record in records:
        moment = record.get("timestamp")
        if not isinstance(moment, dt.datetime):
            continue
        values: dict[StreamChannel, float | int | None] = {
            channel: _first(record, fields) for channel, fields in RECORD_FIELDS.items()
        }
        latitude = _first(record, ("position_lat",))
        longitude = _first(record, ("position_long",))
        if latitude is not None and longitude is not None:
            values[StreamChannel.LAT] = semicircles_to_degrees(latitude)
            values[StreamChannel.LON] = semicircles_to_degrees(longitude)
        yield RawSample(t=as_utc(moment), values=sample_values(values))


def _first(record: Mapping[str, Any], fields: Sequence[str]) -> float | None:
    """The first of ``fields`` this record carries as a number."""
    for name in fields:
        value = record.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    return None


def _sport_boundaries(
    sessions: Sequence[Mapping[str, Any]],
) -> list[tuple[dt.datetime | None, str | None]]:
    """Where each sport starts and what it is called (A4.5).

    A file with no session messages is one activity of unknown sport, which is
    what ``[(None, None)]`` says. Sessions are sorted by start time and the
    timeline is partitioned by them, so a record belongs to the last sport
    that had begun when it was written.
    """
    boundaries = [
        (as_utc(session["start_time"]), _sport_name(session))
        for session in sessions
        if isinstance(session.get("start_time"), dt.datetime)
    ]
    if not boundaries:
        return [(None, None)]
    boundaries.sort(key=lambda entry: entry[0])
    return list(boundaries)


def _sport_name(session: Mapping[str, Any]) -> str | None:
    """The sport string a session message carries.

    The `sport` field wins — it is the vocabulary
    `app.domain.activity.SPORT_FIELD_DISCIPLINE` is written in, and A-5's rule
    is that the file's own sport field maps first. `sub_sport` is the fallback
    for the file that leaves `sport` generic, where "strength_training" is the
    only thing said at all; taking it in preference would demote a plain
    ``cycling`` / ``indoor_cycling`` ride to a heuristic classification for no
    gain.
    """
    sport = session.get("sport")
    if isinstance(sport, str) and sport not in ("generic", ""):
        return sport
    sub_sport = session.get("sub_sport")
    return sub_sport if isinstance(sub_sport, str) else None


def _device_labels(
    devices: Sequence[Mapping[str, Any]], device_types: frozenset[str | int]
) -> list[str]:
    """Label every paired sensor that could have produced a channel (A4.3).

    FIT writes one `record.power` field and one `device_info` message per
    paired sensor, with **nothing linking them**: a bike with a crank meter
    and a smart trainer produces two candidates and no evidence about which
    one fed the records. Enumerating them is what makes that ambiguity
    visible; `app.ingest.parsers.base.choose_source` picks one and says the
    tie-break out loud.

    Ordered by `device_index` — pairing order, and the tie-break — with the
    head unit's own index 0 sorting first.
    """
    labelled: list[tuple[int, str]] = []
    for device in devices:
        kind = device.get("antplus_device_type", device.get("device_type"))
        if kind not in device_types:
            continue
        index = _device_index(device.get("device_index"))
        labelled.append((index, _device_label(device, index)))
    return [label for _, label in sorted(labelled, key=lambda entry: entry[0])]


def _device_label(device: Mapping[str, Any], index: int) -> str:
    """One sensor's provenance label, spelled the same for either decoder.

    The single normalisation point for `device_info`: both readers reach it
    with their own field spellings and leave with one string, so the label a
    recording carries does not depend on which decoder opened the file.
    """
    manufacturer = _named_part(device, MANUFACTURER_FIELDS)
    product = _named_part(device, PRODUCT_FIELDS)
    return f"{manufacturer}/{product} #{index}"


def _named_part(device: Mapping[str, Any], fields: Sequence[str]) -> str:
    """The first of ``fields`` this device names, or ``"unknown"``."""
    for name in fields:
        value = device.get(name)
        if value is None or isinstance(value, bool) or value == "":
            continue
        return str(value)
    return UNKNOWN_DEVICE_PART


def _device_index(raw: Any) -> int:
    """The numeric `device_index`, with the SDK's ``"creator"`` read as 0."""
    if raw == CREATOR_INDEX:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


def _laps(laps: Sequence[Mapping[str, Any]]) -> list[tuple[dt.datetime, dt.datetime]]:
    """The file's own lap markers as ``(start, end)`` instants."""
    return [
        (as_utc(lap["start_time"]), as_utc(lap["timestamp"]))
        for lap in laps
        if isinstance(lap.get("start_time"), dt.datetime)
        and isinstance(lap.get("timestamp"), dt.datetime)
    ]


def _local_offset(activities: Sequence[Mapping[str, Any]]) -> dt.timedelta | None:
    """The athlete-local UTC offset the device wrote, if it wrote one.

    FIT has no timezone field. The activity message carries `timestamp` (UTC)
    and `local_timestamp` (the same instant on the athlete's own clock), and
    their difference is the whole of what the file knows — hence the
    fixed-offset spelling `app.domain.activity.timezone_label` produces (§0
    decision 5, D93).

    Offsets are rounded to the minute: real zones are whole minutes, and a
    device that writes a second of skew must not produce a timezone string
    nothing can parse.
    """
    for activity in activities:
        moment = activity.get("timestamp")
        local = activity.get("local_timestamp")
        if not isinstance(moment, dt.datetime):
            continue
        local_moment = _as_local_moment(local)
        if local_moment is None:
            continue
        seconds = (local_moment - as_utc(moment)).total_seconds()
        return dt.timedelta(minutes=round(seconds / 60))
    return None


def _as_local_moment(local: Any) -> dt.datetime | None:
    """Read `local_timestamp` from either decoder's rendering of it.

    The SDK leaves it as the raw FIT second count; `fitdecode` resolves it to
    a naive datetime holding the local wall clock.
    """
    if isinstance(local, bool):
        return None
    if isinstance(local, int):
        return FIT_EPOCH + dt.timedelta(seconds=local)
    if isinstance(local, dt.datetime):
        return local.replace(tzinfo=dt.UTC)
    return None
