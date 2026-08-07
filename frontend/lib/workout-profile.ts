import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

export type SteadyStep = Schemas["SteadyStepSchema"];
export type RampStep = Schemas["RampStepSchema"];
export type RepeatBlock = Schemas["RepeatBlockSchema-Output"];
export type WorkoutStep = SteadyStep | RampStep | RepeatBlock;
export type EnduranceStructure = Schemas["EnduranceStructureSchema-Output"];
export type StrengthStructure = Schemas["StrengthStructureSchema"];
export type WorkoutStructure = EnduranceStructure | StrengthStructure;
type Target = Schemas["PercentOfAnchorSchema"] | Schemas["AbsoluteRangeSchema"];
type StepRole = Schemas["StepRole"];

/** One bar of the profile: how wide, how tall, what colour. */
export interface ProfileBar {
  /** Flex weight — the step's share of the session's time. */
  readonly weight: number;
  /** Height as a fraction of the plot, 0–1. */
  readonly height: number;
  /** The zone-ramp token this bar is painted with. */
  readonly zone: ZoneTone;
  /** Seconds, when the step declares a duration. For tooltips and tests. */
  readonly durationS: number | null;
  /** The step's role, so a caller can label or group without re-flattening. */
  readonly role: StepRole;
}

export type ZoneTone = "z1" | "z2" | "z3" | "z4" | "z5" | "z6" | "z7";

/**
 * Coggan 7 lower bounds as fractions of FTP.
 *
 * Mirrors `_ZONE_SCHEMES[ZoneModel.COGGAN_7]` in
 * `backend/app/domain/zones.py` — if you change one, change both. The bands
 * are half-open the same way the backend's are: `lower <= x < next lower`.
 */
export const COGGAN_7_LOWER: readonly number[] = [
  0, 0.55, 0.75, 0.9, 1.05, 1.2, 1.5,
];

/**
 * What each band of the ramp is called, for a legend or a headline.
 *
 * `Z1`…`Z7` rather than "recovery"/"threshold": the boundaries are the
 * backend's, but a bar painted from a *prescribed* percentage is still a
 * display bucket rather than the athlete's measured time in zone, and naming
 * it after a physiological state would claim more than the arithmetic
 * supports.
 */
export const ZONE_LABELS: Readonly<Record<ZoneTone, string>> = {
  z1: "Z1",
  z2: "Z2",
  z3: "Z3",
  z4: "Z4",
  z5: "Z5",
  z6: "Z6",
  z7: "Z7",
};

/** CSS custom properties for the zone ramp, in intensity order. */
export const ZONE_COLORS: Readonly<Record<ZoneTone, string>> = {
  z1: "var(--color-zone-1)",
  z2: "var(--color-zone-2)",
  z3: "var(--color-zone-3)",
  z4: "var(--color-zone-4)",
  z5: "var(--color-zone-5)",
  z6: "var(--color-zone-6)",
  z7: "var(--color-zone-7)",
};

/**
 * Intensity a step is assumed to sit at when it prescribes no target at all.
 *
 * A rest step between intervals has no power band — it is defined by being
 * off the gas — but the profile still has to draw it, and drawing it at zero
 * would make the bar disappear. These are display defaults only: nothing
 * scores against them.
 */
const ROLE_FALLBACK_INTENSITY: Readonly<Record<StepRole, number>> = {
  warmup: 0.5,
  work: 0.75,
  recovery: 0.4,
  rest: 0.25,
  cooldown: 0.4,
};

/** A step with no stated duration still needs a width. One minute of it. */
const ASSUMED_STEP_SECONDS = 60;

/**
 * The top of the plot, as a fraction of the anchor.
 *
 * Fixing it at 1.25 (rather than scaling to the workout's own maximum) is what
 * makes profiles comparable across cards: a recovery spin looks flat next to a
 * VO₂ session because it *is* flat, not because each was normalised to itself.
 * A workout that goes above 125% widens the ceiling so nothing clips.
 */
const PLOT_CEILING = 1.25;

/** Bars never render thinner than this, however short the step. */
const MIN_HEIGHT = 0.08;

/**
 * Flatten a step tree into the horizontal bar profile the cards and the
 * session sheet draw.
 *
 * Repeats expand (4× a 5-minute block is eight bars, work/rest alternating);
 * ramps stay one bar drawn at the mean of their two ends, because a ramp is
 * one instruction to the athlete and splitting it would suggest steps that are
 * not prescribed. Consecutive identical bars are *not* merged — the mockup's
 * VO₂ profile is legible precisely because you can count the intervals.
 */
export function profileBars(
  structure: WorkoutStructure | null | undefined,
): ProfileBar[] {
  if (structure?.discipline !== "cycling") {
    return [];
  }
  const flat = flattenSteps(structure.steps);
  if (flat.length === 0) {
    return [];
  }

  const intensities = flat.map(stepIntensity);
  // Absolute targets (250 W) and relative ones (0.95 × FTP) cannot be compared
  // without the anchor, which a calendar card does not have. Scale the
  // absolute ones so the workout's hardest absolute step sits at the plot
  // ceiling; a workout that mixes both is rare and reads correctly either way.
  const maxAbsolute = intensities.reduce(
    (max, i) => (i.kind === "absolute" ? Math.max(max, i.value) : max),
    0,
  );
  const fractions = intensities.map((i) =>
    i.kind === "fraction"
      ? i.value
      : maxAbsolute > 0
        ? (i.value / maxAbsolute) * PLOT_CEILING
        : 0,
  );

  const ceiling = Math.max(PLOT_CEILING, ...fractions);

  return flat.map((step, index) => {
    const fraction = fractions[index] ?? 0;
    const durationS = step.duration_s ?? null;
    return {
      weight: durationS ?? ASSUMED_STEP_SECONDS,
      height: Math.min(1, Math.max(MIN_HEIGHT, fraction / ceiling)),
      zone: zoneToneFor(fraction),
      durationS,
      role: step.role,
    };
  });
}

/**
 * The zone band a relative intensity falls in.
 *
 * Boundaries are the backend's `coggan_7`, so a step painted Z5 on a card is
 * the zone the backend would name for the same fraction. That needs no anchor
 * fetch: a `PercentOfAnchor` target is *already* a fraction of FTP, so the
 * only thing an anchor would add is the watts — which is why the absolute
 * path above scales against the workout's own hardest step instead.
 */
export function zoneToneFor(fraction: number): ZoneTone {
  let index = 0;
  for (let z = COGGAN_7_LOWER.length - 1; z >= 0; z -= 1) {
    if (fraction >= (COGGAN_7_LOWER[z] as number)) {
      index = z;
      break;
    }
  }
  return `z${index + 1}` as ZoneTone;
}

/** Expand repeats; leave steady steps and ramps as single entries. */
export function flattenSteps(
  steps: readonly WorkoutStep[],
): (SteadyStep | RampStep)[] {
  const out: (SteadyStep | RampStep)[] = [];
  for (const step of steps) {
    if (step.kind === "repeat") {
      for (let n = 0; n < step.times; n += 1) {
        out.push(...flattenSteps(step.children));
      }
    } else {
      out.push(step);
    }
  }
  return out;
}

/** Total prescribed seconds of a step tree, repeats expanded. */
export function totalDurationS(
  structure: WorkoutStructure | null | undefined,
): number | null {
  if (structure?.discipline !== "cycling") {
    return null;
  }
  const durations = flattenSteps(structure.steps).map((s) => s.duration_s ?? 0);
  const total = durations.reduce((sum, d) => sum + d, 0);
  return total > 0 ? total : null;
}

/** Total prescribed sets of a strength structure. */
export function totalSets(
  structure: WorkoutStructure | null | undefined,
): number | null {
  if (structure?.discipline !== "strength") {
    return null;
  }
  return structure.groups.reduce(
    (sum, group) => sum + group.items.reduce((s, item) => s + item.sets, 0),
    0,
  );
}

type Intensity =
  | { kind: "fraction"; value: number }
  | { kind: "absolute"; value: number };

/**
 * How hard one step is, from its dominant channel.
 *
 * Power wins over heart rate wins over cadence: power is the channel a bike
 * prescription is written in, and a cadence-only step (a technique drill) is
 * drawn at its role's default rather than at "95 rpm out of what".
 */
function stepIntensity(step: SteadyStep | RampStep): Intensity {
  const targets =
    step.kind === "ramp"
      ? meanTargets(step.start_targets, step.end_targets)
      : (step.targets ?? {});

  for (const channel of ["power", "hr"] as const) {
    const target = targets[channel];
    if (target) {
      return target.kind === "percent_of_anchor"
        ? { kind: "fraction", value: (target.pct_low + target.pct_high) / 2 }
        : { kind: "absolute", value: (target.low + target.high) / 2 };
    }
  }
  return { kind: "fraction", value: ROLE_FALLBACK_INTENSITY[step.role] };
}

/** A ramp is drawn at the midpoint of its two ends — one instruction, one bar. */
function meanTargets(
  start: Record<string, Target>,
  end: Record<string, Target>,
): Record<string, Target> {
  const merged: Record<string, Target> = {};
  for (const [channel, from] of Object.entries(start)) {
    const to = end[channel];
    if (!to || to.kind !== from.kind) {
      merged[channel] = from;
    } else if (
      from.kind === "percent_of_anchor" &&
      to.kind === "percent_of_anchor"
    ) {
      merged[channel] = {
        ...from,
        pct_low: (from.pct_low + to.pct_low) / 2,
        pct_high: (from.pct_high + to.pct_high) / 2,
      };
    } else if (from.kind === "absolute" && to.kind === "absolute") {
      merged[channel] = {
        ...from,
        low: (from.low + to.low) / 2,
        high: (from.high + to.high) / 2,
      };
    }
  }
  return merged;
}
