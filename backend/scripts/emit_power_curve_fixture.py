"""Generate the frontend's power-curve fixtures by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3). A power curve is the purest case of a shape type-checking
cannot police: every point is the argmax of a window search, the 5 s best can
never sit below the 1 min best, and a duration longer than the ride does not
exist. A hand-typed curve that broke any of that would type-check and describe
a ride nobody rode.

So the curves are computed: `app.domain.power_curve.power_curve` over a
stream, projected through **`app.api.routes.power_curve.to_read`** — the very
function the endpoint uses — so the emitted JSON is byte-for-byte a response
the API serves. Three documents:

* ``RIDE_POWER_CURVE`` — over the *same* synthetic 1 200 s stream
  `emit_metrics_fixture.py` emits as ``RIDE_STREAMS``, with its one repaired
  power region, so a session's curve and its chart agree. Twenty minutes long,
  so it has no 60 min point: the readout has to leave that slot out.
* ``LONG_RIDE_POWER_CURVE`` — a two-hour ride, long enough for every readout
  duration (5 s, 1, 5, 20 and 60 min).
* ``NO_POWER_CURVE`` and ``NO_RECORDING_CURVE`` — the two ``not_assessed``
  answers, with the service's own reasons.

Run it with ``just power-curve-fixture`` after changing
`app/domain/power_curve.py`, the response shape, or the metrics fixture's
stream, and commit the result.
"""

import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any

from app.api.routes.power_curve import to_read
from app.domain.power_curve import power_curve
from app.domain.streams import StreamChannel
from app.services.power_curve import NO_POWER, NO_RECORDING, SessionPowerCurve
from scripts.emit_metrics_fixture import build_columns

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "frontend" / "tests" / "mocks" / "generated-power-curve.ts"

#: The repaired power region `emit_metrics_fixture.py` attaches to its stream.
RIDE_ANOMALIES = [(512, 515)]

#: The two-hour ride: ``(seconds, watts)`` blocks — a warm-up, three
#: 20-minute threshold efforts, a sprint, and an endurance tail.
LONG_BLOCKS: list[tuple[int, float]] = [
    (900, 170.0),
    (1_200, 268.0),
    (300, 140.0),
    (1_200, 262.0),
    (300, 140.0),
    (1_200, 255.0),
    (600, 160.0),
    (15, 820.0),
    (1_485, 185.0),
]


def long_ride() -> list[float | None]:
    """The two-hour ride's power column, deterministic to the sample."""
    column: list[float | None] = []
    second = 0
    for duration, watts in LONG_BLOCKS:
        for _ in range(duration):
            wobble = 14 * math.sin(second * 0.7) * math.cos(second * 0.11)
            column.append(round(max(0.0, watts + wobble), 2))
            second += 1
    return column


def document(answer: SessionPowerCurve) -> dict[str, Any]:
    """One answer, exactly as the endpoint serialises it."""
    return to_read(answer).model_dump(mode="json")


def build() -> dict[str, dict[str, Any]]:
    """Every exported constant's payload, by name."""
    session_id = uuid.UUID(int=0)
    ride = power_curve(build_columns()[StreamChannel.POWER], anomalies=RIDE_ANOMALIES)
    long = power_curve(long_ride())
    if ride is None or long is None:
        raise SystemExit("a fixture stream carries no power: nothing to emit")
    return {
        "RIDE_POWER_CURVE": document(
            SessionPowerCurve(session_id=session_id, curve=ride, reason=None)
        ),
        "LONG_RIDE_POWER_CURVE": document(
            SessionPowerCurve(session_id=session_id, curve=long, reason=None)
        ),
        "NO_POWER_CURVE": document(
            SessionPowerCurve(session_id=session_id, curve=None, reason=NO_POWER)
        ),
        "NO_RECORDING_CURVE": document(
            SessionPowerCurve(session_id=session_id, curve=None, reason=NO_RECORDING)
        ),
    }


HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_power_curve_fixture.py`
// (`just power-curve-fixture`) by running `app.domain.power_curve` over a
// stream and projecting the result through
// `app.api.routes.power_curve.to_read` — the same function the endpoint uses.
// `RIDE_POWER_CURVE` is computed over the same stream as `RIDE_STREAMS` in
// `generated-metrics.ts`, so a session's curve and its chart agree.
//
// Regenerate after changing app/domain/power_curve.py, the power-curve read
// shape or the metrics fixture's stream, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];
"""

#: The doc comment above each constant.
COMMENTS = {
    "RIDE_POWER_CURVE": (
        "The curve of the synthetic 20-minute ride `RIDE_STREAMS` carries: "
        "no 60 min point."
    ),
    "LONG_RIDE_POWER_CURVE": (
        "A two-hour ride, long enough for every readout duration."
    ),
    "NO_POWER_CURVE": "A recorded session with no power meter.",
    "NO_RECORDING_CURVE": "A session entered by hand.",
}


def main() -> None:
    """Write the generated module."""
    sections = [
        f"\n/** {COMMENTS[name]} */\n"
        f'export const {name}: Schemas["{_schema(payload)}"] = '
        f"{json.dumps(payload, indent=2, sort_keys=True)};\n"
        for name, payload in build().items()
    ]
    DESTINATION.write_text(HEADER + "".join(sections), encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(ROOT)}", file=sys.stderr)


def _schema(payload: dict[str, Any]) -> str:
    """The OpenAPI component a payload is an instance of."""
    return (
        "AssessedPowerCurveRead"
        if payload["status"] == "assessed"
        else "NotAssessedPowerCurveRead"
    )


if __name__ == "__main__":
    main()
