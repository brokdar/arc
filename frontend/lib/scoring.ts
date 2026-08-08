import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

export type SessionScore = Schemas["SessionScoreRead"];
export type AxisResult = Schemas["AxisRead"];
export type ScoringAxis = Schemas["ScoringAxis"];
export type CriterionOutcome = Schemas["CriterionOutcomeRead"];
export type CriterionKind = Schemas["CriterionKind"];
export type Verdict = Schemas["Verdict"];
export type VerdictRule = Schemas["VerdictRule"];
export type VerdictDeclaration = Schemas["VerdictDeclarationRead"];
export type Reason = Schemas["Reason"];
export type Reasons = Schemas["ReasonsRead"];
export type SessionAlignment = Schemas["SessionAlignmentRead"];
export type AlignedStep = Schemas["AlignedStepRead"];
export type ExcludedStep = Schemas["ExcludedStepRead"];
export type CompletionState = Schemas["CompletionState"];

/**
 * One scoring axis, resolved into the one thing a component has to know.
 *
 * The same narrowing `lib/metrics.ts` does for a metric slot, and for the same
 * reason: the API guarantees exactly one of `value` / `not_assessed` is set
 * (`app.api.schemas.scoring.AxisRead`), so the grid branches **once** and
 * renders either a number with its explanation or the reason there is none —
 * in the same slot either way (UI convention 4). A missing axis is *not
 * assessed*, never zero: an unscored axis is not a failed one.
 */
export type ResolvedAxis =
  | {
      readonly kind: "value";
      readonly value: number;
      readonly explanation: Schemas["ExplanationRead"] | null;
    }
  | { readonly kind: "absent"; readonly reason: string };

/** Narrow one axis. A malformed axis reads as absent, never as zero. */
export function resolveAxis(axis: AxisResult): ResolvedAxis {
  if (axis.value !== null && axis.value !== undefined) {
    return {
      kind: "value",
      value: axis.value,
      explanation: axis.explanation ?? null,
    };
  }
  return {
    kind: "absent",
    reason: axis.not_assessed ?? "This axis was not computed.",
  };
}

/** The five axes, as the athlete reads them. */
export const AXIS_LABELS: Readonly<Record<ScoringAxis, string>> = {
  completion: "Completion",
  adherence: "Adherence",
  discipline: "Discipline",
  pacing: "Pacing",
  sets_load: "Sets & load",
  response: "Response",
  fuelling: "Fuelling",
};

/** What each axis is asking. Rendered under the label, once per grid slot. */
export const AXIS_QUESTIONS: Readonly<Record<ScoringAxis, string>> = {
  completion: "Did you do the prescribed amount?",
  adherence: "Was the work done at the prescribed target?",
  discipline: "Did you stay under the caps the session set?",
  pacing: "Did the last effort hold up against the first?",
  sets_load: "Were the sets completed at the prescribed load?",
  response: "How your body answered — out of scope for now.",
  fuelling: "Whether fuelling matched the demand — out of scope for now.",
};

/** The five criterion kinds, as the expandable detail names them. */
export const CRITERION_LABELS: Readonly<Record<CriterionKind, string>> = {
  time_in_band: "Time in band",
  duration_floor: "Duration floor",
  ceiling: "Ceiling",
  sets_completed: "Sets completed",
  load_within: "Load within tolerance",
};

/**
 * The five verdicts, in the order the override picker offers them.
 *
 * `app.domain.scoring.Verdict`'s own order — best-to-worst is not the point,
 * a stable order is: the athlete learns where "under" sits and stops reading
 * the list.
 */
export const VERDICT_ORDER: readonly Verdict[] = [
  "as_intended",
  "under",
  "over",
  "abandoned",
  "different_session",
];

export const VERDICT_LABELS: Readonly<Record<Verdict, string>> = {
  as_intended: "As intended",
  under: "Under",
  over: "Over",
  abandoned: "Abandoned",
  different_session: "A different session",
};

/** One line of help under each verdict in the picker. */
export const VERDICT_HINTS: Readonly<Record<Verdict, string>> = {
  as_intended: "The session went as the plan asked.",
  under: "Short of the prescription — less time, or under the target.",
  over: "Past the prescription — longer, or harder than asked.",
  abandoned: "It was started and not finished.",
  different_session: "You trained, and it was not this.",
};

/**
 * The controlled reason list (WP-7.3), in the order the picker offers them.
 *
 * Controlled rather than free text because the point is to be able to count
 * them; the note travels beside the list, never instead of it. `not_provided`
 * is last and is the honest member — "we asked and got no answer" is a
 * different fact from "we never asked".
 */
export const REASON_ORDER: readonly Reason[] = [
  "time",
  "weather",
  "heat",
  "traffic",
  "terrain",
  "fatigue",
  "sleep",
  "fuelling",
  "illness",
  "equipment",
  "group_ride",
  "felt_good",
  "not_provided",
];

export const REASON_LABELS: Readonly<Record<Reason, string>> = {
  time: "Time",
  weather: "Weather",
  heat: "Heat",
  traffic: "Traffic",
  terrain: "Terrain",
  fatigue: "Fatigue",
  sleep: "Sleep",
  fuelling: "Fuelling",
  illness: "Illness",
  equipment: "Equipment",
  group_ride: "Group ride",
  felt_good: "Felt good",
  not_provided: "Would rather not say",
};

/** `app.domain.scoring.MIN_REASONS` / `MAX_REASONS`. */
export const MIN_REASONS = 1;
export const MAX_REASONS = 3;

/** Longest note the API stores beside the reasons. */
export const MAX_REASON_NOTE_CHARS = 1000;

/**
 * Whether a declaration of `verdict` may be sent with `reasons`.
 *
 * The same rule `app.services.scoring._check_reasons` enforces, restated on
 * this side so the form can refuse before it spends a request: anything but
 * `as_intended` needs one to three reasons, each at most once. Checked here
 * *and* there on purpose — the client's copy is a courtesy, the server's is
 * the rule.
 */
export function reasonsProblem(
  verdict: Verdict,
  reasons: readonly Reason[],
): string | null {
  if (new Set(reasons).size !== reasons.length) {
    return "Each reason may be given once.";
  }
  if (reasons.length > MAX_REASONS) {
    return `Pick at most ${MAX_REASONS} reasons, in order of importance.`;
  }
  if (verdict === "as_intended") {
    return null;
  }
  if (reasons.length < MIN_REASONS) {
    return "Say why, in at least one reason — pick “Would rather not say” if you would rather not.";
  }
  return null;
}

/**
 * How the week strip colours one day, or one card.
 *
 * A runtime lookup keyed by a value that arrives from the API, so it is data
 * rather than Tailwind class names — the same reason `PURPOSE_TONES` is data
 * (Tailwind can only generate a utility it can read in the source). The
 * colours themselves stay in `app/globals.css`; this table only says which
 * state wears which token.
 *
 * Two states share a tone deliberately. `displaced` is a link status and
 * `different_session` is a verdict, and both say *the athlete trained, and it
 * was not this* — one fact, one colour (D156).
 */
export interface CompletionTone {
  readonly label: string;
  readonly color: string;
}

export const COMPLETION_TONES: Readonly<
  Record<CompletionState, CompletionTone>
> = {
  planned: { label: "Planned", color: "var(--color-status-pending)" },
  completed: {
    // Not "Completed as intended": nothing has judged it yet, and a strip that
    // said so would be declaring a verdict nobody computed (D152).
    label: "Recorded, not yet judged",
    color: "var(--color-status-recorded)",
  },
  "completed-as_intended": {
    label: "As intended",
    color: "var(--color-status-completed)",
  },
  under: { label: "Under", color: "var(--color-status-under)" },
  over: { label: "Over", color: "var(--color-status-over)" },
  abandoned: { label: "Abandoned", color: "var(--color-status-abandoned)" },
  different_session: {
    label: "A different session",
    color: "var(--color-status-different)",
  },
  missed: { label: "Missed", color: "var(--color-status-missed)" },
  displaced: {
    label: "Trained something else",
    color: "var(--color-status-different)",
  },
  unplanned: { label: "Unplanned", color: "var(--color-status-unplanned)" },
};

/**
 * How an axis score is written: a percentage, in monospace by its caller.
 *
 * A day's roll-up (`app.domain.scoring.worst_state`) is deliberately **not**
 * here: the API computes the day's state and sends it, and a second copy of
 * the severity order on this side would be a rule that could disagree with the
 * one that produced the payload.
 */
export function formatAxisValue(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** How an alignment confidence is written. */
export function formatConfidence(value: number): string {
  return value.toFixed(2);
}

/**
 * Why a pair was excluded, in the athlete's terms.
 *
 * `app.domain.alignment.LOW_CONFIDENCE_REASON` is a tag, not a sentence — it
 * is stored so a later reader can group exclusions, and the prose belongs on
 * this side where it can be worded for a person.
 */
export function excludedReason(reason: string): string {
  return reason === "alignment_low_confidence"
    ? "The effort found here was too unlike the step to trust the pairing."
    : reason;
}
