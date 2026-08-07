import type { components } from "@/generated/api/schema";

import { formatDurationClock, formatPercent } from "@/lib/format";

type Schemas = components["schemas"];

/** The tagged union of machine-checkable success criteria (build plan WP-2.7). */
export type SuccessCriterion =
  | Schemas["TimeInBandSchema"]
  | Schemas["DurationFloorSchema"]
  | Schemas["CeilingSchema"]
  | Schemas["SetsCompletedSchema"]
  | Schemas["LoadWithinSchema"];

const ANCHOR_LABELS: Record<Schemas["AnchorType"], string> = {
  ftp: "FTP",
  lthr: "LTHR",
  max_hr: "max HR",
  cp: "CP",
  w_prime: "W′",
};

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
      )} within ${low}–${high} of the prescribed ${channel}`;
    }
    case "duration_floor":
      return `Lasts at least ${formatDurationClock(criterion.min_seconds)}`;
    case "ceiling": {
      const channel = CHANNEL_LABELS[criterion.channel];
      const limit =
        criterion.limit.kind === "percent_of_anchor"
          ? `${formatPercent(criterion.limit.pct)} of ${
              ANCHOR_LABELS[criterion.limit.anchor_type]
            }`
          : `${criterion.limit.value} ${criterion.limit.unit}`;
      return `No more than ${formatDurationClock(
        criterion.max_seconds_above,
      )} with ${channel} above ${limit}`;
    }
    case "sets_completed":
      return `${formatPercent(criterion.min_fraction)} of the prescribed sets completed`;
    case "load_within":
      return `Loads within ${formatPercent(criterion.pct_tolerance)} of what was prescribed`;
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
