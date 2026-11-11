import type { ActiveSession } from "@/components/strength/session-model";
import { getTraining, putTraining } from "@/lib/offline/db";

/**
 * The logging screen's memory on this phone: one `ActiveSession` per planned
 * session, in IndexedDB's `training` store (`lib/offline/db.ts`).
 *
 * Written after every action, so a reload, a crash or a phone that killed the
 * tab in the locker comes back to the next unlogged set with the rest timer's
 * stored end time. It is not what reaches the server — the outbox is — so
 * losing it (a private window with no IndexedDB) costs the resume, never a
 * logged set.
 */

export async function loadSession(
  plannedSessionId: string,
): Promise<ActiveSession | null> {
  try {
    return await getTraining<ActiveSession>(plannedSessionId);
  } catch {
    return null;
  }
}

export async function saveSession(session: ActiveSession): Promise<void> {
  try {
    await putTraining(session.plannedSessionId, session);
  } catch {
    // No IndexedDB: the screen still works, it just cannot resume.
  }
}
