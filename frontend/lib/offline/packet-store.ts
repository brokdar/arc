import type { components } from "@/generated/api/schema";
import { apiClient } from "@/lib/api/client";
import { getPacket, putPacket, type StoredPacket } from "@/lib/offline/db";
import { type FlushOutcome, flush } from "@/lib/offline/outbox";

/**
 * The session packet on the phone: the next seven days of strength sessions,
 * prepared by the server (`backend/app/services/packet.py`) and kept in
 * IndexedDB so a gym with no signal still has every number.
 *
 * **The phone renders, it does not decide.** Pre-fills, warm-ups, plates and
 * the effort question all arrive computed; nothing here derives a training
 * number. The store keeps the document exactly as served.
 *
 * It is refreshed on sync: `syncNow` uploads the outbox first — so the last
 * numbers the server prepares include the sets just uploaded — and then
 * fetches the packet, at most once per `REFRESH_EVERY_MS` while a stored one
 * is fresh, because the outbox's 30-second tick would otherwise rebuild it
 * twice a minute. A failed fetch keeps the stored packet: an old packet is
 * still the athlete's plan, and losing it to a dropped connection would leave
 * the gym with nothing.
 */

type Packet = components["schemas"]["PacketRead"];
type PacketSession = components["schemas"]["PacketSessionRead"];

/** The newest packet layout this build understands. */
export const PACKET_SCHEMA_VERSION = 1;
/** A packet covers seven days; past that it is shown as needing a refresh. */
export const STALE_AFTER_MS = 7 * 24 * 60 * 60 * 1000;
/** How often a sync re-fetches a packet that is already stored. */
export const REFRESH_EVERY_MS = 5 * 60 * 1000;

export type RefreshOutcome = "stored" | "offline" | "refused";

/** The packet this phone holds, or null when it has never synced one. */
export function storedPacket(): Promise<StoredPacket | null> {
  return getPacket();
}

/**
 * Fetch the packet and store it. Anything but a usable answer — no network,
 * an error status, or a layout newer than this build reads — keeps what is
 * stored.
 */
export async function refreshPacket(
  now: number = Date.now(),
): Promise<RefreshOutcome> {
  let answer: Awaited<ReturnType<typeof fetchPacket>>;
  try {
    answer = await fetchPacket();
  } catch {
    return "offline";
  }
  const { data, response } = answer;
  if (!response.ok || !data) return "refused";
  if (data.schema_version > PACKET_SCHEMA_VERSION) return "refused";
  await putPacket({ packet: data, storedAt: now });
  return "stored";
}

function fetchPacket() {
  return apiClient.GET("/api/v1/strength/packet");
}

/**
 * One sync: upload the outbox, then refresh the packet when the stored one
 * is missing or older than `REFRESH_EVERY_MS`. Returns the outbox's outcome,
 * so it can stand in for `flush` in `startOutbox`.
 */
export async function syncNow(
  now: () => number = Date.now,
): Promise<FlushOutcome> {
  const outcome = await flush();
  if (outcome === "signedOut" || outcome === "offline") return outcome;
  try {
    const stored = await getPacket();
    if (stored === null || now() - stored.storedAt >= REFRESH_EVERY_MS) {
      await refreshPacket(now());
    }
  } catch {
    // No IndexedDB (a private window): there is nowhere to keep a packet.
  }
  return outcome;
}

/**
 * Whether a packet is past its window: generated more than seven days ago,
 * so every session it carries has had its day. Judged on the server's
 * `generated_at`, because that is what the window was computed from.
 */
export function isStale(packet: Packet, now: number): boolean {
  return now - Date.parse(packet.generated_at) > STALE_AFTER_MS;
}

/**
 * The sessions Today lists from a packet: the ones dated `today`, or — when
 * it has none for today, as a stale packet never does — every session it
 * holds, so an old packet is still something to train from.
 */
export function sessionsForToday(
  packet: Packet,
  today: string,
): PacketSession[] {
  const todays = packet.sessions.filter((session) => session.date === today);
  return todays.length > 0 ? todays : packet.sessions;
}
