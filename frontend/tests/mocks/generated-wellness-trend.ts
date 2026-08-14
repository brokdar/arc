// GENERATED FILE — do not hand-edit.
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
export const WELLNESS_TREND: Schemas["WellnessTrendRead"] = {
  as_of: "2026-08-14",
  end: "2026-08-15",
  metrics: {
    hrv_rmssd_ms: {
      baseline: {
        hrv_context: "sleeping",
        kind: "abstention",
        mature: false,
        metric: "hrv_rmssd_ms",
        readings: {
          have: 9,
          need: 14,
          statement: "9 of 14",
        },
        reason:
          "9 of 14 readings over 20 of 28 days: a baseline needs 14 readings spanning 28 days",
        span_days: {
          have: 20,
          need: 28,
          statement: "20 of 28",
        },
      },
      by_context: {
        sleeping: {
          hrv_context: "sleeping",
          kind: "abstention",
          mature: false,
          metric: "hrv_rmssd_ms",
          readings: {
            have: 9,
            need: 14,
            statement: "9 of 14",
          },
          reason:
            "9 of 14 readings over 20 of 28 days: a baseline needs 14 readings spanning 28 days",
          span_days: {
            have: 20,
            need: 28,
            statement: "20 of 28",
          },
        },
      },
      metric: "hrv_rmssd_ms",
      rolling_mean_7d: {
        mean: 4.024926249281948,
        mean_native: 55.97618034591412,
        n: 3,
      },
      series: [
        {
          local_date: "2026-07-18",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-19",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-20",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-21",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-22",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-23",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-24",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-25",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-26",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 58.0,
        },
        {
          local_date: "2026-07-27",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-28",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-29",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 55.0,
        },
        {
          local_date: "2026-07-30",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-31",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 58.0,
        },
        {
          local_date: "2026-08-01",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-02",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-03",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 55.0,
        },
        {
          local_date: "2026-08-04",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-05",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 58.0,
        },
        {
          local_date: "2026-08-06",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-07",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 56.0,
        },
        {
          local_date: "2026-08-08",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-09",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-10",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 58.0,
        },
        {
          local_date: "2026-08-11",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-12",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 56.0,
        },
        {
          local_date: "2026-08-13",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-14",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 54.0,
        },
      ],
      space: "ln",
      today: 54.0,
      unit: "ms",
    },
    motivation: {
      baseline: {
        hrv_context: null,
        kind: "abstention",
        mature: false,
        metric: "motivation",
        readings: {
          have: 3,
          need: 14,
          statement: "3 of 14",
        },
        reason:
          "3 of 14 readings over 3 of 28 days: a baseline needs 14 readings spanning 28 days",
        span_days: {
          have: 3,
          need: 28,
          statement: "3 of 28",
        },
      },
      by_context: {},
      metric: "motivation",
      rolling_mean_7d: {
        mean: 4.0,
        mean_native: 4.0,
        n: 3,
      },
      series: [
        {
          local_date: "2026-07-18",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-07-19",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-07-20",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-07-21",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-07-22",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-23",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-07-24",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-07-25",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-07-26",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-07-27",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-28",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-07-29",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-07-30",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-07-31",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-08-01",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-02",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-08-03",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-08-04",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-08-05",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
        {
          local_date: "2026-08-06",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-07",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-08-08",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-09",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-10",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-08-11",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-12",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 5.0,
        },
        {
          local_date: "2026-08-13",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 4.0,
        },
        {
          local_date: "2026-08-14",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 3.0,
        },
      ],
      space: "linear",
      today: 3.0,
      unit: "1-5",
    },
    resting_hr_bpm: {
      baseline: {
        band: {
          half_width: 0.5637345210021216,
          high: 50.06373452100212,
          high_native: 50.06373452100212,
          low: 48.93626547899788,
          low_native: 48.93626547899788,
        },
        cv: 0.022777152363722086,
        deviation_sd: -0.1900590670807162,
        direction: "within",
        hrv_context: null,
        kind: "banded",
        mature: true,
        mean: 49.5,
        mean_native: 49.5,
        metric: "resting_hr_bpm",
        n: 60,
        sd: 1.1274690420042432,
        space: "linear",
        span_days: 60,
        trend: {
          n: 60,
          per_day: -0.004167824395665463,
          per_week: -0.02917477076965824,
        },
        unit: "bpm",
      },
      by_context: {},
      metric: "resting_hr_bpm",
      rolling_mean_7d: {
        mean: 49.285714285714285,
        mean_native: 49.285714285714285,
        n: 7,
      },
      series: [
        {
          local_date: "2026-07-18",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-07-19",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-07-20",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-07-21",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-07-22",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-07-23",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-07-24",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-07-25",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-07-26",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-07-27",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-07-28",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-07-29",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-07-30",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-07-31",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-08-01",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-08-02",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-08-03",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-08-04",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-08-05",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-08-06",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-08-07",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-08-08",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-08-09",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-08-10",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
        {
          local_date: "2026-08-11",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 51.0,
        },
        {
          local_date: "2026-08-12",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 50.0,
        },
        {
          local_date: "2026-08-13",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 49.0,
        },
        {
          local_date: "2026-08-14",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 48.0,
        },
      ],
      space: "linear",
      today: 48.0,
      unit: "bpm",
    },
    spo2: {
      baseline: {
        hrv_context: null,
        kind: "abstention",
        mature: false,
        metric: "spo2",
        readings: {
          have: 0,
          need: 14,
          statement: "0 of 14",
        },
        reason:
          "0 of 14 readings over 0 of 28 days: a baseline needs 14 readings spanning 28 days",
        span_days: {
          have: 0,
          need: 28,
          statement: "0 of 28",
        },
      },
      by_context: {},
      metric: "spo2",
      rolling_mean_7d: {
        mean: null,
        mean_native: null,
        n: 0,
      },
      series: [
        {
          local_date: "2026-07-18",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-19",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-20",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-21",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-22",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-23",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-24",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-25",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-26",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-27",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-28",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-29",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-30",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-07-31",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-01",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-02",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-03",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-04",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-05",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-06",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-07",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-08",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-09",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-10",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-11",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-12",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-13",
          markers: null,
          value: null,
        },
        {
          local_date: "2026-08-14",
          markers: null,
          value: null,
        },
      ],
      space: "linear",
      today: null,
      unit: "fraction",
    },
    weight_kg: {
      baseline: {
        cv: 0.0026800963261274092,
        hrv_context: null,
        kind: "trend",
        mature: true,
        mean: 78.24600000000001,
        mean_native: 78.24600000000001,
        metric: "weight_kg",
        n: 60,
        sd: 0.20970681713416528,
        space: "linear",
        span_days: 60,
        trend: {
          n: 60,
          per_day: 0.012006668519033046,
          per_week: 0.08404667963323131,
        },
        unit: "kg",
      },
      by_context: {},
      metric: "weight_kg",
      rolling_mean_7d: {
        mean: 78.56428571428572,
        mean_native: 78.56428571428572,
        n: 7,
      },
      series: [
        {
          local_date: "2026-07-18",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.28,
        },
        {
          local_date: "2026-07-19",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.29,
        },
        {
          local_date: "2026-07-20",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.3,
        },
        {
          local_date: "2026-07-21",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.31,
        },
        {
          local_date: "2026-07-22",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.32,
        },
        {
          local_date: "2026-07-23",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.34,
        },
        {
          local_date: "2026-07-24",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.35,
        },
        {
          local_date: "2026-07-25",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.36,
        },
        {
          local_date: "2026-07-26",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.37,
        },
        {
          local_date: "2026-07-27",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.38,
        },
        {
          local_date: "2026-07-28",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.4,
        },
        {
          local_date: "2026-07-29",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.41,
        },
        {
          local_date: "2026-07-30",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.42,
        },
        {
          local_date: "2026-07-31",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.43,
        },
        {
          local_date: "2026-08-01",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.44,
        },
        {
          local_date: "2026-08-02",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.46,
        },
        {
          local_date: "2026-08-03",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.47,
        },
        {
          local_date: "2026-08-04",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.48,
        },
        {
          local_date: "2026-08-05",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.49,
        },
        {
          local_date: "2026-08-06",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.5,
        },
        {
          local_date: "2026-08-07",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.52,
        },
        {
          local_date: "2026-08-08",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.53,
        },
        {
          local_date: "2026-08-09",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.54,
        },
        {
          local_date: "2026-08-10",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.55,
        },
        {
          local_date: "2026-08-11",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.56,
        },
        {
          local_date: "2026-08-12",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.58,
        },
        {
          local_date: "2026-08-13",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.59,
        },
        {
          local_date: "2026-08-14",
          markers: {
            actionable: true,
            invalidated_by: [],
            statement: "recorded",
          },
          value: 78.6,
        },
      ],
      space: "linear",
      today: 78.6,
      unit: "kg",
    },
  },
  readiness: {
    as_of: "2026-08-14",
    markers_outside_band: {
      count: 0,
      markers: [],
      of: 1,
      statement: "0 of 1",
    },
  },
  start: "2026-07-18",
};
