import type { components } from "@/generated/api/schema";
import { formatDurationClock } from "@/lib/format";

type Schemas = components["schemas"];

export type MatchLinkStatus = Schemas["MatchLinkStatus"];
export type MatchSummary = Schemas["MatchSummary"];
export type Match = Schemas["MatchRead"];
export type MatchBreakdown = Schemas["MatchBreakdownRead"];
export type MatchComponent = Schemas["MatchComponent"];
export type MatchComponentScore = Schemas["MatchComponentRead"];
export type MatchUnassessed = Schemas["MatchUnassessedRead"];
export type PlannedSessionListItem = Schemas["PlannedSessionListItem"];

/**
 * Every cached match list, however it was filtered — and, separately, every
 * cached single match.
 *
 * Two constants rather than one, because a query key is matched by **prefix**
 * over its elements and `"/api/v1/matches/{match_id}"` is a different second
 * element from `"/api/v1/matches"`, not a longer key beginning with it.
 * Invalidating the list alone leaves the panel reading a link whose status the
 * click just changed — which looks exactly like a mutation that silently did
 * nothing.
 */
export const MATCHES_QUERY_PREFIX = ["get", "/api/v1/matches"] as const;
export const MATCH_QUERY_PREFIX = [
  "get",
  "/api/v1/matches/{match_id}",
] as const;
/** Every cached page of the plan, which a link changes the status of. */
export const PLANNED_SESSIONS_QUERY_PREFIX = [
  "get",
  "/api/v1/planned-sessions",
] as const;
/**
 * One planned session by id — a different cache from the list above, for the
 * reason spelled out for matches: the second element is a different string,
 * not a longer one. Today's panel and the calendar's session sheet both read
 * it, so anything that rewrites a prescription has to name it explicitly.
 */
export const PLANNED_SESSION_QUERY_PREFIX = [
  "get",
  "/api/v1/planned-sessions/{planned_session_id}",
] as const;
/** The calendar week, whose cards carry the link state (WP-6, WP-7). */
export const PLAN_WEEK_QUERY_PREFIX = ["get", "/api/v1/plan/week"] as const;

/**
 * What one link claims, said in English.
 *
 * Keyed by the generated enum so a member added to `MatchLinkStatus` is a
 * compile error here rather than a badge rendering `auto_high` at an athlete.
 *
 * The pair worth reading carefully is `auto_high` against `confirmed`: both
 * mean the session and the planned session are joined, and they differ in
 * *whose* claim it is. A machine link may be revised by the next re-run of
 * matching; the athlete's never is (`STICKY_STATUSES`).
 */
export const MATCH_LINK_LABELS: Readonly<Record<MatchLinkStatus, string>> = {
  auto_high: "Auto-linked",
  pending: "Proposed",
  confirmed: "Confirmed",
  displaced: "Instead of",
};

/** Why a link says what it says, on hover and to a screen reader. */
export const MATCH_LINK_REASONS: Readonly<Record<MatchLinkStatus, string>> = {
  auto_high:
    "Linked without asking, because the recording and the prescription agree closely. Still yours to undo.",
  pending:
    "A proposal waiting on you: arc thinks this session answered that planned session, and has changed nothing until you say so",
  confirmed:
    "You linked these yourself, and no re-run of matching will revise it",
  displaced:
    "You trained, and deliberately not this: the planned session counts as displaced rather than missed or completed, and this session is scored on its own",
};

/**
 * The thresholds `app.domain.matching.classify` applies, mirrored.
 *
 * Mirrored rather than fetched because they are constants of the build plan
 * (WP-6.3) and not settings — the API states no endpoint for them, and a
 * number the UI can only get by reading the backend source is exactly the
 * number to write down beside the sentence it justifies. Nothing here decides
 * a link: they are used only to *explain* a score the API already classified,
 * which is why a drift between them and the domain misphrases a sentence
 * rather than mislabelling a link.
 */
export const AUTO_LINK_SIMILARITY = 0.75;
export const PROPOSAL_SIMILARITY = 0.4;

/** The three things a similarity is made of, named. */
export const COMPONENT_LABELS: Readonly<Record<MatchComponent, string>> = {
  duration: "Duration",
  intensity: "Intensity",
  structure: "Structure",
};

/** What each component compares, for the athlete who has not read WP-6. */
export const COMPONENT_DESCRIPTIONS: Readonly<Record<MatchComponent, string>> =
  {
    duration: "Prescribed seconds against recorded seconds",
    intensity:
      "Prescribed normalized power (or heart rate) against what was recorded",
    structure: "Prescribed efforts or sets against those detected or logged",
  };

/**
 * One side of one component, in the unit that component is measured in.
 *
 * The raw numbers travel with every ratio because a ratio alone is not
 * explicable (`MatchComponentRead`): 0.38 on duration means nothing until it
 * reads "57:00 prescribed against 2:29:00 ridden".
 */
export function formatComponentValue(
  component: MatchComponent,
  basis: string | null,
  value: number,
): string {
  if (component === "duration") {
    return formatDurationClock(value);
  }
  if (component === "intensity") {
    return basis === "hr"
      ? `${Math.round(value)} bpm`
      : `${Math.round(value)} W`;
  }
  const unit = basis === "sets" ? "sets" : "efforts";
  return `${Math.round(value)} ${unit}`;
}

/** A similarity as a whole percentage: `0.6872` → `69%`. */
export function formatSimilarity(score: number): string {
  return `${Math.round(score * 100)}%`;
}

/** A weight as a whole percentage, for the column that says how much it counted. */
export function formatWeight(weight: number): string {
  return `${Math.round(weight * 100)}%`;
}

/**
 * Why a score is null, said as the API means it.
 *
 * Null is not zero (`MatchBreakdownRead.score`): it means no component could
 * be assessed at all, which is why the link is a question rather than a
 * refusal.
 */
export const NO_SCORE_REASON =
  "Nothing could be compared: the date and the discipline agree, and none of the three components had two sides to put against each other";

/**
 * How a score compares to the thresholds, in a sentence.
 *
 * Said rather than shown as a gauge because the number is only meaningful
 * against the two cut-offs it was classified by, and a bar with no marks on it
 * would invite reading 0.69 as "nearly right" when what it means is "inside
 * the band where arc asks instead of deciding".
 */
export function describeScore(score: number | null): string {
  if (score === null) {
    return NO_SCORE_REASON;
  }
  if (score >= AUTO_LINK_SIMILARITY) {
    return `At or above ${formatSimilarity(AUTO_LINK_SIMILARITY)}, arc links a session without asking.`;
  }
  if (score >= PROPOSAL_SIMILARITY) {
    return `Between ${formatSimilarity(PROPOSAL_SIMILARITY)} and ${formatSimilarity(AUTO_LINK_SIMILARITY)}, arc proposes and leaves the decision to you.`;
  }
  return `Below ${formatSimilarity(PROPOSAL_SIMILARITY)}, arc proposes nothing on its own — this link is yours.`;
}

/** Whether a score is low enough that "done instead of" is the likelier claim. */
export function isDisplacementScore(score: number | null): boolean {
  return score !== null && score < PROPOSAL_SIMILARITY;
}

/**
 * What linking as *displaced* actually does, in plain language.
 *
 * WP-6.4 in one sentence, and it is not a detail: the difference between the
 * two link kinds is what the week says about the planned session afterwards
 * and whether the ride is scored against a prescription it never followed.
 */
export const DISPLACED_EXPLANATION =
  "Use this when you trained, but not the thing on the calendar. The planned session is marked displaced — neither missed nor completed — and this session is scored on its own terms, with no adherence to a prescription it never followed.";

/** The same, for the ordinary link. */
export const CONFIRMED_EXPLANATION =
  "Use this when this session is the planned one, however far the numbers drifted. The planned session is marked completed and this session is scored against its prescription.";
