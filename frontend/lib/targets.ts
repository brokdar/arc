/**
 * What a prescription asks for, per channel — the Today view's Targets panel
 * and the legend beside its profile.
 *
 * Two jobs, one idea. The panel answers "how hard, in numbers I can ride to";
 * the legend answers "which of those numbers is which colour on the plot".
 * Both are folds over the same flattened step list, so a step that is drawn
 * cannot be missing from the table and vice versa.
 *
 * Percentages are resolved to absolute values **only when the anchor they name
 * is known**, and the resolved form always says which anchor it came from. A
 * band shown as `165–190 W` when nobody has entered an FTP would be a number
 * the application made up.
 */

import type { components } from "@/generated/api/schema";

import {
  flattenSteps,
  profileBars,
  type RampStep,
  type SteadyStep,
  type WorkoutStructure,
  ZONE_LABELS,
  type ZoneTone,
} from "@/lib/workout-profile";

type Schemas = components["schemas"];
type WireTarget =
  | Schemas["PercentOfAnchorSchema"]
  | Schemas["AbsoluteRangeSchema"];
export type Channel = Schemas["Channel"];
export type AnchorType = Schemas["AnchorType"];

/** The anchor values in force, as far as the caller managed to fetch them. */
export type AnchorValues = Partial<Record<AnchorType, number>>;

/** One channel's band across the whole prescription. */
export interface ChannelBand {
  readonly channel: Channel;
  readonly mode: "percent" | "absolute";
  /** Present in `percent` mode. */
  readonly anchorType: AnchorType | null;
  /** Fractions in `percent` mode, the channel's own unit in `absolute`. */
  readonly low: number;
  readonly high: number;
}

/** One row of the legend under the profile: a band and the colour it is drawn in. */
export interface LegendEntry extends ChannelBand {
  readonly zone: ZoneTone;
  readonly zoneLabel: string;
}

const CHANNEL_LABELS: Readonly<Record<Channel, string>> = {
  power: "Power",
  hr: "Heart rate",
  cadence: "Cadence",
};

const CHANNEL_UNITS: Readonly<Record<Channel, string>> = {
  power: "W",
  hr: "bpm",
  cadence: "rpm",
};

const ANCHOR_LABELS: Readonly<Record<AnchorType, string>> = {
  ftp: "FTP",
  lthr: "LTHR",
  max_hr: "max HR",
  cp: "CP",
  w_prime: "W′",
};

/** The channel order the Targets panel lists rows in. */
const CHANNEL_ORDER: readonly Channel[] = ["power", "hr", "cadence"];

/**
 * The widest band each channel is prescribed anywhere in the session.
 *
 * The union rather than the mode: a ride whose warm-up starts at 50% and whose
 * work sits at 90% is honestly summarised as "50–90% of FTP", and the profile
 * beside it shows where each part falls. Channels prescribed in both forms in
 * one session (rare) report the absolute form, which needs no anchor to read.
 */
export function channelBands(
  structure: WorkoutStructure | null | undefined,
): ChannelBand[] {
  if (structure?.discipline !== "cycling") {
    return [];
  }
  const bands = new Map<Channel, ChannelBand>();
  for (const step of flattenSteps(structure.steps)) {
    for (const [channel, target] of targetEntries(step)) {
      bands.set(channel, widen(bands.get(channel), channel, target));
    }
  }
  return CHANNEL_ORDER.filter((channel) => bands.has(channel)).map(
    // Non-null by the filter; narrowed so the map stays total.
    (channel) => bands.get(channel) as ChannelBand,
  );
}

/**
 * One legend row per band of the ramp the profile actually uses.
 *
 * Zipped with `profileBars` rather than recomputed: the bars and the flattened
 * steps are the same list in the same order, so the legend cannot colour a
 * band differently from the plot it explains.
 */
export function profileLegend(
  structure: WorkoutStructure | null | undefined,
): LegendEntry[] {
  if (structure?.discipline !== "cycling") {
    return [];
  }
  const steps = flattenSteps(structure.steps);
  const bars = profileBars(structure);
  const byZone = new Map<ZoneTone, ChannelBand>();
  steps.forEach((step, index) => {
    const bar = bars[index];
    if (!bar) {
      return;
    }
    // Power is what a ride is prescribed in; heart rate stands in when it is
    // the only thing the step states. A cadence-only drill has no intensity
    // to explain, so it contributes no legend row.
    const entry =
      targetEntries(step).find(([channel]) => channel === "power") ??
      targetEntries(step).find(([channel]) => channel === "hr");
    if (!entry) {
      return;
    }
    const [channel, target] = entry;
    byZone.set(bar.zone, widen(byZone.get(bar.zone), channel, target));
  });

  const order = Object.keys(ZONE_LABELS) as ZoneTone[];
  return order
    .filter((zone) => byZone.has(zone))
    .map((zone) => ({
      // Non-null by the filter above.
      ...(byZone.get(zone) as ChannelBand),
      zone,
      zoneLabel: ZONE_LABELS[zone],
    }));
}

/** `Power`, `Heart rate`, `Cadence` — the Targets panel's left column. */
export function channelLabel(channel: Channel): string {
  return CHANNEL_LABELS[channel];
}

/**
 * A band as a person reads it: `165–190 W`, or `88–94% of FTP` when the anchor
 * is unknown. A single-valued band collapses to one number.
 */
export function describeBand(band: ChannelBand, anchors: AnchorValues): string {
  const unit = CHANNEL_UNITS[band.channel];
  if (band.mode === "absolute") {
    return range(band.low, band.high, unit);
  }
  const anchorValue = band.anchorType ? anchors[band.anchorType] : undefined;
  if (anchorValue === undefined) {
    const label = band.anchorType
      ? ANCHOR_LABELS[band.anchorType]
      : "the anchor";
    return `${range(band.low * 100, band.high * 100, "%")} of ${label}`;
  }
  return range(
    Math.round(band.low * anchorValue),
    Math.round(band.high * anchorValue),
    unit,
  );
}

/** The `% of FTP` a resolved band came from, for the line beneath it. */
export function describeBandSource(
  band: ChannelBand,
  anchors: AnchorValues,
): string | null {
  if (band.mode !== "percent" || !band.anchorType) {
    return null;
  }
  if (anchors[band.anchorType] === undefined) {
    return null;
  }
  return `${range(band.low * 100, band.high * 100, "%")} of ${
    ANCHOR_LABELS[band.anchorType]
  }`;
}

function range(low: number, high: number, unit: string): string {
  const a = Math.round(low);
  const b = Math.round(high);
  return a === b
    ? `${a} ${unit}`.replace(" %", "%")
    : `${a}–${b} ${unit}`.replace(" %", "%");
}

/** The per-channel targets a step states — a ramp reports both of its ends. */
function targetEntries(step: SteadyStep | RampStep): [Channel, WireTarget][] {
  const sources =
    step.kind === "ramp"
      ? [step.start_targets, step.end_targets]
      : [step.targets ?? {}];
  const entries: [Channel, WireTarget][] = [];
  for (const source of sources) {
    for (const [channel, target] of Object.entries(source)) {
      entries.push([channel as Channel, target]);
    }
  }
  return entries;
}

/** Grow a band to admit one more target, or start one. */
function widen(
  current: ChannelBand | undefined,
  channel: Channel,
  target: WireTarget,
): ChannelBand {
  const next: ChannelBand =
    target.kind === "percent_of_anchor"
      ? {
          channel,
          mode: "percent",
          anchorType: target.anchor_type,
          low: target.pct_low,
          high: target.pct_high,
        }
      : {
          channel,
          mode: "absolute",
          anchorType: null,
          low: target.low,
          high: target.high,
        };
  if (!current) {
    return next;
  }
  // Two forms of the same channel cannot be unioned without the anchor, so
  // the absolute one wins: it reads correctly with nothing else known.
  if (current.mode !== next.mode) {
    return current.mode === "absolute" ? current : next;
  }
  return {
    ...current,
    low: Math.min(current.low, next.low),
    high: Math.max(current.high, next.high),
  };
}
