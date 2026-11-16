import { addedLine } from "@/components/strength/session-edits/edits";
import {
  type ActiveSession,
  isPlannedLine,
  type LoggedSet,
  nextOpenLine,
  type Packet,
  type PacketLine,
  type PacketSession,
  type Stamp,
  UNPLANNED,
} from "@/components/strength/session-model";
import { linesWithSwaps } from "@/components/strength/swap/swap-model";
import type { components } from "@/generated/api/schema";
import type { Mutation } from "@/lib/offline/db";

/**
 * One session, two phones: "Continue here?".
 *
 * Every `POST /sync` answer lists the active sessions and the device holding
 * each (`backend/app/services/sync.py`). A phone opening a planned session
 * that another device holds — learned online, at open — asks "Continue
 * here?"; accepting reads the session's sets from the server, rebuilds the
 * screen's state from them under the **same** session client id, and claims
 * it (`session_claim`). The phone that held it reads, on its next sync, that
 * another device does now, and turns read-only.
 *
 * **Read-only is a screen state, not a refusal.** Sets the first phone logged
 * offline before it learned of the handover still upload, and the server
 * keeps them beside the second phone's (`test_sync_conflicts.py`, AC-42 b):
 * two phones offline at once is a merge, never a loss. Displaced: refusing a
 * non-holder's uploads, which would throw away sets the athlete really did.
 * Offline, nobody knows who holds what, so nothing is asked and nothing is
 * locked.
 */

type ActiveSessionRead = components["schemas"]["ActiveSessionRead"];
type SessionRead = components["schemas"]["SessionRead"];

/** The active session another device holds for this plan, if any. */
export function heldElsewhere(
  active: readonly ActiveSessionRead[] | null,
  plannedSessionId: string,
  me: string,
): ActiveSessionRead | null {
  if (!active || plannedSessionId === UNPLANNED) return null;
  return (
    active.find(
      (one) =>
        one.planned_session_id === plannedSessionId &&
        one.device_id !== null &&
        one.device_id !== me,
    ) ?? null
  );
}

/** Whether this phone has handed `sessionClientId` to another device. */
export function handedOver(
  active: readonly ActiveSessionRead[] | null,
  sessionClientId: string,
  me: string | null,
): boolean {
  if (!active || !me) return false;
  const held = active.find((one) => one.session_client_id === sessionClientId);
  return Boolean(held?.device_id && held.device_id !== me);
}

/**
 * The screen's state for a session read from the server, **as the other phone
 * left it**: the server keeps no copy of the screen, only the sets, so the
 * edits are read back off what the sets say.
 *
 * - **A swap**: a set filed under a planned line but lifted as another
 *   exercise (it carries `replaced_exercise_id`) means the line was swapped,
 *   so the line shows and logs that exercise (`session.swaps`). Before, the
 *   taking-over phone rebuilt the plan: Back Squat instead of the swapped Box
 *   Squat, and its next set uploaded as Back Squat beside the other phone's
 *   Box Squat (UAT B3).
 * - **An added exercise** is a line the planned session does not have: one
 *   per exercise of a set the server holds with no planned line.
 * - **The order** is the sets' own: the server stores them grouped by
 *   exercise in the session's exercise order (`edits.ts`), so the exercises
 *   with sets keep that order and an exercise with no set yet keeps its place
 *   after the one that preceded it in the plan.
 * - **Extra sets**: a line with more working sets than prescribed had sets
 *   added; its set count is raised to match.
 * - **Moved sets** need nothing: a moved set is stored under its new
 *   exercise and planned line.
 *
 * What cannot be read back is an edit that left no set: a swap, an extra set
 * or an added exercise not yet lifted. Those are the other phone's to redo;
 * the server never hears of them (`swap` is applied as a preference only),
 * and a layout mutation to carry them was displaced as a schema change for
 * the edge of an edge — the hand-over happens mid-exercise, sets in hand.
 * Notes need nothing here: they live on the server per exercise and arrive in
 * the packet.
 */
export function takeOver(
  read: SessionRead,
  active: ActiveSessionRead,
  planned: PacketSession,
  packet: Packet,
): ActiveSession {
  const lines: PacketLine[] = [...planned.lines];
  const swaps: Record<string, string> = {};
  const ordered = [...read.logged_sets].sort(
    (a, b) => a.set_index - b.set_index,
  );
  const sets: LoggedSet[] = ordered.map((set) => {
    let line =
      lines.find((one) => one.line_ref === set.planned_line_ref) ??
      lines.find(
        (one) =>
          !isPlannedLine(one.line_ref) && one.exercise_id === set.exercise_id,
      );
    if (!line) {
      const exercise = packet.library.find((e) => e.id === set.exercise_id);
      line = exercise ? addedLine(packet, exercise, set.client_id) : undefined;
      if (line) lines.push(line);
    }
    if (
      line &&
      isPlannedLine(line.line_ref) &&
      set.replaced_exercise_id &&
      set.exercise_id &&
      set.exercise_id !== line.exercise_id
    ) {
      swaps[line.line_ref] = set.exercise_id;
    }
    return {
      clientId: set.client_id,
      lineRef: line?.line_ref ?? set.planned_line_ref ?? "",
      exerciseId: set.exercise_id ?? "",
      warmup: set.set_kind === "warmup",
      loadKg: set.load_kg,
      reps: set.reps,
      durationS: set.duration_s,
      perSide: set.per_side,
      rir: set.rir,
      rirCapped: set.rir_capped,
      effortSimple: set.effort_simple,
      ...(set.replaced_exercise_id
        ? { replacedExerciseId: set.replaced_exercise_id }
        : {}),
      ...(set.completed_at
        ? { completedAt: Date.parse(set.completed_at) }
        : {}),
    };
  });
  const shaped = inSetOrder(lines, sets).map((line) => {
    const worked = sets.filter(
      (set) => set.lineRef === line.line_ref && !set.warmup,
    ).length;
    return worked > line.prescription.sets
      ? { ...line, prescription: { ...line.prescription, sets: worked } }
      : line;
  });
  const session: ActiveSession = {
    plannedSessionId: planned.planned_session_id,
    sessionClientId: active.session_client_id,
    startedAt: Date.parse(active.start_time),
    sets,
    lineIndex: 0,
    rest: null,
    finished: false,
    lines: shaped,
    ...(Object.keys(swaps).length > 0 ? { swaps } : {}),
  };
  const open = nextOpenLine(
    session,
    linesWithSwaps(session, shaped, packet),
    0,
  );
  return { ...session, lineIndex: open ?? 0 };
}

/**
 * `lines` in the order `sets` (stored order) stand in: lines with sets first
 * come by their first set; a line with none stays right after the line that
 * preceded it in `lines` (or first, if none did).
 */
function inSetOrder(
  lines: readonly PacketLine[],
  sets: readonly LoggedSet[],
): PacketLine[] {
  const withSets: PacketLine[] = [];
  for (const set of sets) {
    const line = lines.find((one) => one.line_ref === set.lineRef);
    if (line && !withSets.includes(line)) withSets.push(line);
  }
  const result = [...withSets];
  lines.forEach((line, i) => {
    if (withSets.includes(line)) return;
    const before = lines
      .slice(0, i)
      .reverse()
      .find((one) => result.includes(one));
    result.splice(before ? result.indexOf(before) + 1 : 0, 0, line);
  });
  return result;
}

export function claimMutation(
  session: ActiveSession,
  deviceId: string,
  stamp: Stamp,
): Mutation {
  return {
    kind: "session_claim",
    mutation_id: stamp.id(),
    client_edit_time: new Date(stamp.now).toISOString(),
    session_client_id: session.sessionClientId,
    device_id: deviceId,
  };
}
