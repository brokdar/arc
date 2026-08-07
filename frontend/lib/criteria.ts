import type { components } from "@/generated/api/schema";

import { formatDurationClock, formatPercent } from "@/lib/format";
import { anchorLabel } from "@/lib/targets";

type Schemas = components["schemas"];

/** The tagged union of machine-checkable success criteria (build plan WP-2.7). */
export type SuccessCriterion =
  | Schemas["TimeInBandSchema"]
  | Schemas["DurationFloorSchema"]
  | Schemas["CeilingSchema"]
  | Schemas["SetsCompletedSchema"]
  | Schemas["LoadWithinSchema"];

const CHANNEL_LABELS: Record<Schemas["Channel"], string> = {
  power: "power",
  hr: "heart rate",
  cadence: "cadence",
};

const STEP_ROLE_LABELS: Record<Schemas["StepRole"], string> = {
  warmup: "warm-up",
  work: "work",
  recovery: "recovery",
  rest: "rest",
  cooldown: "cool-down",
};

/**
 * `warm-up`, `cool-down` — a step's role in the words a plan uses.
 *
 * Not `step.role` prettified: `warmup` and `cooldown` are one word on the
 * wire and two in English, and a component that title-cases the slug renders
 * "Warmup".
 */
export function stepRoleLabel(role: Schemas["StepRole"]): string {
  return STEP_ROLE_LABELS[role];
}

/**
 * One criterion as a sentence an athlete can check themselves against.
 *
 * The wire format is a tagged union chosen to be *evaluable* — selectors,
 * fractions, anchor references — which makes it unreadable as-is. This is the
 * only place that translation lives, so the calendar sheet, the creator's
 * criteria editor (slice 2) and any later verdict screen all phrase a
 * criterion the same way.
 */
export function describeCriterion(criterion: SuccessCriterion): string {
  switch (criterion.kind) {
    case "time_in_band": {
      const channel = CHANNEL_LABELS[criterion.band.channel];
      const low = formatPercent(criterion.band.low);
      const high = formatPercent(criterion.band.high);
      return `${formatPercent(criterion.min_fraction)} of ${describeSelector(
        criterion.selector,
      )} within ${low}–${high} of the prescribed ${channel}${describeSmoothing(
        criterion.band.smoothing_s,
      )}`;
    }
    case "duration_floor":
      return `Lasts at least ${formatDurationClock(criterion.min_seconds)}`;
    case "ceiling": {
      const channel = CHANNEL_LABELS[criterion.channel];
      const limit =
        criterion.limit.kind === "percent_of_anchor"
          ? `${formatPercent(criterion.limit.pct)} of ${anchorLabel(
              criterion.limit.anchor_type,
            )}`
          : `${criterion.limit.value} ${criterion.limit.unit}`;
      return `No more than ${formatDurationClock(
        criterion.max_seconds_above,
      )} with ${channel} above ${limit}${describeSmoothing(
        criterion.smoothing_s,
      )}`;
    }
    case "sets_completed":
      return `${formatPercent(criterion.min_fraction)} of the prescribed sets completed`;
    case "load_within":
      return `Loads within ${formatPercent(criterion.pct_tolerance)} of what was prescribed`;
  }
}

/**
 * The trailing clause naming a band's or a ceiling's smoothing window.
 *
 * Always said, never implied. The window is the difference between judging
 * the athlete and judging the equipment — power is spiky at 1 Hz, and the same
 * ride scores differently at 0 s and at 30 s — so a criterion that hides its
 * window is not a criterion the athlete can hold anyone to (D73).
 */
function describeSmoothing(seconds: number): string {
  return seconds > 0 ? `, ${seconds} s average` : ", raw samples";
}

/** The tag of each criterion in the union. */
export type CriterionKind = SuccessCriterion["kind"];

/** How the criteria editor names each kind in its "add" menu. */
export const CRITERION_KIND_LABELS: Readonly<Record<CriterionKind, string>> = {
  time_in_band: "Time in band",
  duration_floor: "Minimum duration",
  ceiling: "Ceiling",
  sets_completed: "Sets completed",
  load_within: "Load within tolerance",
};

/**
 * Which criteria a discipline can be judged by.
 *
 * Mirrors the domain's `STRENGTH_ONLY_KINDS` / `ENDURANCE_ONLY_KINDS`: a
 * `time_in_band` on a lifting session refers to a power trace that will never
 * exist, and the backend refuses it. Offering it and then failing would be a
 * worse way to say the same thing.
 */
export function criterionKindsFor(
  discipline: Schemas["Discipline"],
): CriterionKind[] {
  return discipline === "cycling"
    ? ["time_in_band", "duration_floor", "ceiling"]
    : ["sets_completed", "load_within", "duration_floor"];
}

/**
 * A criterion of this kind with defensible starting values.
 *
 * Not zeroes: a criterion is a rule, and a rule of "at least 0% of the time"
 * passes every session ever ridden. These are the values a coach would write
 * first and then adjust.
 */
export function blankCriterion(kind: CriterionKind): SuccessCriterion {
  switch (kind) {
    case "time_in_band":
      return {
        kind: "time_in_band",
        // 30 s is the conventional window for steady work, and the
        // default the backend applies when a band does not state one.
        band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
        min_fraction: 0.8,
        selector: { kind: "role", role: "work", index: null },
      };
    case "duration_floor":
      return { kind: "duration_floor", min_seconds: 3600 };
    case "ceiling":
      return {
        kind: "ceiling",
        channel: "hr",
        limit: { kind: "percent_of_anchor", anchor_type: "lthr", pct: 1.0 },
        max_seconds_above: 300,
        // Raw: a ceiling is about excursions, and smoothing hides them.
        smoothing_s: 0,
      };
    case "sets_completed":
      return { kind: "sets_completed", min_fraction: 0.9 };
    case "load_within":
      return { kind: "load_within", pct_tolerance: 0.05 };
  }
}

/** Which steps a criterion applies to, in words. */
export function describeSelector(
  selector: Schemas["StepSelectorSchema"],
): string {
  switch (selector.kind) {
    case "all":
      return "the session's time";
    case "role":
      return selector.role
        ? `the ${STEP_ROLE_LABELS[selector.role]} steps' time`
        : "the selected steps' time";
    case "index":
      return selector.index === null || selector.index === undefined
        ? "the selected step's time"
        : `step ${selector.index + 1}'s time`;
  }
}
