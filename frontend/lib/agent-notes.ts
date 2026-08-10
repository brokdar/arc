import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

export type AgentNote = Schemas["AgentNoteRead"];
export type NoteKind = Schemas["NoteKind"];
export type DisputeRating = Schemas["DisputeRating"];

/** Every cached note list, whichever subject it was fetched for. */
export const AGENT_NOTES_QUERY_PREFIX = ["get", "/api/v1/agent-notes"] as const;

/**
 * The two things the coach may write, told apart.
 *
 * An evaluation is the agent's reading of a session that has happened; an
 * annotation is free commentary. They are separate autonomy tiers on the
 * backend (WP-8 §1), and the badge is where that distinction reaches the
 * athlete — "what it thought of Tuesday" and "a thing it noticed" are not the
 * same kind of claim.
 */
export const NOTE_KIND_LABELS: Readonly<Record<NoteKind, string>> = {
  evaluation: "Evaluation",
  annotation: "Note",
};

/**
 * The rating a tap should send, given what is already on the note.
 *
 * Tapping the rating a note already carries clears it: that is the third state
 * of the toggle — "I take that back" — and an athlete who cannot take a rating
 * back stops giving them (the API's own reasoning, `AgentNoteDispute`).
 */
export function nextRating(
  current: DisputeRating | null,
  tapped: DisputeRating,
): DisputeRating | null {
  return current === tapped ? null : tapped;
}
