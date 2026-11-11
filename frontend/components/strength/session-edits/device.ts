import { getTraining, putTraining } from "@/lib/offline/db";
import { uuid7 } from "@/lib/offline/uuid7";

/**
 * This phone's id: minted once and kept in the `training` store, sent with
 * `session_start` and `session_claim` so the server can say which device
 * holds a session (`backend/app/services/sync.py` `active_sessions`). Without
 * IndexedDB it lasts the page's life — the phone cannot hold a session past a
 * reload then anyway.
 */

const KEY = "device";
let fallback: string | null = null;

export async function deviceId(now: number = Date.now()): Promise<string> {
  try {
    const stored = await getTraining<string>(KEY);
    if (stored) return stored;
    const minted = uuid7(now);
    await putTraining(KEY, minted);
    return minted;
  } catch {
    fallback ??= uuid7(now);
    return fallback;
  }
}
