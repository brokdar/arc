"use client";

import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { WellnessTrajectoryChart } from "@/components/wellness/wellness-trajectory-chart";
import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

type Trend = Schemas["WellnessTrendRead"];
type MetricTrend = Schemas["MetricTrendRead"];
type Baseline = MetricTrend["baseline"];

/**
 * The metrics `/wellness` charts, in read order, with what to call each.
 *
 * A short list on purpose: the page is answering "what is normal for me and
 * where am I against it", and thirteen plots is a wall rather than an answer.
 * The rest are a `GET /wellness/trend?metric=…` away.
 */
export const CHARTED_METRICS: readonly {
  metric: Schemas["WellnessMetric"];
  label: string;
}[] = [
  { metric: "resting_hr_bpm", label: "Resting HR" },
  { metric: "hrv_rmssd_ms", label: "HRV (RMSSD, sleeping)" },
  { metric: "weight_kg", label: "Weight" },
  { metric: "spo2", label: "Blood oxygen" },
  { metric: "motivation", label: "Motivation" },
];

export interface WellnessTrajectoriesProps {
  readonly trend: Trend | undefined;
}

/**
 * The trajectory half of `/wellness`: one metric per panel, and its standing.
 *
 * **An immature baseline renders `NotAssessed` with the API's own sentence**,
 * never a dash and never a number with a caveat beside it. That is the whole
 * point of the page: `54` is alarming for one athlete and a Tuesday for
 * another, so a number the athlete cannot read against their own normal is
 * shown *as* a number and nothing more, with the reason it cannot yet be more
 * stated where the band would have been. Copying "not enough data" into this
 * component would put a second copy of the maturity rule in the UI, which is
 * exactly what serving `reason` exists to prevent.
 *
 * A metric with readings but no baseline still charts — nine mornings are
 * worth looking at, they are just not worth a normal range. A metric with no
 * readings at all charts nothing: an empty plot is a claim that there was
 * something to plot.
 */
export function WellnessTrajectories({ trend }: WellnessTrajectoriesProps) {
  if (trend === undefined) {
    return null;
  }
  return (
    <section className="flex flex-col gap-[18px]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <SectionLabel level={2}>Trajectory</SectionLabel>
        <ReadinessSummary readiness={trend.readiness} />
      </div>
      {CHARTED_METRICS.map(({ metric, label }) => {
        const found = trend.metrics[metric];
        return found === undefined ? null : (
          <MetricPanel key={metric} label={label} trend={found} />
        );
      })}
    </section>
  );
}

/**
 * How many markers sit outside their own band, and which.
 *
 * A count with names, and deliberately no verdict: the denominator excludes
 * markers whose baseline is immature and says so, because two of five reads
 * calmer than two of two and only one of them is true. Whether today is a day
 * to train is not this page's sentence to write.
 */
function ReadinessSummary({
  readiness,
}: {
  readiness: Schemas["ReadinessRead"];
}) {
  const outside = readiness.markers_outside_band;
  return (
    <p
      data-testid="wellness-readiness"
      className="font-mono text-2xs text-ink-muted"
    >
      {outside.statement} markers outside their normal band
      {outside.markers.length === 0
        ? null
        : `: ${outside.markers
            .map((marker) => `${marker.metric} ${marker.direction}`)
            .join(", ")}`}
      {readiness.joint_state ? ` · ${readiness.joint_state.label}` : null}
    </p>
  );
}

function MetricPanel({ label, trend }: { label: string; trend: MetricTrend }) {
  const values = trend.series.map((point) => point.value);
  const dates = trend.series.map((point) => point.local_date);
  const recorded = values.filter((value) => value !== null).length;
  const band =
    trend.baseline.kind === "banded"
      ? {
          low: trend.baseline.band.low_native,
          high: trend.baseline.band.high_native,
        }
      : null;

  return (
    <Panel
      className="flex flex-col gap-2 px-5 py-4"
      aria-label={label}
      role="region"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <SectionLabel level={3}>{label}</SectionLabel>
        <BaselineStanding baseline={trend.baseline} unit={trend.unit} />
        <span className="ml-auto font-mono text-2xs text-ink-faint">
          7-day mean{" "}
          {trend.rolling_mean_7d.mean_native === null
            ? "—"
            : round(trend.rolling_mean_7d.mean_native)}{" "}
          · n {trend.rolling_mean_7d.n}
        </span>
      </div>
      {recorded === 0 ? null : (
        <WellnessTrajectoryChart
          label={label}
          unit={trend.unit}
          dates={dates}
          values={values}
          band={band}
        />
      )}
    </Panel>
  );
}

/** What the baseline says, or why it says nothing yet. */
function BaselineStanding({
  baseline,
  unit,
}: {
  baseline: Baseline;
  unit: string;
}) {
  if (baseline.kind === "abstention") {
    // The reason is the API's sentence, verbatim. A string typed here would be
    // a second copy of a rule that lives in the domain.
    return <NotAssessed reason={baseline.reason} />;
  }
  if (baseline.kind === "trend") {
    return (
      <span className="font-mono text-2xs text-ink-faint">
        {round(baseline.mean_native)} {unit} · {signed(baseline.trend.per_week)}{" "}
        {unit} per week · n {baseline.n}
      </span>
    );
  }
  return (
    <span className="font-mono text-2xs text-ink-faint">
      {round(baseline.mean_native)} {unit} ·{" "}
      {baseline.deviation_sd === null
        ? "no deviation"
        : `${signed(baseline.deviation_sd)} SD ${baseline.direction ?? ""}`}{" "}
      · n {baseline.n}
    </span>
  );
}

/** Enough decimals to be readable at any of these magnitudes. */
function round(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 100) {
    return value.toFixed(0);
  }
  return magnitude >= 1 ? value.toFixed(1) : value.toFixed(3);
}

/** A signed figure, so a fall reads as a fall. */
function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${round(Math.abs(value))}`;
}
