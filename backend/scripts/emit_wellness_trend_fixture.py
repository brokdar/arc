"""Generate the frontend's wellness-trend fixture by running the real domain.

A fixture must be a payload the real API could produce (the repo's testing
strategy, rule 3), and a trend read is one of the shapes where type-checking
proves almost nothing. Every number in it is derived: the baseline mean is a
fold over sixty days of readings, the band is ``0.5 x CV`` of their natural
logs, ``deviation_sd`` is the seven-day mean's distance from that mean, and
``mature`` is the output of two thresholds over the readings that survived the
confounder and recall exclusions. A hand-typed fixture where a baseline over
nine readings carries a mean type-checks perfectly and describes an answer
`app.domain.wellness_baseline` cannot produce — and the component test then
agrees with the fixture instead of with the application.

So the whole document is generated: this script writes a synthetic sixty-day
athlete, runs `trend_for` and `readiness` over it, and projects the result
through **`app.api.routes.wellness.to_trend_read`** — the very function the
endpoint uses — so the emitted JSON is byte-for-byte a response the API serves.

The scenarios are chosen to cover what `/wellness` has to render:

* ``resting_hr_bpm`` — a mature, banded baseline: the ordinary case.
* ``hrv_rmssd_ms`` — readings, but only nine of them: the series charts and
  the band does not, which is the abstention the page exists to render.
* ``weight_kg`` — mature with a trend and **no** band, because a daily SD
  deviation from a body weight is a statement nobody should make.
* ``spo2`` — no readings at all: an abstention over zero, and no chart.
* ``motivation`` — a subjective series with gaps in it, so the chart has a
  break to draw.

Run it with ``just wellness-trend-fixture`` after changing
`app/domain/wellness_baseline.py` or the API's trend shape, and commit the
result.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from app.api.routes.wellness import to_trend_read
from app.domain.wellness import HrvContext, HrvMetric, WellnessDay
from app.domain.wellness_baseline import (
    MARKERS,
    DaySample,
    WellnessMetric,
    readiness,
    trend_for,
)
from app.services.wellness import WellnessTrend

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "frontend" / "tests" / "mocks" / "generated-wellness-trend.ts"

#: The anchor the emitted document ends on. A fixed date rather than "today",
#: so the file is stable in git; the mock rebases it onto whatever range the
#: component asks for, which changes the calendar and none of the numbers.
AS_OF = dt.date(2026, 8, 14)

#: How many days of series the fixture carries — the window `/wellness` shows.
SERIES_DAYS = 28


def at(offset: int) -> dt.date:
    """The date ``offset`` days before :data:`AS_OF`."""
    return AS_OF - dt.timedelta(days=offset)


def document() -> list[DaySample]:
    """Sixty days of a synthetic athlete, one row per day.

    Deliberately uneven: HRV on nine mornings, motivation with holes in it,
    SpO2 never. An athlete who answered everything every day is the one case
    the abstention rendering never has to handle.
    """
    days: list[DaySample] = []
    for offset in range(60):
        fields: dict[str, Any] = {
            # Resting HR every morning, with a real spread to build a band on.
            "resting_hr_bpm": 48 + (offset % 4),
            # Weight most mornings, drifting slowly downwards over the window.
            "weight_kg": round(78.6 - offset * 0.012, 2),
        }
        # HRV on nine of the last three weeks' mornings: enough to draw, not
        # enough to mean anything.
        if offset in (0, 2, 4, 7, 9, 11, 14, 16, 19):
            fields |= {
                "hrv_ms": 54.0 + (offset % 5),
                "hrv_metric": HrvMetric.RMSSD,
                "hrv_context": HrvContext.SLEEPING,
            }
        # Motivation on most days, with two deliberate holes inside the
        # charted window.
        if offset % 5 != 3 and offset not in (5, 6):
            fields["motivation"] = 3 + (offset % 3)
        days.append(
            DaySample(
                day=WellnessDay(local_date=at(offset), **fields),
                # Only the last three days are close enough to their own
                # morning to be report rather than recall.
                subjective_recalled=offset > 2,
            )
        )
    return days


def build() -> WellnessTrend:
    """Run the domain over the document, exactly as the service does."""
    days = document()
    start = at(SERIES_DAYS - 1)
    end = AS_OF + dt.timedelta(days=1)
    computed = {
        marker.metric: trend_for(marker, days, start=start, end=end, on=AS_OF)
        for marker in MARKERS
    }
    return WellnessTrend(
        start=start,
        end=end,
        as_of=AS_OF,
        metrics={metric: computed[metric] for metric in CHARTED},
        readiness=readiness(computed, on=AS_OF),
    )


#: The metrics the fixture carries — the ones `/wellness` charts.
CHARTED: tuple[WellnessMetric, ...] = (
    WellnessMetric.RESTING_HR_BPM,
    WellnessMetric.HRV_RMSSD_MS,
    WellnessMetric.WEIGHT_KG,
    WellnessMetric.SPO2,
    WellnessMetric.MOTIVATION,
)

HEADER = """// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_wellness_trend_fixture.py`
// (`just wellness-trend-fixture`) by running `app.domain.wellness_baseline`
// over a synthetic sixty-day athlete and projecting the result through
// `app.api.routes.wellness.to_trend_read` — the same function the endpoint
// uses. Every mean, band, coefficient of variation, `deviation_sd` and
// maturity verdict below is therefore the domain's own, which a hand-typed
// fixture cannot promise: a baseline over nine readings that carried a mean
// would type-check and describe an answer the API cannot produce, and the
// component test would then agree with the fixture instead of the page.
//
// Regenerate after changing app/domain/wellness_baseline.py or the trend read
// shape, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * One trend read over a 28-day window, as the API serves it.
 *
 * Five metrics, chosen for what they make the page render: a mature banded
 * baseline (`resting_hr_bpm`), a series whose baseline is still immature so
 * the line charts and the band abstains (`hrv_rmssd_ms`), a mature baseline
 * with a trend and no band at all (`weight_kg`), a metric with no readings
 * whatsoever (`spo2`), and a subjective series with holes in it
 * (`motivation`).
 *
 * The dates end on a fixed day so the file is stable in git;
 * `tests/mocks/fixtures.ts` rebases the whole document onto whatever range the
 * component asked for, which moves the calendar and none of the numbers.
 */
export const WELLNESS_TREND: Schemas["WellnessTrendRead"] = """


def main() -> None:
    """Write the generated module."""
    payload = to_trend_read(build()).model_dump(mode="json")
    body = json.dumps(payload, indent=2, sort_keys=True)
    DESTINATION.write_text(HEADER + body + ";\n", encoding="utf-8")
    print(f"wrote {DESTINATION.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
