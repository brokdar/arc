import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

export type WellnessDay = Schemas["WellnessDayRead"];
export type WellnessWrite = Schemas["WellnessDayWrite"];
export type WellnessInputs = Schemas["WellnessInputsRead"];
export type SubjectiveScale = Schemas["SubjectiveScaleRead"];
export type Confounder = Schemas["Confounder"];
export type InputTier = Schemas["InputTier"];

/**
 * The wellness form's vocabulary, kept out of the components.
 *
 * What is **not** here is the part that matters: the scales, their polarity,
 * their anchor descriptors, the confounder list and which confounders void a
 * morning all come from `GET /api/v1/wellness/inputs` at runtime. The UI
 * carrying a private copy of what a 3 means is exactly what serving that
 * endpoint exists to prevent, so this module holds only the things a *form*
 * needs and the API has no opinion about: what to call a field on screen, what
 * unit it is entered in, and which group it belongs to.
 */

/** How a numeric marker is entered, and what to call it. */
export interface NumericField {
  readonly field: keyof WellnessWrite;
  readonly label: string;
  /** The unit shown beside the label — a hint, not help text. */
  readonly hint: string;
  /**
   * A number the athlete types is not always the number the API stores. Sleep
   * is entered in hours and stored in seconds, and SpO2 is entered as a
   * percentage and stored as a fraction, because "0.97" is not what anybody's
   * watch shows them.
   */
  readonly scale?: number;
  /** Decimal places to render the stored value back with. */
  readonly precision: number;
}

/** The overnight block: how long, when, and how it felt. */
export const SLEEP_FIELDS: readonly NumericField[] = [
  {
    field: "sleep_duration_s",
    label: "Slept",
    hint: "h",
    scale: 3600,
    precision: 2,
  },
];

/** The device numbers. Ordered `valuable` tier first — see `orderByTier`. */
export const MARKER_FIELDS: readonly NumericField[] = [
  { field: "resting_hr_bpm", label: "Resting HR", hint: "bpm", precision: 0 },
  { field: "hrv_ms", label: "HRV", hint: "ms", precision: 1 },
  { field: "weight_kg", label: "Weight", hint: "kg", precision: 1 },
  {
    field: "respiratory_rate_brpm",
    label: "Respiratory rate",
    hint: "breaths/min",
    precision: 1,
  },
  // Entered as a percentage and stored as a fraction: `.claude/rules/
  // backend-domain-units.md` rule 1 binds the API, and 97 is what the watch
  // shows the athlete.
  {
    field: "spo2",
    label: "Blood oxygen",
    hint: "%",
    scale: 0.01,
    precision: 0,
  },
  {
    field: "wrist_temperature_delta_c",
    label: "Wrist temp",
    hint: "Δ°C",
    precision: 1,
  },
];

/** The subjective ratings, in the order the form asks them. */
export const RATING_FIELDS = [
  "sleep_quality",
  "fatigue",
  "soreness",
  "stress",
  "motivation",
] as const;

export type RatingField = (typeof RATING_FIELDS)[number];

const RATING_LABELS: Readonly<Record<RatingField, string>> = {
  sleep_quality: "Sleep quality",
  fatigue: "Fatigue",
  soreness: "Soreness",
  stress: "Stress",
  motivation: "Motivation",
};

export function ratingLabel(field: RatingField): string {
  return RATING_LABELS[field];
}

/** A confounder tag rendered as words: `poor_sleep_timing` → `Poor sleep timing`. */
export function confounderLabel(value: string): string {
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Reorder fields so the ones the served tiers call `valuable` come first.
 *
 * The tier is data the API publishes and the promise it carries is that a
 * consumer *acts* on it (§13's graceful degradation). Here that means the
 * athlete meets the six inputs the morning question turns on before the ones
 * that are nice to have — a form answered top-down then degrades gracefully
 * when it is abandoned half way, which is how a real morning goes.
 *
 * A stable sort, so the ordering inside each tier stays the one declared above.
 */
export function orderByTier<T extends { readonly field: string }>(
  fields: readonly T[],
  tiers: WellnessInputs["tiers"] | undefined,
): readonly T[] {
  if (!tiers) {
    return fields;
  }
  const rank = new Map<string, number>(
    tiers.map((entry) => [entry.field, entry.tier === "valuable" ? 0 : 1]),
  );
  return [...fields].sort(
    (a, b) => (rank.get(a.field) ?? 1) - (rank.get(b.field) ?? 1),
  );
}

/** The stored value of a numeric field, rendered for its input box. */
export function toInputValue(
  day: WellnessDay | null | undefined,
  spec: NumericField,
): string {
  const stored = day?.[spec.field as keyof WellnessDay];
  if (typeof stored !== "number") {
    return "";
  }
  const shown = stored / (spec.scale ?? 1);
  // `Number(...)` after `toFixed` drops the trailing zeros a fixed rendering
  // adds, so 46 does not appear in the box as "46.0" and then get saved back
  // as a different-looking number.
  return String(Number(shown.toFixed(spec.precision)));
}

/** Turn a typed box back into the number the API stores, or `null` to clear. */
export function fromInputValue(
  text: string,
  spec: NumericField,
): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") {
    // `null` clears, which is what an emptied box means — the athlete deleted
    // a value they had entered.
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    // `undefined` is omission: the write leaves the stored value alone rather
    // than clearing it, which is the safe answer to text nobody can parse.
    return undefined;
  }
  const stored = parsed * (spec.scale ?? 1);
  return spec.scale === 3600 ? Math.round(stored) : stored;
}

/**
 * The anchor descriptor for one point on a served scale.
 *
 * Falls back to the bare numeral rather than inventing words: an unlabelled
 * point means the served table and this build disagree, and guessing at the
 * label would hide that.
 */
export function anchorLabel(scale: SubjectiveScale, value: number): string {
  const found = scale.anchors.find((anchor) => anchor.value === value);
  return found ? `${value} — ${found.label}` : String(value);
}

/** The points of a scale, low to high. */
export function scalePoints(scale: SubjectiveScale): number[] {
  return Array.from(
    { length: scale.high - scale.low + 1 },
    (_, index) => scale.low + index,
  );
}
