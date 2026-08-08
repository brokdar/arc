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
 * Where a completed session stands relative to the plan (WP-6).
 *
 * Keyed by the generated enum, which is what made adding WP-6's three members
 * a compile error here rather than a badge rendering a raw enum value.
 *
 * `unmatched` and `unplanned` are the pair worth reading carefully: the first
 * means undecided — including while a proposal is waiting on an answer — and
 * the second means decided, that there was nothing on the calendar this could
 * have been.
 */
export const MATCH_STATUS_LABELS: Readonly<Record<SessionMatchStatus, string>> =
  {
    unmatched: "Unmatched",
    matched: "Matched",
    unplanned: "Unplanned",
    displaced: "Instead of",
  };

/** Why a badge says what it says, on hover. */
export const MATCH_STATUS_REASONS: Readonly<
  Record<SessionMatchStatus, string>
> = {
  unmatched: "Not yet linked to a planned session",
  matched: "Linked to the planned session it answered",
  unplanned: "Nothing was planned that this could have been",
  displaced:
    "Deliberately linked as done instead of a planned session, and scored on its own",
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
