import {
  type ActiveSession,
  draftFor,
  type LoggedSet,
  type PacketLine,
  type Stamp,
} from "@/components/strength/session-model";
import type { Mutation } from "@/lib/offline/db";

/**
 * When a session ends, and when it may start again.
 *
 * - **Abandoned sessions close at their last set.** A session with no set
 *   logged for three hours is finished at the time of its last set (or its
 *   start, when nothing was logged) — the phone left in the locker, a session
 *   opened twice. The server applies the same rule after every sync and on a
 *   schedule (`backend/app/domain/sync.py` `auto_close_end`); the phone
 *   applies it when it opens the session, so an offline phone does not
 *   resume a session from last night. The two agree on the end time because
 *   both read it off the last set, and on when the three hours start because
 *   both count the session's own latest edit — a reopen, a claim
 *   (`touchedAt`) — as a sign of life alongside the sets. Without it a
 *   session reopened more than three hours after its last set closed again
 *   on the phone's next open, and that later `session_finish` won.
 * - **A finished session reopens for 24 hours,** to add a set forgotten or
 *   fix one mis-logged; after that it is history, and the server refuses a
 *   later reopen (`may_reopen`).
 * - **"Log it now"** enters a planned session after the fact: every
 *   prescribed working set as planned, `source: "recap"`, with no
 *   `completed_at` — when each set was lifted is not known, and a recap
 *   never claims it was.
 */

export const AUTO_CLOSE_MS = 3 * 60 * 60 * 1000;
export const REOPEN_MS = 24 * 60 * 60 * 1000;

/** The end of an abandoned session, or null while it is live. */
export function abandonedEnd(
  session: ActiveSession,
  now: number,
): number | null {
  if (session.finished) return null;
  const times = session.sets
    .map((set) => set.completedAt)
    .filter((at): at is number => at !== undefined);
  const lastSet = times.length > 0 ? Math.max(...times) : null;
  const alive = Math.max(
    session.startedAt,
    lastSet ?? 0,
    session.touchedAt ?? 0,
  );
  if (now - alive < AUTO_CLOSE_MS) return null;
  return lastSet ?? session.startedAt;
}

export function mayReopen(session: ActiveSession, now: number): boolean {
  return (
    session.finished &&
    typeof session.endedAt === "number" &&
    now - session.endedAt <= REOPEN_MS
  );
}

export function finishMutation(
  session: ActiveSession,
  endAt: number,
  stamp: Stamp,
): Mutation {
  return {
    kind: "session_finish",
    mutation_id: stamp.id(),
    client_edit_time: new Date(stamp.now).toISOString(),
    session_client_id: session.sessionClientId,
    end_time: new Date(endAt).toISOString(),
  };
}

/**
 * "End" with nothing logged: the session is thrown away rather than saved
 * empty (UAT U14). Only for a session the server already holds — one whose
 * start never uploaded is simply withdrawn (`withdrawSession`).
 */
export function discardMutation(
  session: ActiveSession,
  stamp: Stamp,
): Mutation {
  return {
    kind: "session_discard",
    mutation_id: stamp.id(),
    client_edit_time: new Date(stamp.now).toISOString(),
    session_client_id: session.sessionClientId,
  };
}

export function reopenMutation(session: ActiveSession, stamp: Stamp): Mutation {
  return {
    kind: "session_reopen",
    mutation_id: stamp.id(),
    client_edit_time: new Date(stamp.now).toISOString(),
    session_client_id: session.sessionClientId,
  };
}

/** One minute: the least a session that happened can have lasted. */
const MINUTE_MS = 60_000;

/**
 * How long "Log it now" assumes the session took, in minutes: about three
 * minutes a set — lifting plus rest — rounded to five, never under twenty.
 * It only seeds the field the athlete can change; a recap does not know when
 * each set was lifted (see `recapSets`), and the whole session was recorded
 * as starting *now* and lasting eleven seconds before the athlete could say
 * (UAT U11).
 */
export function recapMinutes(lines: readonly PacketLine[]): number {
  const sets = lines.reduce((n, line) => n + line.prescription.sets, 0);
  return Math.max(20, Math.round((sets * 3) / 5) * 5);
}

/**
 * The start and end a recap sends, from what the athlete entered: when it
 * started (a `datetime-local` value in the phone's own zone) and how many
 * minutes it took. A session cannot start or end in the future, so a later
 * start is held to now and an end past now to now; one unreadable entry falls
 * back to the default (an hour ago) rather than blocking the log.
 */
export function recapWindow(
  startedInput: string,
  minutes: number,
  now: number,
): { startedAt: number; endedAt: number } {
  const parsed = new Date(startedInput).getTime();
  const startedAt = Math.min(
    Number.isFinite(parsed) ? parsed : now - 60 * MINUTE_MS,
    now,
  );
  const length = Math.max(
    1,
    Math.round(Number.isFinite(minutes) ? minutes : 1),
  );
  const endedAt = Math.min(startedAt + length * MINUTE_MS, now);
  return { startedAt, endedAt: Math.max(endedAt, startedAt + MINUTE_MS) };
}

/** An epoch as a `datetime-local` value, in the phone's own zone. */
export function toLocalInput(epochMs: number): string {
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Every prescribed working set of every line, as planned: the numbers each
 * line's first set opens with (the packet's pre-fill), marked as a recap.
 */
export function recapSets(
  session: ActiveSession,
  lines: readonly PacketLine[],
  id: () => string,
): LoggedSet[] {
  return lines.flatMap((line) => {
    const draft = draftFor({ ...session, sets: [] }, line);
    return Array.from(
      { length: line.prescription.sets },
      (): LoggedSet => ({
        clientId: id(),
        lineRef: line.line_ref,
        exerciseId: line.exercise_id,
        warmup: false,
        loadKg: draft.loadKg,
        reps: draft.durationS !== null ? null : draft.reps,
        durationS: draft.durationS,
        perSide: line.prescription.per_side === true,
        rir: null,
        rirCapped: false,
        effortSimple: null,
        source: "recap",
      }),
    );
  });
}
