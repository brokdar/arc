import type { Packet } from "@/components/strength/session-model";
import { getTraining, putTraining } from "@/lib/offline/db";

/**
 * Exercise notes on this phone, so a note written on Monday shows on that
 * exercise in Thursday's session with no signal.
 *
 * Two sources: the packet, which carries every note the server holds
 * (`backend/app/services/packet.py`), and the notes saved here since — kept
 * in the `training` store under one key until a newer packet has them.
 * **A note saved here wins over a packet generated before it was saved; a
 * packet generated after it wins over it**, because the outbox uploads
 * before the packet is fetched (`lib/offline/packet-store.ts` `syncNow`), so
 * a newer packet already includes the note — or a later edit of it made on
 * another device or by the coach. An empty note is kept as a tombstone, so
 * deleting a note hides the packet's stale copy until a newer packet arrives.
 */

const KEY = "notes";

export interface LocalNote {
  text: string;
  /** Epoch ms it was saved on this phone. */
  savedAt: number;
}

export type LocalNotes = Record<string, LocalNote>;

export async function loadNotes(): Promise<LocalNotes> {
  try {
    return (await getTraining<LocalNotes>(KEY)) ?? {};
  } catch {
    return {};
  }
}

export async function saveNote(
  exerciseId: string,
  text: string,
  savedAt: number,
): Promise<LocalNotes> {
  const notes = { ...(await loadNotes()), [exerciseId]: { text, savedAt } };
  try {
    await putTraining(KEY, notes);
  } catch {
    // No IndexedDB: the note still uploads through the outbox.
  }
  return notes;
}

/** The note to show on one exercise, or null when there is none. */
export function noteFor(
  exerciseId: string,
  packet: Packet,
  local: LocalNotes,
): string | null {
  const mine = local[exerciseId];
  if (mine && mine.savedAt > Date.parse(packet.generated_at)) {
    return mine.text || null;
  }
  const served = packet.notes.find((n) => n.exercise_id === exerciseId);
  return served?.text || null;
}
