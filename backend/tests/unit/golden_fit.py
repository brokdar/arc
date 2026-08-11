"""Synthetic golden FIT files: the operator has no real ones to give us.

Work-order §0 decision 4. Four deterministic files, each shaped around a case
the pipeline has to get right, written by `fit-tool` (a dev dependency) and
**committed as binaries** under `tests/data/golden/`:

* ``outdoor_ride.fit`` — GPS, power, HR, cadence, speed, altitude and
  temperature; sampling that switches from 1 Hz to every 4 s; a 600 s coffee
  stop (A4.4's "done when"); a two-second 2 900 W spike from a dropped magnet
  — outside `app.domain.streams.PLAUSIBLE_RANGE`, so the cleaner clips it and
  records the repair (A4.2's "done when"); two laps; a local offset of +02:00;
  and a cumulative ``distance`` channel deliberately
  :data:`OUTDOOR_ODOMETER_RATIO` **above** what integrating its own speed
  gives, which is the real head unit's behaviour (D197) and the only way a
  test can tell the two distances apart.
* ``indoor_trainer.fit`` — no GPS, smooth 1 Hz power, **two** ANT+ power
  meters so the source rule has a choice to make (A4.3's "done when"); no
  local offset, so the session's timezone falls back to ``UTC``; and no
  odometer channel at all, which keeps the fall-back-to-speed path covered by
  a whole file rather than by a hand-built column.
* ``strength_watch.fit`` — thirty minutes of heart rate and nothing else,
  sport ``training``: the shape that must classify as strength.
* ``brick.fit`` — one file, two sports (A4.5), so ``file_sport_index`` is
  exercised by something other than a hand-built row.

Regenerate with ``uv run python tests/unit/golden_fit.py`` from ``backend/``.
The bytes are deterministic — no clock, no randomness, no serial numbers that
change per run — and `test_golden_fit_files.py` re-runs the builders into a
temporary directory and asserts the committed files are byte-identical, so a
`fit-tool` upgrade that changes the encoding is a failed test rather than a
fixture that silently stopped matching its generator.

Real-file parse tests are **operator-pending**: when real exports exist, add
them beside these and snapshot their summaries the same way. Nothing here is
skipped in the meantime — every pipeline test runs against these.
"""

import datetime as dt
import math
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.message import Message
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    AntplusDeviceType,
    FileType,
    Manufacturer,
    SourceType,
    Sport,
    SubSport,
)

#: Where the committed binaries live.
GOLDEN_DIR = Path(__file__).parents[1] / "data" / "golden"


#: FIT counts its `date_time` fields in seconds from 1989-12-31 UTC.
FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.UTC)


def _millis(moment: dt.datetime) -> int:
    """fit-tool takes timestamps as milliseconds since the Unix epoch."""
    return round(moment.timestamp() * 1000)


def _fit_seconds(moment: dt.datetime) -> int:
    """Seconds from the FIT epoch.

    `fit-tool` rescales the `timestamp` field for us but not the
    `local_timestamp` one, which it passes through as the raw uint32 the
    format stores.
    """
    return round((moment - FIT_EPOCH).total_seconds())


def _file_id(created: dt.datetime) -> FileIdMessage:
    """The header every FIT activity file starts with."""
    message = FileIdMessage()
    message.type = FileType.ACTIVITY
    message.manufacturer = Manufacturer.GARMIN.value
    message.product = 3121
    message.serial_number = 1234567890
    message.time_created = _millis(created)
    return message


def _device(
    *,
    at: dt.datetime,
    index: int,
    manufacturer: Manufacturer,
    product: int,
    device_type: AntplusDeviceType | None,
    source: SourceType,
) -> DeviceInfoMessage:
    """One paired sensor, as a head unit records it."""
    message = DeviceInfoMessage()
    message.timestamp = _millis(at)
    message.device_index = index
    message.manufacturer = manufacturer.value
    message.product = product
    message.source_type = source
    if device_type is not None:
        message.antplus_device_type = device_type.value
    return message


def _lap(start: dt.datetime, end: dt.datetime) -> LapMessage:
    """One lap marker."""
    message = LapMessage()
    message.start_time = _millis(start)
    message.timestamp = _millis(end)
    message.total_elapsed_time = (end - start).total_seconds()
    return message


def _session(
    *,
    start: dt.datetime,
    elapsed_s: float,
    sport: Sport,
    sub_sport: SubSport | None = None,
) -> SessionMessage:
    """One sport within the file (A4.5)."""
    message = SessionMessage()
    message.start_time = _millis(start)
    message.timestamp = _millis(start + dt.timedelta(seconds=elapsed_s))
    message.total_elapsed_time = elapsed_s
    message.sport = sport
    if sub_sport is not None:
        message.sub_sport = sub_sport
    return message


def _activity(
    *, start: dt.datetime, elapsed_s: float, sessions: int, offset: dt.timedelta | None
) -> ActivityMessage:
    """The activity trailer — and the only place a local offset appears.

    FIT has no timezone field: `local_timestamp` is the same instant written
    on the athlete's own clock, and the difference between the two is all the
    offset there is (§0 decision 5, D93).
    """
    message = ActivityMessage()
    end = start + dt.timedelta(seconds=elapsed_s)
    message.timestamp = _millis(end)
    message.total_timer_time = elapsed_s
    message.num_sessions = sessions
    if offset is not None:
        message.local_timestamp = _fit_seconds(end + offset)
    return message


def _record(at: dt.datetime, **values: float | int | None) -> RecordMessage:
    """One sample. Absent channels are omitted, never written as zero."""
    message = RecordMessage()
    message.timestamp = _millis(at)
    for name, value in values.items():
        if value is not None:
            setattr(message, name, value)
    return message


def _build(messages: Sequence[Message], destination: Path) -> None:
    """Encode messages into a FIT file at ``destination``."""
    builder = FitFileBuilder(auto_define=True)
    for message in messages:
        builder.add(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    builder.build().to_file(str(destination))


# --- the four files -----------------------------------------------------------

#: Start of the outdoor ride, aware UTC.
OUTDOOR_START = dt.datetime(2026, 5, 4, 7, 30, tzinfo=dt.UTC)
#: The athlete-local offset the outdoor ride's head unit wrote.
OUTDOOR_OFFSET = dt.timedelta(hours=2)
#: Seconds of 1 Hz riding before the coffee stop.
OUTDOOR_FIRST_LEG_S = 600
#: The coffee stop itself — A4.4's ten minutes.
OUTDOOR_STOP_S = 600
#: Seconds of 4 s sampling after it.
OUTDOOR_SECOND_LEG_S = 1200
#: Where the power spike sits, in seconds from the start.
OUTDOOR_SPIKE_AT_S = 300
#: How long it lasts. Two seconds is a spike; `clean` clips it and records it.
OUTDOOR_SPIKE_S = 2
#: What the dropped magnet reports. Above the 2 500 W top of power's plausible
#: range, because a value *inside* the range is a hard effort, not a defect —
#: `clean` repairs implausible readings and this one has to be one.
OUTDOOR_SPIKE_W = 2900

#: How much further the outdoor ride's odometer claims than integrating its
#: own once-a-second speed does.
#:
#: A head unit integrates wheel revolutions continuously and writes speed
#: rounded to the millimetre per second once a second, so its ``distance``
#: field runs a little ahead of anything reconstructed from that column — on
#: the Wahoo BOLT ride this system was checked against, by 1.5 % over 41 km
#: (D197). A golden file whose odometer merely *agreed* with its speed column
#: could not tell the two apart, and every test of which one the distance
#: metric read would pass whichever it read.
OUTDOOR_ODOMETER_RATIO = 1.015


def _outdoor_offsets() -> Iterator[int]:
    """Seconds from the start at which the outdoor ride recorded a sample.

    The first leg's last sample is *on* the boundary, so the gap between it
    and the first sample after the stop is exactly ``OUTDOOR_STOP_S`` — which
    is what A4.4's "elapsed exceeds recording time by ~600 s" is measured
    against.
    """
    yield from range(OUTDOOR_FIRST_LEG_S + 1)
    resume = OUTDOOR_FIRST_LEG_S + OUTDOOR_STOP_S
    yield from range(resume, resume + OUTDOOR_SECOND_LEG_S + 1, 4)


def _outdoor_speed(second: int) -> float:
    """The outdoor ride's speed at ``second``, in m/s, as the file records it.

    Named because the odometer integrates the same rounded numbers the records
    carry: a generator that integrated the unrounded sine would write an
    odometer no reader of the file could reproduce.
    """
    return round(8.4 + 1.2 * math.sin(second / 240.0), 3)


def outdoor_ride(destination: Path) -> None:
    """GPS + power + HR, irregular sampling, a 600 s stop and a spike."""
    elapsed_s = OUTDOOR_FIRST_LEG_S + OUTDOOR_STOP_S + OUTDOOR_SECOND_LEG_S
    messages: list[Message] = [
        _file_id(OUTDOOR_START),
        _device(
            at=OUTDOOR_START,
            index=0,
            manufacturer=Manufacturer.GARMIN,
            product=3121,
            device_type=None,
            source=SourceType.LOCAL,
        ),
        _device(
            at=OUTDOOR_START,
            index=1,
            manufacturer=Manufacturer.SRM,
            product=7,
            device_type=AntplusDeviceType.BIKE_POWER,
            source=SourceType.ANTPLUS,
        ),
        _device(
            at=OUTDOOR_START,
            index=2,
            manufacturer=Manufacturer.GARMIN,
            product=1234,
            device_type=AntplusDeviceType.HEART_RATE,
            source=SourceType.ANTPLUS,
        ),
    ]
    odometer_m = 0.0
    previous: int | None = None
    for second in _outdoor_offsets():
        wave = math.sin(second / 240.0)
        power = 210 + round(60 * wave)
        if OUTDOOR_SPIKE_AT_S <= second < OUTDOOR_SPIKE_AT_S + OUTDOOR_SPIKE_S:
            power = OUTDOOR_SPIKE_W
        speed = round(8.4 + 1.2 * wave, 3)
        # The odometer advances over the interval since the *previous recorded
        # sample*, so it stands still across the coffee stop exactly as a
        # paused head unit's does — and comes out ahead of the speed column by
        # the ratio, not by whatever the gaps happened to add up to.
        if previous is not None and second - previous <= OUTDOOR_STOP_S - 1:
            odometer_m += _outdoor_speed(previous) * (second - previous)
        previous = second
        messages.append(
            _record(
                OUTDOOR_START + dt.timedelta(seconds=second),
                power=power,
                heart_rate=138 + round(14 * wave),
                cadence=88 + round(5 * wave),
                speed=speed,
                distance=round(odometer_m * OUTDOOR_ODOMETER_RATIO, 2),
                altitude=round(412.0 + 60 * math.sin(second / 900.0), 1),
                temperature=17,
                position_lat=round(47.3769 + second * 2.0e-5, 7),
                position_long=round(8.5417 + second * 1.5e-5, 7),
            )
        )
    messages += [
        _lap(OUTDOOR_START, OUTDOOR_START + dt.timedelta(seconds=OUTDOOR_FIRST_LEG_S)),
        _lap(
            OUTDOOR_START + dt.timedelta(seconds=OUTDOOR_FIRST_LEG_S),
            OUTDOOR_START + dt.timedelta(seconds=elapsed_s),
        ),
        _session(start=OUTDOOR_START, elapsed_s=elapsed_s, sport=Sport.CYCLING),
        _activity(
            start=OUTDOOR_START,
            elapsed_s=elapsed_s,
            sessions=1,
            offset=OUTDOOR_OFFSET,
        ),
    ]
    _build(messages, destination)


#: Start of the indoor trainer ride, aware UTC. No local offset is written.
INDOOR_START = dt.datetime(2026, 5, 6, 18, 0, tzinfo=dt.UTC)
#: Its duration — one hour at 1 Hz.
INDOOR_ELAPSED_S = 3600


def indoor_trainer(destination: Path) -> None:
    """No GPS, smooth power, and two power meters to choose between (A4.3)."""
    messages: list[Message] = [
        _file_id(INDOOR_START),
        _device(
            at=INDOOR_START,
            index=1,
            manufacturer=Manufacturer.SRM,
            product=7,
            device_type=AntplusDeviceType.BIKE_POWER,
            source=SourceType.ANTPLUS,
        ),
        _device(
            at=INDOOR_START,
            index=2,
            manufacturer=Manufacturer.WAHOO_FITNESS,
            product=42,
            device_type=AntplusDeviceType.BIKE_POWER,
            source=SourceType.ANTPLUS,
        ),
        _device(
            at=INDOOR_START,
            index=3,
            manufacturer=Manufacturer.GARMIN,
            product=1234,
            device_type=AntplusDeviceType.HEART_RATE,
            source=SourceType.ANTPLUS,
        ),
    ]
    for second in range(INDOOR_ELAPSED_S + 1):
        block = second // 600
        power = 190 + 25 * (block % 3)
        messages.append(
            _record(
                INDOOR_START + dt.timedelta(seconds=second),
                power=power,
                heart_rate=132 + 6 * (block % 3),
                cadence=92,
                speed=round(7.5 + 0.4 * (block % 3), 3),
            )
        )
    messages += [
        _session(
            start=INDOOR_START,
            elapsed_s=INDOOR_ELAPSED_S,
            sport=Sport.CYCLING,
            sub_sport=SubSport.INDOOR_CYCLING,
        ),
        _activity(
            start=INDOOR_START, elapsed_s=INDOOR_ELAPSED_S, sessions=1, offset=None
        ),
    ]
    _build(messages, destination)


#: Start of the gym recording, aware UTC.
STRENGTH_START = dt.datetime(2026, 5, 7, 17, 0, tzinfo=dt.UTC)
#: Half an hour, sampled every five seconds — a watch, not a head unit.
STRENGTH_ELAPSED_S = 1800
#: Its sample interval.
STRENGTH_INTERVAL_S = 5


def strength_watch(destination: Path) -> None:
    """A short recording with heart rate and nothing else."""
    messages: list[Message] = [
        _file_id(STRENGTH_START),
        _device(
            at=STRENGTH_START,
            index=1,
            manufacturer=Manufacturer.GARMIN,
            product=1234,
            device_type=AntplusDeviceType.HEART_RATE,
            source=SourceType.ANTPLUS,
        ),
    ]
    for second in range(0, STRENGTH_ELAPSED_S + 1, STRENGTH_INTERVAL_S):
        set_phase = (second // 90) % 2
        messages.append(
            _record(
                STRENGTH_START + dt.timedelta(seconds=second),
                heart_rate=104 + 38 * set_phase,
            )
        )
    messages += [
        _session(
            start=STRENGTH_START,
            elapsed_s=STRENGTH_ELAPSED_S,
            sport=Sport.TRAINING,
            sub_sport=SubSport.STRENGTH_TRAINING,
        ),
        _activity(
            start=STRENGTH_START,
            elapsed_s=STRENGTH_ELAPSED_S,
            sessions=1,
            offset=dt.timedelta(hours=2),
        ),
    ]
    _build(messages, destination)


#: Start of the brick session, aware UTC.
BRICK_START = dt.datetime(2026, 5, 9, 9, 0, tzinfo=dt.UTC)
#: Seconds of riding, then seconds of lifting, back to back in one file.
BRICK_RIDE_S = 1800
BRICK_GYM_S = 900


def brick(destination: Path) -> None:
    """One file, two sports — A4.5's cardinality, in bytes."""
    gym_start = BRICK_START + dt.timedelta(seconds=BRICK_RIDE_S)
    messages: list[Message] = [
        _file_id(BRICK_START),
        _device(
            at=BRICK_START,
            index=1,
            manufacturer=Manufacturer.SRM,
            product=7,
            device_type=AntplusDeviceType.BIKE_POWER,
            source=SourceType.ANTPLUS,
        ),
    ]
    # Exclusive of the boundary second: a real multisport file has a
    # transition, and a record written at the instant the next sport begins
    # would belong to both of them.
    messages.extend(
        _record(
            BRICK_START + dt.timedelta(seconds=second),
            power=205,
            heart_rate=140,
            cadence=90,
            speed=8.0,
        )
        for second in range(0, BRICK_RIDE_S, 2)
    )
    messages.extend(
        _record(gym_start + dt.timedelta(seconds=second), heart_rate=118)
        for second in range(0, BRICK_GYM_S + 1, 5)
    )
    messages += [
        _session(start=BRICK_START, elapsed_s=BRICK_RIDE_S, sport=Sport.CYCLING),
        _session(
            start=gym_start,
            elapsed_s=BRICK_GYM_S,
            sport=Sport.TRAINING,
            sub_sport=SubSport.STRENGTH_TRAINING,
        ),
        _activity(
            start=BRICK_START,
            elapsed_s=BRICK_RIDE_S + BRICK_GYM_S,
            sessions=2,
            offset=dt.timedelta(hours=2),
        ),
    ]
    _build(messages, destination)


#: Filename -> builder. The single source of truth for what exists on disk.
BUILDERS: dict[str, Callable[[Path], None]] = {
    "outdoor_ride.fit": outdoor_ride,
    "indoor_trainer.fit": indoor_trainer,
    "strength_watch.fit": strength_watch,
    "brick.fit": brick,
}


def build_all(directory: Path) -> dict[str, Path]:
    """Write every golden file into ``directory`` and return the paths."""
    written: dict[str, Path] = {}
    for name, builder in BUILDERS.items():
        destination = directory / name
        builder(destination)
        written[name] = destination
    return written


def golden(name: str) -> Path:
    """Path of one committed golden file.

    Raises:
        FileNotFoundError: When it has not been generated — run this module.
    """
    path = GOLDEN_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing; regenerate the golden files with "
            "`uv run python tests/unit/golden_fit.py` from backend/"
        )
    return path


if __name__ == "__main__":
    for name, path in build_all(GOLDEN_DIR).items():
        print(f"{name}: {path.stat().st_size} bytes")  # noqa: T201
