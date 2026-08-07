import type { components } from "@/generated/api/schema";
import { formatDurationClock } from "@/lib/format";

type Schemas = components["schemas"];

export type SessionMetrics = Schemas["SessionMetricsRead"];
export type Metric = Schemas["MetricRead"];
export type MetricExplanation = Schemas["ExplanationRead"];
export type TimeInZone = Schemas["TimeInZoneRead"];
export type SessionLoad = Schemas["LoadRead"];
export type StrengthMetrics = Schemas["StrengthRead"];
export type DetectedInterval = Schemas["IntervalRead"];
export type AnchorPin = Schemas["AnchorPinRead"];
export type SessionStreams = Schemas["SessionStreamsRead"];
export type StreamChannel = Schemas["StreamChannel"];
export type LoadBasis = Schemas["LoadBasis"];
export type AnchorType = Schemas["AnchorType"];

/**
 * A metric slot, resolved into the one thing a component has to know.
 *
 * The API guarantees exactly one of `value` / `not_assessed` is set, and the
 * whole point of the shape is that a component branches **once**: it either
 * renders a number with its explanation or renders `NotAssessed` with the
 * reason, in the same slot either way (UI convention 4). This narrows the
 * union so that branch is a discriminated one rather than three null checks
 * that could disagree.
 */
export type ResolvedMetric =
  | {
      readonly kind: "value";
      readonly value: number;
      readonly explanation: MetricExplanation | null;
    }
  | { readonly kind: "absent"; readonly reason: string };

/** Narrow one metric slot. A malformed slot reads as absent, never as zero. */
export function resolve(metric: Metric | null | undefined): ResolvedMetric {
  if (metric && metric.value !== null && metric.value !== undefined) {
    return {
      kind: "value",
      value: metric.value,
      explanation: metric.explanation ?? null,
    };
  }
  return {
    kind: "absent",
    reason: metric?.not_assessed ?? "This metric was not computed.",
  };
}

/** The number, or `null`. For arithmetic; render through `resolve`. */
export function metricValue(metric: Metric | null | undefined): number | null {
  const resolved = resolve(metric);
  return resolved.kind === "value" ? resolved.value : null;
}

/** Round a metric to `digits`, or hand back the reason it has none. */
export function formatMetric(metric: Metric, digits = 0): string | null {
  const resolved = resolve(metric);
  return resolved.kind === "value" ? resolved.value.toFixed(digits) : null;
}

/**
 * A5.2's counterfactual, as a sentence — or `null` when there is no other
 * model to compare against.
 *
 * *"Load 79, from power. Had power been unavailable, the heart-rate model
 * would have given 75."* It is composed here rather than sent by the API
 * because it is a rendering of two numbers the API does send, and a server
 * that shipped the prose would be shipping copy the page cannot restyle.
 */
export function loadCounterfactual(load: SessionLoad): string | null {
  const power = load.power_load ?? null;
  const hr = load.hr_load ?? null;
  const basis = load.load_basis ?? null;
  if (power === null || hr === null || basis === null) {
    return null;
  }
  return basis === "power"
    ? `Had power been unavailable, the heart-rate model would have given ${Math.round(hr)}.`
    : `Had the heart-rate model been unavailable, power would have given ${Math.round(power)}.`;
}

/** How each load basis reads in a sentence. */
export const LOAD_BASIS_LABELS: Readonly<Record<LoadBasis, string>> = {
  power: "power",
  hr: "heart rate",
};

/**
 * The zone-ramp token one band of a zone model paints with.
 *
 * The seven-zone power model maps one-to-one onto the ramp. The five-zone
 * heart-rate model maps onto the **same** ramp so a zone bar reads the same
 * way whichever channel drew it — first and last zone take the endpoints and
 * the rest spread evenly, exactly as `globals.css` documents the rule
 * (Z1→zone-1, Z2→zone-2, Z3→zone-4, Z4→zone-5, Z5→zone-7).
 */
const HR_ZONE_RAMP: readonly number[] = [1, 2, 4, 5, 7];

export function zoneRampIndex(
  zoneModel: Schemas["ZoneModel"] | null,
  index: number,
): number {
  if (zoneModel === "lthr_5") {
    return HR_ZONE_RAMP[index - 1] ?? index;
  }
  return index;
}

/** One band of a rendered zone bar. */
export interface ZoneBand {
  readonly index: number;
  readonly name: string;
  readonly seconds: number;
  /** Share of the banded total, 0–1. Zero when nothing fell in the band. */
  readonly fraction: number;
  /** The `--color-zone-*` custom property this band is painted with. */
  readonly color: string;
  /** What the band says on hover: zone, time and share. */
  readonly title: string;
}

/**
 * A zone distribution, prepared for the bar.
 *
 * Every band of the model is kept, including the empty ones: a zone with no
 * time in it is a fact about the ride, and dropping it would make the bar's
 * shape depend on the data rather than on the model.
 */
export function zoneBands(distribution: TimeInZone): ZoneBand[] {
  const total = distribution.total_s ?? 0;
  return distribution.zones.map((zone) => {
    const fraction = total > 0 ? zone.seconds / total : 0;
    return {
      index: zone.index,
      name: zone.name,
      seconds: zone.seconds,
      fraction,
      color: `var(--color-zone-${zoneRampIndex(distribution.zone_model ?? null, zone.index)})`,
      title: `Z${zone.index} ${zone.name} · ${formatDurationClock(zone.seconds)} · ${(fraction * 100).toFixed(1)}%`,
    };
  });
}

/** How each anchor type is written in a pin line. */
export const PIN_UNITS: Readonly<Record<AnchorType, string>> = {
  ftp: "W",
  lthr: "bpm",
  max_hr: "bpm",
  resting_hr: "bpm",
  cp: "W",
  w_prime: "J",
};

/** The pin of one anchor type, or `undefined` when none was in force. */
export function pinOf(
  metrics: SessionMetrics,
  anchorType: AnchorType,
): AnchorPin | undefined {
  return metrics.pins.find((pin) => pin.anchor_type === anchorType);
}

/** One channel's column out of a stream payload, or `undefined`. */
export function channelValues(
  streams: SessionStreams,
  channel: StreamChannel,
): (number | null)[] | undefined {
  return streams.channels.find((entry) => entry.channel === channel)?.values;
}

/** What a drag-selection reports for the rows it covers. */
export interface SelectionStats {
  readonly durationS: number;
  readonly averagePower: number | null;
  readonly normalizedPower: number | null;
  readonly averageHr: number | null;
  readonly averageCadence: number | null;
}

/** The rolling window Coggan's normalized power is defined over, in seconds. */
export const NP_WINDOW_S = 30;

/**
 * Normalized power over a slice, by the same definition the backend uses.
 *
 * A deliberate second implementation, and the only one in this codebase:
 * a drag-selection is a range nobody asked the server about, and a round trip
 * per pixel of drag is not a design. It is the *same arithmetic* —
 * `app.domain.metrics.normalized_power`, 30 s trailing rolling mean, fourth
 * power, mean, fourth root, leading samples averaged over a shorter window —
 * and `lib/metrics.test.ts` pins it against the backend's own committed
 * fixtures so the two cannot drift silently. Nothing is ever stored from it:
 * the artefact's numbers are always the server's.
 */
export function normalizedPower(watts: readonly number[]): number | null {
  if (watts.length === 0) {
    return null;
  }
  let running = 0;
  let fourthPowerSum = 0;
  for (let index = 0; index < watts.length; index += 1) {
    running += watts[index];
    if (index >= NP_WINDOW_S) {
      running -= watts[index - NP_WINDOW_S];
    }
    const mean = running / Math.min(index + 1, NP_WINDOW_S);
    fourthPowerSum += mean ** 4;
  }
  return (fourthPowerSum / watts.length) ** 0.25;
}

function mean(values: readonly (number | null)[]): number | null {
  const present = values.filter((value): value is number => value !== null);
  return present.length === 0
    ? null
    : present.reduce((total, value) => total + value, 0) / present.length;
}

/**
 * Statistics for the rows a selection covers, computed in the browser.
 *
 * Null rows are **excluded**, never read as zero — the same rule the backend
 * applies, and the reason a selection across a coffee stop reports the riding
 * either side of it rather than an average dragged toward nothing.
 */
export function selectionStats(
  streams: SessionStreams,
  from: number,
  to: number,
): SelectionStats {
  const start = Math.max(0, Math.min(from, to));
  const end = Math.min(streams.length, Math.max(from, to) + 1);
  const slice = (channel: StreamChannel) =>
    (channelValues(streams, channel) ?? []).slice(start, end);
  const watts = slice("power").filter(
    (value): value is number => value !== null,
  );
  return {
    durationS: Math.max(0, end - start),
    averagePower: mean(slice("power")),
    normalizedPower: normalizedPower(watts),
    averageHr: mean(slice("hr")),
    averageCadence: mean(slice("cadence")),
  };
}
