import {
  type PainBand,
  type PainKind,
  type RedFlagKind,
  setsLeftToday,
} from "@/components/strength/safety/safety-model";
import type { components } from "@/generated/api/schema";
import type { Mutation, SetPatch } from "@/lib/offline/db";

/**
 * The logging screen's state, and the outbox mutations each action writes.
 *
 * Pure: no React, no IndexedDB. The screen holds one `ActiveSession`, stores
 * it on every change (`session-store.ts`) and hands the mutations these
 * functions return to the outbox. Every number on the screen comes from the
 * packet the server prepared (`lib/offline/packet-store.ts`) or from what the
 * athlete entered; nothing here invents a training number.
 *
 * `set_index` is the order sets were logged in, as the server keeps it. A set
 * is identified by the uuid7 minted when it was logged (`set_client_id`), so
 * an edit or a rating patches that set and an undo deletes it.
 */

export type PacketSession = components["schemas"]["PacketSessionRead"];
export type PacketLine = components["schemas"]["PacketLineRead"];
export type Packet = components["schemas"]["PacketRead"];
export type EffortSimple = NonNullable<
  components["schemas"]["LoggedSetRead"]["effort_simple"]
>;

/** Which effort question the packet asks after an exercise's last set. */
export type EffortScale = Packet["effort"]["scale"];

/** One answer to the effort question, or none. */
export type Rating =
  | { kind: "reps"; rir: number; capped: boolean }
  | { kind: "simple"; answer: EffortSimple }
  | { kind: "none" };

export interface LoggedSet {
  clientId: string;
  lineRef: string;
  exerciseId: string;
  warmup: boolean;
  loadKg: number | null;
  reps: number | null;
  durationS: number | null;
  perSide: boolean;
  rir: number | null;
  rirCapped: boolean;
  effortSimple: EffortSimple | null;
  /**
   * The ⋯ → Pain note on this set. Optional: a session stored by an earlier
   * build has none, which reads as no pain.
   */
  painKind?: PainKind | null;
  painBand?: PainBand | null;
}

export interface Rest {
  /** Wall-clock epoch ms the rest ends at (corrected while the page lives). */
  endsAt: number;
}

export interface ActiveSession {
  plannedSessionId: string;
  sessionClientId: string;
  startedAt: number;
  /** In the order they were logged: index = `set_index`. */
  sets: LoggedSet[];
  /** The exercise the screen shows, an index into the packet's lines. */
  lineIndex: number;
  rest: Rest | null;
  finished: boolean;
  /** Set when *Stop — something's wrong* ended the session: which text to show. */
  redFlag?: RedFlagKind;
}

/** Rest after a set when the line names none: main lifts 3 min, else 2. */
export const MAIN_REST_S = 180;
export const OTHER_REST_S = 120;

export function restFor(line: PacketLine): number {
  return line.prescription.rest_s ?? (line.main ? MAIN_REST_S : OTHER_REST_S);
}

export function workingSetsOf(
  session: ActiveSession,
  lineRef: string,
): LoggedSet[] {
  return session.sets.filter((s) => s.lineRef === lineRef && !s.warmup);
}

export function warmupsDone(session: ActiveSession, lineRef: string): number {
  return session.sets.filter((s) => s.lineRef === lineRef && s.warmup).length;
}

/**
 * Whether a line has nothing left to log today: every prescribed set, or a
 * set with sharp pain (`safety/safety-model.ts`), whichever comes first.
 */
export function lineComplete(
  session: ActiveSession,
  line: PacketLine,
): boolean {
  const pain = workingSetsOf(session, line.line_ref).map((s) => s.painKind);
  return setsLeftToday(line.prescription.sets, pain) === 0;
}

/** Whether sharp pain stopped this line today. */
export function stoppedByPain(
  session: ActiveSession,
  line: PacketLine,
): boolean {
  return workingSetsOf(session, line.line_ref).some(
    (s) => s.painKind === "sharp",
  );
}

/** The first line at or after `from` (wrapping) with sets still to log. */
export function nextOpenLine(
  session: ActiveSession,
  lines: readonly PacketLine[],
  from: number,
): number | null {
  for (let step = 0; step < lines.length; step++) {
    const index = (from + step) % lines.length;
    if (!lineComplete(session, lines[index])) return index;
  }
  return null;
}

export interface Draft {
  loadKg: number | null;
  reps: number | null;
  durationS: number | null;
}

/** The numbers a line's next set opens with: its last set here, else the packet's pre-fill. */
export function draftFor(session: ActiveSession, line: PacketLine): Draft {
  const done = workingSetsOf(session, line.line_ref);
  const last = done[done.length - 1];
  if (last) {
    return { loadKg: last.loadKg, reps: last.reps, durationS: last.durationS };
  }
  const { prefill, prescription } = line;
  const plannedReps = Array.isArray(prescription.reps)
    ? prescription.reps[1]
    : prescription.reps;
  const durationS = prefill.duration_s ?? prescription.duration_s ?? null;
  return {
    loadKg: prefill.load_kg,
    reps: durationS !== null ? null : (prefill.reps ?? plannedReps ?? 8),
    durationS,
  };
}

/** Whether the next set of `line` is its last working set. */
export function isLastWorkingSet(
  session: ActiveSession,
  line: PacketLine,
): boolean {
  return (
    workingSetsOf(session, line.line_ref).length === line.prescription.sets - 1
  );
}

/**
 * Whether the effort row replaces ✓ on this set. Asked once, on an
 * exercise's last working set (see `effort-row.tsx`); never on a timed hold,
 * whose rating is not a count of reps.
 */
export function asksEffort(
  packet: Packet,
  session: ActiveSession,
  line: PacketLine,
): boolean {
  if (packet.effort.ask === "never") return false;
  if (
    line.prescription.duration_s !== null &&
    line.prescription.duration_s !== undefined
  ) {
    return false;
  }
  return isLastWorkingSet(session, line);
}

export interface Stamp {
  /** Epoch ms of the action. */
  now: number;
  /** Minutes east of UTC on the device at that moment. */
  utcOffsetMin: number;
  /** Mints one uuid7. */
  id: () => string;
}

function base(session: ActiveSession, stamp: Stamp) {
  return {
    mutation_id: stamp.id(),
    client_edit_time: new Date(stamp.now).toISOString(),
    session_client_id: session.sessionClientId,
  };
}

export function startMutation(session: ActiveSession, stamp: Stamp): Mutation {
  return {
    kind: "session_start",
    ...base(session, stamp),
    start_time: new Date(session.startedAt).toISOString(),
  };
}

/** The fields a rating writes, on the scale the packet asked in. */
export function ratingFields(rating: Rating, scale: EffortScale): SetPatch {
  if (scale === "easy_good_hard") {
    return { effort_simple: rating.kind === "simple" ? rating.answer : null };
  }
  return rating.kind === "reps"
    ? { rir: rating.rir, rir_capped: rating.capped }
    : { rir: null, rir_capped: false };
}

function ratingOf(set: LoggedSet, scale: EffortScale): SetPatch {
  if (set.warmup) return {};
  return scale === "easy_good_hard"
    ? { effort_simple: set.effortSimple }
    : { rir: set.rir, rir_capped: set.rirCapped };
}

/** A whole set as its first upload: every field it has. */
export function setMutation(
  session: ActiveSession,
  set: LoggedSet,
  setIndex: number,
  scale: EffortScale,
  stamp: Stamp,
): Mutation {
  const fields: SetPatch = {
    exercise_id: set.exerciseId,
    planned_line_ref: set.lineRef,
    set_index: setIndex,
    completed_at: new Date(stamp.now).toISOString(),
    utc_offset_min: stamp.utcOffsetMin,
    ...ratingOf(set, scale),
  };
  if (set.reps !== null) fields.reps = set.reps;
  if (set.durationS !== null) fields.duration_s = set.durationS;
  if (set.loadKg !== null) fields.load_kg = set.loadKg;
  if (set.perSide) fields.per_side = true;
  if (set.warmup) fields.set_kind = "warmup";
  return {
    kind: "set_upsert",
    ...base(session, stamp),
    set_client_id: set.clientId,
    set: fields,
  };
}

/** An edit of fields on a logged set. */
export function patchMutation(
  session: ActiveSession,
  clientId: string,
  patch: SetPatch,
  stamp: Stamp,
): Mutation {
  return {
    kind: "set_upsert",
    ...base(session, stamp),
    set_client_id: clientId,
    set: patch,
  };
}

export function deleteMutation(
  session: ActiveSession,
  clientId: string,
  stamp: Stamp,
): Mutation {
  return {
    kind: "set_delete",
    ...base(session, stamp),
    set_client_id: clientId,
  };
}

export function applyRating(
  set: LoggedSet,
  rating: Rating,
  scale: EffortScale,
): LoggedSet {
  if (set.warmup) return set;
  if (scale === "easy_good_hard") {
    return {
      ...set,
      effortSimple: rating.kind === "simple" ? rating.answer : null,
    };
  }
  return rating.kind === "reps"
    ? { ...set, rir: rating.rir, rirCapped: rating.capped }
    : { ...set, rir: null, rirCapped: false };
}

/** The rating fields a set holds now, to restore it after an undo. */
export function ratingSnapshot(set: LoggedSet, scale: EffortScale): SetPatch {
  return ratingOf(set, scale);
}

/** The session's active line once the current one is complete. */
export function advance(
  session: ActiveSession,
  lines: readonly PacketLine[],
): ActiveSession {
  const current = lines[session.lineIndex];
  if (current && !lineComplete(session, current)) return session;
  const next = nextOpenLine(session, lines, session.lineIndex);
  return next === null ? session : { ...session, lineIndex: next };
}

/** Whether every working set of every line is logged. */
export function allDone(
  session: ActiveSession,
  lines: readonly PacketLine[],
): boolean {
  return nextOpenLine(session, lines, 0) === null;
}
