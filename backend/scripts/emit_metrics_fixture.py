"""Generate the frontend's session-metrics fixture by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). For metrics that is a stronger demand than type-checking
can meet: NP, IF, TSS, time-in-zone, the polarization index and the detected
intervals are all derived from **the same stream**, and a hand-typed fixture
where NP is 241 W and the power column averages 150 W type-checks perfectly
while asserting against a ride no pipeline could produce. The component test
would then agree with the fixture instead of with the application.

So the fixture is generated: this script builds a synthetic 20-minute session
on the 1 Hz grid, runs `app.domain.session_analysis` over it with fixed anchor
versions, and emits both halves — the metric artefact and the stream payload —
as one TypeScript module the mock handlers import. The numbers in it agree
with each other because the same code produced them.

Run it with ``just metrics-fixture`` after changing anything in
`app/domain/metrics.py`, `app/domain/alignment.py` or
`app/domain/session_analysis.py`, and commit the result.
"""

import datetime as dt
import json
import math
import uuid
from pathlib import Path
from typing import Any

from app.domain.activity import SessionDiscipline
from app.domain.anchors import (
    ANCHOR_UNITS,
    AnchorSource,
    AnchorType,
    AnchorVersion,
    Provenance,
)
from app.domain.athlete import Sex
from app.domain.session_analysis import (
    SessionInputs,
    analyse_session,
    analysis_to_json,
    zone_model_of,
)
from app.domain.streams import StreamChannel

#: Where the generated module lands.
DESTINATION = (
    Path(__file__).parents[2] / "frontend" / "tests" / "mocks" / "generated-metrics.ts"
)

#: The grid origin of the synthetic session, aware UTC.
T0 = dt.datetime(2026, 8, 5, 5, 14, tzinfo=dt.UTC)

#: Stable ids, so regenerating the fixture does not churn the diff.
RECORDING_ID = uuid.UUID("0199a000-0000-7000-8000-0000000002a1")
ANCHOR_IDS = {
    AnchorType.FTP: uuid.UUID("0199a000-0000-7000-8000-0000000003f1"),
    AnchorType.LTHR: uuid.UUID("0199a000-0000-7000-8000-0000000003f2"),
    AnchorType.MAX_HR: uuid.UUID("0199a000-0000-7000-8000-0000000003f3"),
    AnchorType.RESTING_HR: uuid.UUID("0199a000-0000-7000-8000-0000000003f4"),
}

#: The anchor values the fixture is computed against.
ANCHOR_VALUES = {
    AnchorType.FTP: 262.0,
    AnchorType.LTHR: 171.0,
    AnchorType.MAX_HR: 190.0,
    AnchorType.RESTING_HR: 48.0,
}


def anchor(anchor_type: AnchorType) -> AnchorVersion:
    """One anchor version of the fixture's athlete."""
    return AnchorVersion(
        anchor_type=anchor_type,
        value=ANCHOR_VALUES[anchor_type],
        unit=ANCHOR_UNITS[anchor_type],
        provenance=Provenance.ESTIMATED,
        effective_date=dt.date(2026, 7, 12),
        created_at=dt.datetime(2026, 7, 12, 9, 0, tzinfo=dt.UTC),
        source=AnchorSource.ATHLETE,
        ci_low=ANCHOR_VALUES[anchor_type] - 15,
        ci_high=ANCHOR_VALUES[anchor_type] + 15,
    )


#: The shape of the synthetic ride: `(seconds, watts, bpm, rpm)` per block.
#: A warm-up, four work intervals off recoveries, and a cool-down — the
#: session the mockup's analysis screen shows, at a fifth of the length so the
#: committed fixture stays readable.
BLOCKS: list[tuple[int, float, float, float]] = [
    (240, 150.0, 128.0, 84.0),
    (120, 305.0, 168.0, 96.0),
    (90, 120.0, 138.0, 80.0),
    (120, 302.0, 172.0, 95.0),
    (90, 118.0, 140.0, 79.0),
    (120, 298.0, 174.0, 94.0),
    (90, 116.0, 141.0, 78.0),
    (120, 291.0, 176.0, 92.0),
    (210, 130.0, 132.0, 82.0),
]

#: Where the recording was paused, as a `[start, end)` row range. Deliberately
#: present: a fixture with no stop cannot exercise the one thing the chart has
#: to get right, which is drawing a hole as a break rather than as zero watts.
STOP = (700, 760)

#: A traffic light in the middle of a recovery, as a `[start, end)` row range.
#: Twenty-five seconds — under `GAP_THRESHOLD_S`, so the head unit kept
#: recording through it and it is **not** a recording stop. That is the whole
#: point of it: it is the difference between recording time and moving time
#: (D194), so a fixture without one cannot tell an average over the first from
#: an average over the second, and the header's stopped-time slot would have
#: nothing but the pause above to show.
STANDSTILL = (400, 425)

#: Freewheeling down the last hill, as a `[start, end)` row range: still
#: travelling, no watts. Coasting and standing still are different facts about
#: a ride and the header now shows both, so the fixture has to contain both —
#: with only a traffic light in it, the two slots could be swapped and every
#: test would still pass.
FREEWHEEL = (1_130, 1_170)


#: How far ahead of its own speed column the fixture's odometer runs.
#:
#: A head unit integrates wheel revolutions far faster than the once-a-second
#: speed it writes, so its cumulative `distance` field is a percent or two
#: above anything reconstructed from that column — 1.5 % on the real ride this
#: system was checked against (D197). The fixture carries the gap so that a
#: component rendering "distance" is rendering the odometer's kilometres,
#: which is what the API now serves, and not the speed integral it used to.
ODOMETER_RATIO = 1.015

#: Decimal places every emitted sample is rounded to.
#:
#: The rounding happens **before** the analysis, not on the way out. A stream
#: rounded afterwards would no longer be the stream the metrics were computed
#: from, and the frontend's own NP over the emitted column would disagree with
#: the artefact's by a fraction of a watt — which is exactly the drift the
#: generated fixture exists to make impossible.
SAMPLE_PLACES = 2


def _wobble(second: int, amplitude: float) -> float:
    """Deterministic sensor noise. Seeded by the row, so reruns are identical."""
    return amplitude * math.sin(second * 0.7) * math.cos(second * 0.11)


def _sample(value: float) -> float:
    """One emitted reading, at the precision the fixture carries."""
    return round(value, SAMPLE_PLACES)


def build_columns() -> dict[StreamChannel, tuple[float | None, ...]]:
    """The synthetic session's cleaned columns, on the 1 Hz grid."""
    power: list[float | None] = []
    heart_rate: list[float | None] = []
    cadence: list[float | None] = []
    speed: list[float | None] = []
    odometer: list[float | None] = []
    elevation: list[float | None] = []
    temperature: list[float | None] = []
    travelled = 0.0
    second = 0
    for duration, watts, bpm, rpm in BLOCKS:
        for _ in range(duration):
            paused = STOP[0] <= second < STOP[1]
            # Standing at the light: no watts, no cadence, no speed — and a
            # heart rate, because the athlete is still there and the strap is
            # still recording. Zero, not null: the device recorded these rows.
            standing = STANDSTILL[0] <= second < STANDSTILL[1]
            freewheeling = FREEWHEEL[0] <= second < FREEWHEEL[1]
            power.append(
                None
                if paused
                else 0.0
                if standing or freewheeling
                else _sample(max(0.0, watts + _wobble(second, 18)))
            )
            heart_rate.append(None if paused else _sample(bpm + _wobble(second, 2)))
            cadence.append(
                None
                if paused
                else 0.0
                if standing or freewheeling
                else _sample(rpm + _wobble(second, 4))
            )
            metres_per_s = (
                None
                if paused
                else 0.0
                if standing
                else _sample(9.0 + _wobble(second, 1.5))
            )
            speed.append(metres_per_s)
            # Cumulative, and it does not advance through the pause — a device
            # that stopped recording did not roll anywhere.
            travelled += (metres_per_s or 0.0) * ODOMETER_RATIO
            odometer.append(None if paused else _sample(travelled))
            elevation.append(
                None
                if paused
                else _sample(412.0 + 24 * math.sin(second / 260) + _wobble(second, 0.4))
            )
            # A morning ride warming up from 14 °C to about 19 °C, as the
            # device's own sensor would read it.
            temperature.append(
                None
                if paused
                else _sample(14.0 + 5 * second / 1200 + _wobble(second, 0.3))
            )
            second += 1
    return {
        StreamChannel.POWER: tuple(power),
        StreamChannel.HR: tuple(heart_rate),
        StreamChannel.CADENCE: tuple(cadence),
        StreamChannel.SPEED: tuple(speed),
        StreamChannel.DISTANCE: tuple(odometer),
        StreamChannel.ELEVATION: tuple(elevation),
        StreamChannel.TEMP: tuple(temperature),
    }


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(metrics, streams)`` as the API would serve them."""
    columns = build_columns()
    rows = len(columns[StreamChannel.POWER])
    stopped = STOP[1] - STOP[0]
    anchors = {anchor_type: anchor(anchor_type) for anchor_type in ANCHOR_IDS}

    analysis = analyse_session(
        SessionInputs(
            discipline=SessionDiscipline.CYCLING,
            # Elapsed minus the stop rows — A4.4's arithmetic, which is what
            # makes the fixture's load agree with the duration beside it.
            recording_time_s=float(rows - stopped),
            elapsed_time_s=float(rows),
            columns=columns,
            sex=Sex.MALE,
            anchors=anchors,
        )
    )
    metrics = analysis_to_json(analysis) | {
        "version": 1,
        "computed_at": "2026-08-05T07:55:31Z",
        "recompute_reason": None,
        "pins": [
            {
                "anchor_type": anchor_type.value,
                "version_id": str(version_id),
                "value": ANCHOR_VALUES[anchor_type],
                "unit": ANCHOR_UNITS[anchor_type].value,
                "provenance": "estimated",
                "effective_date": "2026-07-12",
                "ci_low": ANCHOR_VALUES[anchor_type] - 15,
                "ci_high": ANCHOR_VALUES[anchor_type] + 15,
            }
            for anchor_type, version_id in ANCHOR_IDS.items()
        ],
        "power_zone_model": _model(zone_model_of(analysis.power_time_in_zone)),
        "hr_zone_model": _model(zone_model_of(analysis.hr_time_in_zone)),
    }
    streams = {
        "recording_id": str(RECORDING_ID),
        # One recording, which is every session the MVP ingests; the list is
        # what a merged session (WP-6.5) fills with more than one.
        "recording_ids": [str(RECORDING_ID)],
        "t0": T0.isoformat().replace("+00:00", "Z"),
        "length": rows,
        "channels": [
            {
                "channel": channel.value,
                "source": _SOURCES.get(channel),
                "values": list(column),
            }
            for channel, column in sorted(
                columns.items(), key=lambda item: item[0].value
            )
        ],
        "recording_stops": [{"start_index": STOP[0], "end_index": STOP[1]}],
        # One repair, so the chart's anomaly marking has something to draw and
        # the fixture states that a repaired region is not a measurement.
        "anomalies": [
            {
                "channel": "power",
                "start_index": 512,
                "end_index": 515,
                "kind": "spike_clipped",
                "substituted_value": 298.0,
            }
        ],
    }
    return metrics, streams


_SOURCES = {
    StreamChannel.POWER: "Quarq DZero",
    StreamChannel.HR: "Wahoo TICKR",
}


def _model(model: Any) -> str | None:
    """The zone model's stored value, or ``None``."""
    return None if model is None else model.value


HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_metrics_fixture.py` (`just metrics-fixture`)
// by running the real domain over a synthetic 1 Hz session. Every number here
// therefore agrees with every other one: NP, IF, TSS, the zone distribution,
// the polarization index and the detected intervals all came out of the same
// stream, which is exactly what a hand-typed fixture cannot promise and what
// the testing strategy's third rule demands.
//
// Regenerate after changing app/domain/metrics.py, app/domain/alignment.py or
// app/domain/session_analysis.py, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/** The metric artefact of the synthetic ride. */
export const RIDE_METRICS: Schemas["SessionMetricsRead"] =
"""

STREAMS_HEADER = """
/** The 1 Hz stream payload the metrics above were computed from. */
export const RIDE_STREAMS: Schemas["SessionStreamsRead"] =
"""


def main() -> None:
    """Write the generated module."""
    metrics, streams = build()
    body = (
        HEADER
        + json.dumps(metrics, indent=2)
        + ";\n"
        + STREAMS_HEADER
        + json.dumps(streams, indent=2)
        + ";\n"
    )
    DESTINATION.write_text(body, encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(Path(__file__).parents[2])}")


if __name__ == "__main__":
    main()
