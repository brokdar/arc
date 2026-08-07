/**
 * What a prescription asks for, per channel — the Today view's Targets panel
 * and the legend beside its profile.
 *
 * Two jobs, one idea. The panel answers "how hard, in numbers I can ride to";
 * the legend answers "which of those numbers is which colour on the plot".
 * Both are folds over the same flattened step list, so a step that is drawn
 * cannot be missing from the table and vice versa.
 *
 * **Nothing here resolves a percentage.** A band's absolute numbers come from
 * the API's already-resolved steps (`PlannedSessionRead.resolved_steps`),
 * which the backend computed against the anchor versions the session *pinned*
 * (D49). There is deliberately no function that multiplies a prescribed
 * percentage by an anchor value, because the only anchor value a client can
 * easily reach is the one in force *now* — and a screen that resolves against
 * "now" silently rewrites every planned session the next time the athlete
 * tests. When nothing resolved, the band stays in the form the plan states it.
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
export type PinnedAnchor = Schemas["PinnedAnchorRead"];
export type ResolvedStep = Schemas["ResolvedStepRead"];

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

/**
 * `FTP`, `LTHR`, `W′` — an anchor as it is written on a prescription.
 *
 * Exported because three modules now name anchors (this one, the criteria
 * translation and the session sheet's provenance line) and a third private
 * copy of the table is a third place to forget a new anchor type.
 */
export function anchorLabel(anchorType: AnchorType): string {
  return ANCHOR_LABELS[anchorType];
}

/** The channel order the Targets panel lists rows in. */
const CHANNEL_ORDER: readonly Channel[] = ["power", "hr", "cadence"];

/**
 * The identity a band is unioned within: one channel *and* one reference.
 *
 * The reference is the whole point. `85 % LTHR` and `75 % max HR` are both
 * heart-rate targets and unioning them into `75–85 % of LTHR` would attribute
 * one prescription's percentage to the other's anchor — a number the plan does
 * not state. An absolute range shares no key with a percentage either: it is
 * measured against nothing, and averaging the two forms would need the anchor
 * that the absolute form deliberately does without.
 */
function bandKey(channel: Channel, target: WireTarget): string {
  return target.kind === "percent_of_anchor"
    ? `${channel}|pct|${target.anchor_type}`
    : `${channel}|abs`;
}

/**
 * The widest band each channel is prescribed anywhere in the session, one row
 * per reference the channel is written against.
 *
 * The union rather than the mode: a ride whose warm-up starts at 50% and whose
 * work sits at 90% is honestly summarised as "50–90% of FTP", and the profile
 * beside it shows where each part falls. A channel prescribed against two
 * different anchors — or in both the percentage and the absolute form — yields
 * a row each, in the order the prescription first states them, because there
 * is no single band that says both.
 */
export function channelBands(
  structure: WorkoutStructure | null | undefined,
): ChannelBand[] {
  if (structure?.discipline !== "cycling") {
    return [];
  }
  // Insertion-ordered, so rows within a channel follow the prescription.
  const bands = new Map<string, ChannelBand>();
  for (const step of flattenSteps(structure.steps)) {
    for (const [channel, target] of targetEntries(step)) {
      const key = bandKey(channel, target);
      bands.set(key, widen(bands.get(key), channel, target));
    }
  }
  const found = [...bands.values()];
  return CHANNEL_ORDER.flatMap((channel) =>
    found.filter((band) => band.channel === channel),
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
  const byZone = new Map<string, LegendEntry>();
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
    // A zone drawn from two different references is two rows, for the reason
    // `bandKey` exists — the colour is shared, the band is not.
    const key = `${bar.zone}|${bandKey(channel, target)}`;
    byZone.set(key, {
      ...widen(byZone.get(key), channel, target),
      zone: bar.zone,
      zoneLabel: ZONE_LABELS[bar.zone],
    });
  });

  const order = Object.keys(ZONE_LABELS) as ZoneTone[];
  const found = [...byZone.values()];
  return order.flatMap((zone) => found.filter((entry) => entry.zone === zone));
}

/** `Power`, `Heart rate`, `Cadence` — the Targets panel's left column. */
export function channelLabel(channel: Channel): string {
  return CHANNEL_LABELS[channel];
}

/**
 * The band exactly as the prescription writes it: `40–122% of FTP`,
 * `120–148 bpm`. A single-valued band collapses to one number.
 *
 * The form that survives an FTP change, and therefore the one that belongs
 * beside every resolved figure rather than instead of it (F2).
 */
export function describePrescribed(band: ChannelBand): string {
  if (band.mode === "absolute") {
    return range(band.low, band.high, CHANNEL_UNITS[band.channel]);
  }
  const label = band.anchorType ? ANCHOR_LABELS[band.anchorType] : "the anchor";
  return `${range(band.low * 100, band.high * 100, "%")} of ${label}`;
}

/** One band's absolute span, as the API already resolved it. */
export interface ResolvedSpan {
  readonly low: number;
  readonly high: number;
  readonly unit: string;
}

/**
 * The absolute numbers a band resolves to, taken from the session's own
 * resolved steps — never computed here.
 *
 * A band is matched to its resolved targets by *anchor version*, not merely by
 * channel: a session prescribing `85 % LTHR` on some steps and `75 % max HR`
 * on others has two heart-rate bands, and each must report the watts-or-beats
 * its own anchor produced. `null` when nothing on that band resolved — an
 * anchor the session did not pin resolves to nothing, which is a legal answer
 * (`app.domain.resolution`) and reads as the prescribed percentage.
 */
export function resolveBand(
  band: ChannelBand,
  anchors: readonly PinnedAnchor[],
  steps: readonly ResolvedStep[],
): ResolvedSpan | null {
  let versionId: string | null = null;
  if (band.mode === "percent") {
    const pinned = anchors.find(
      (anchor) => anchor.anchor_type === band.anchorType,
    );
    if (!pinned) {
      return null;
    }
    versionId = pinned.anchor_version_id;
  }
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  let unit: string | null = null;
  for (const step of steps) {
    for (const target of [...step.start_targets, ...step.end_targets]) {
      if (
        target.channel !== band.channel ||
        target.anchor_version_id !== versionId ||
        target.resolved_low === null ||
        target.resolved_high === null
      ) {
        continue;
      }
      low = Math.min(low, target.resolved_low);
      high = Math.max(high, target.resolved_high);
      unit = target.unit;
    }
  }
  return unit === null ? null : { low, high, unit };
}

/** A resolved span as a person reads it: `100–305 W`, `200 W` for a point. */
export function describeSpan(span: ResolvedSpan): string {
  return range(span.low, span.high, span.unit);
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

/**
 * Grow a band to admit one more target, or start one.
 *
 * Only ever called with targets that share a `bandKey`, so the two ends being
 * merged are the same channel measured against the same reference. Targets
 * that do not share one are separate rows, never an average.
 */
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
  return {
    ...current,
    low: Math.min(current.low, next.low),
    high: Math.max(current.high, next.high),
  };
}
