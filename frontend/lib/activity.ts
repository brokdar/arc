import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

export type SessionDiscipline = Schemas["SessionDiscipline"];
export type RecordingKind = Schemas["RecordingKind"];
export type SessionMatchStatus = Schemas["SessionMatchStatus"];
export type ClassificationSource = Schemas["ClassificationSource"];
export type SessionListItem = Schemas["SessionListItem"];
export type Session = Schemas["SessionRead"];
export type Recording = Schemas["RecordingRead"];

/** Every cached page of the session log, however it was filtered. */
export const SESSIONS_QUERY_PREFIX = ["get", "/api/v1/sessions"] as const;

/**
 * The vocabulary of a *recorded* session, said in English.
 *
 * `SessionDiscipline` is a superset of the two disciplines arc prescribes: a
 * head unit can hand us a walk or a swim, and `other` is the bucket that lets
 * those be ingested without being lied about. The icon set only draws the two
 * we train, so `other` gets neither — see `disciplineIconName`.
 */
export const DISCIPLINE_LABELS: Readonly<Record<SessionDiscipline, string>> = {
  cycling: "Ride",
  strength: "Strength",
  other: "Other",
};

/**
 * Which glyph a recorded discipline gets, or `null` for the bucket.
 *
 * `other` deliberately has no icon: drawing it as a bike would say the file
 * was a ride, which is the one thing we know it was not classified as.
 */
export function disciplineIconName(
  discipline: SessionDiscipline,
): "cycling" | "strength" | null {
  return discipline === "other" ? null : discipline;
}

/** Whether the session came from a device file or was typed in. */
export const RECORDING_KIND_LABELS: Readonly<Record<RecordingKind, string>> = {
  device: "Device",
  manual: "Manual",
};

/**
 * Where a completed session stands relative to the plan.
 *
 * One member today, and the badge takes it as a **prop** rather than assuming
 * it: WP-6 owns this lifecycle and adds `matched` / `unplanned` / `displaced`
 * then (D81). Keyed by the generated enum, so the day those arrive this table
 * fails to compile instead of rendering a raw enum value.
 */
export const MATCH_STATUS_LABELS: Readonly<Record<SessionMatchStatus, string>> =
  {
    unmatched: "Unmatched",
  };

/** Why a badge says what it says, on hover. */
export const MATCH_STATUS_REASONS: Readonly<
  Record<SessionMatchStatus, string>
> = {
  unmatched: "Not yet linked to a planned session — matching arrives with WP-6",
};

/**
 * How the discipline was arrived at.
 *
 * `manual` covers both a hand-entered session and an athlete's correction of a
 * guessed one: both are "because you said so", which is neither a file's sport
 * field nor an inference over channels (D99).
 */
export const CLASSIFICATION_LABELS: Readonly<
  Record<ClassificationSource, string>
> = {
  sport_field: "the file's sport field",
  heuristic: "a guess from the channels present",
  manual: "you",
};
