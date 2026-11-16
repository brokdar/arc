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
 * The screen's state for a session read from the server: its sets filed
 * under the lines they answered, an exercise the other phone added becoming
 * an added line here.
 */
export function takeOver(
  read: SessionRead,
  active: ActiveSessionRead,
  planned: PacketSession,
  packet: Packet,
): ActiveSession {
  const lines: PacketLine[] = [...planned.lines];
  const sets: LoggedSet[] = [...read.logged_sets]
    .sort((a, b) => a.set_index - b.set_index)
    .map((set) => {
      let line =
        lines.find((one) => one.line_ref === set.planned_line_ref) ??
        lines.find(
          (one) =>
            !isPlannedLine(one.line_ref) && one.exercise_id === set.exercise_id,
        );
      if (!line) {
        const exercise = packet.library.find((e) => e.id === set.exercise_id);
        line = exercise
          ? addedLine(packet, exercise, set.client_id)
          : undefined;
        if (line) lines.push(line);
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
        ...(set.completed_at
          ? { completedAt: Date.parse(set.completed_at) }
          : {}),
      };
    });
  const session: ActiveSession = {
    plannedSessionId: planned.planned_session_id,
    sessionClientId: active.session_client_id,
    startedAt: Date.parse(active.start_time),
    sets,
    lineIndex: 0,
    rest: null,
    finished: false,
    lines,
  };
  return { ...session, lineIndex: nextOpenLine(session, lines, 0) ?? 0 };
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
