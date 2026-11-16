import type { components } from "@/generated/api/schema";
import { apiClient } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n/catalogue";
import {
  addQueued,
  type Blocked,
  type FailedItem,
  failed,
  getBlocked,
  type Mutation,
  moveToFailed,
  type QueuedItem,
  queued,
  removeQueued,
  removeQueuedMutations,
  setBlocked,
} from "@/lib/offline/db";

/**
 * The outbox: every gym action is saved on the phone first and reaches the
 * server once, in order, whatever the signal does.
 *
 * A screen calls `enqueue` with a mutation (ids from `uuid7`) and moves on —
 * it never waits for the network. `flush` uploads the queue to `POST /sync`
 * in slices of `BATCH_SIZE`, oldest first, and `startOutbox` runs it on
 * launch, when the app becomes visible, when the browser says it is online,
 * and every `FLUSH_INTERVAL_MS` while the app is open. Nothing here depends on
 * background sync or notifications: the gym floor gets neither reliably.
 *
 * Before uploading it probes `/health`, because `navigator.onLine` says only
 * that a network interface is up — a basement gym's captive Wi-Fi is "online".
 *
 * What each answer does to the queue (the server side is
 * `backend/app/api/routes/sync.py`):
 *
 * - **200** — the slice is removed. A replayed item answers `already_applied`
 *   and is removed the same way: the server has it.
 * - **401** — everything stays; the badge asks for a login, and the flush the
 *   next launch runs after logging in drains it.
 * - **409 `schema_version_unsupported`** — everything stays and the badge says
 *   "Update arc to upload". Not retried in a loop: the server will not
 *   understand this batch until the app is updated.
 * - **422** — nothing in the batch was applied; the one item it names moves to
 *   "Couldn't upload" (copy/export, never retried) and the rest are sent again
 *   at once.
 * - **no answer, or any other status** — everything stays for the next try.
 */

export const SCHEMA_VERSION = 1;
/** The server's `MAX_BATCH_MUTATIONS`. */
export const BATCH_SIZE = 500;
/** A queue younger than this is normal — a set logged between bars of signal. */
export const BADGE_AFTER_MS = 60_000;
export const FLUSH_INTERVAL_MS = 30_000;

export type FlushOutcome =
  | "drained"
  | "offline"
  | "signedOut"
  | "updateRequired"
  | "error";

type ActiveSessionRead = components["schemas"]["ActiveSessionRead"];

/**
 * The sessions the server last said were active, and the device holding each
 * — every 200 from `POST /sync` carries the list. Null until this page has
 * heard from the server: offline, nobody knows who holds what, and the
 * logging screen keeps logging (two phones offline both keep their sets).
 */
let active: ActiveSessionRead[] | null = null;
let watchers = 0;

export function activeSessions(): ActiveSessionRead[] | null {
  return active;
}

/**
 * Ask every flush to read the active sessions, even with nothing queued —
 * the logging screen does while it is open, so a phone that handed its
 * session to another learns so within one sync. An empty batch is what the
 * server answers that with. Returns the function that stops asking.
 */
/** Forget what the server said — a test's fresh start. */
export function resetActiveSessions(): void {
  active = null;
}

export function watchActiveSessions(): () => void {
  watchers += 1;
  return () => {
    watchers -= 1;
  };
}

export interface OutboxStatus {
  pending: number;
  /** When the oldest waiting item was queued, epoch ms; null when none. */
  oldestQueuedAt: number | null;
  failed: FailedItem[];
  blocked: Blocked;
}

const listeners = new Set<() => void>();

/** Be told whenever the queue or its state changes. Returns the unsubscribe. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** Save one mutation for upload. Resolves once it is on the phone's disk. */
export async function enqueue(
  mutation: Mutation,
  now: number = Date.now(),
): Promise<void> {
  await addQueued(mutation, now);
  notify();
}

let inflight: Promise<FlushOutcome> | null = null;

/**
 * Take back mutations that may not have left the phone yet — the logging
 * screen's Undo. Waits for a flush in progress first, so an item being
 * uploaded right now is either still queued afterwards (and removed) or
 * reported as gone. Resolves to the ids that had already reached the server:
 * the caller sends a compensating mutation for those.
 */
export async function withdraw(mutationIds: string[]): Promise<string[]> {
  if (inflight) await inflight.catch(() => undefined);
  const removed = new Set(await removeQueuedMutations(mutationIds));
  notify();
  return mutationIds.filter((id) => !removed.has(id));
}

let again = false;

/**
 * Upload what is queued. One flush at a time; a second call joins the first
 * — and, because the first read the queue before the caller added to it,
 * makes the first go round once more when it drained cleanly. Without that,
 * a session finished during the outbox's own upload waited for the next
 * 30-second tick.
 */
export function flush(): Promise<FlushOutcome> {
  if (inflight) {
    again = true;
    return inflight;
  }
  inflight = (async () => {
    let outcome: FlushOutcome;
    do {
      again = false;
      outcome = await drain();
    } while (again && outcome === "drained");
    return outcome;
  })().finally(() => {
    inflight = null;
    again = false;
    notify();
  });
  return inflight;
}

async function reachable(): Promise<boolean> {
  try {
    const { response } = await apiClient.GET("/health");
    return response.ok;
  } catch {
    return false;
  }
}

/** The indexes a framework 422 points at: `loc` is `[body, mutations, i, ...]`. */
function indexesIn(detail: unknown): number[] {
  if (!Array.isArray(detail)) return [];
  const out = new Set<number>();
  for (const entry of detail) {
    const loc = (entry as { loc?: unknown[] })?.loc;
    if (Array.isArray(loc) && typeof loc[2] === "number") out.add(loc[2]);
  }
  return [...out];
}

async function drain(): Promise<FlushOutcome> {
  let items: QueuedItem[] = await queued();
  if (items.length === 0) {
    await setBlocked(null);
    if (watchers > 0 && (await reachable())) await readActive();
    return "drained";
  }
  if (!(await reachable())) return "offline";

  while (items.length > 0) {
    const slice = items.slice(0, BATCH_SIZE);
    let answer: Awaited<ReturnType<typeof post>>;
    try {
      answer = await post(slice);
    } catch {
      return "offline";
    }
    const { data, error, response } = answer;
    if (response.ok) {
      if (data) active = data.active_sessions;
      await removeQueued(slice.map((item) => item.seq));
      items = items.slice(slice.length);
      continue;
    }
    if (response.status === 401) {
      await setBlocked("signedOut");
      return "signedOut";
    }
    if (
      response.status === 409 &&
      (error as { code?: string } | undefined)?.code ===
        "schema_version_unsupported"
    ) {
      await setBlocked("update");
      return "updateRequired";
    }
    if (response.status === 422) {
      const body = (error ?? {}) as { detail?: unknown; mutation_id?: string };
      const named = slice.filter(
        (item) => item.mutation.mutation_id === body.mutation_id,
      );
      const rejected =
        named.length > 0
          ? named
          : indexesIn(body.detail).length > 0
            ? indexesIn(body.detail)
                .map((index) => slice[index])
                .filter(Boolean)
            : // Nothing attributable: the slice itself is unusable, and
              // keeping it would resend it forever. It is kept for export.
              slice;
      const reason =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail ?? "refused");
      const now = Date.now();
      for (const item of rejected) await moveToFailed(item, reason, now);
      const gone = new Set(rejected.map((item) => item.seq));
      items = items.filter((item) => !gone.has(item.seq));
      notify();
      continue;
    }
    return "error";
  }
  await setBlocked(null);
  return "drained";
}

/** An empty batch: applies nothing, answers with the active sessions. */
async function readActive(): Promise<void> {
  try {
    const { data } = await post([]);
    if (data) active = data.active_sessions;
  } catch {
    // Unreachable after all: keep what was last heard.
  }
}

function post(slice: QueuedItem[]) {
  return apiClient.POST("/api/v1/sync", {
    body: {
      schema_version: SCHEMA_VERSION,
      // Set patches omit defaulted fields the generated type calls required;
      // the contract does not (see `SetPatch`).
      mutations: slice.map(
        (item) => item.mutation,
      ) as components["schemas"]["Mutation"][],
    },
  });
}

/** What the badge and the "Couldn't upload" list read. */
export async function outboxStatus(): Promise<OutboxStatus> {
  const [waiting, refused, blocked] = await Promise.all([
    queued(),
    failed(),
    getBlocked(),
  ]);
  return {
    pending: waiting.length,
    oldestQueuedAt: waiting.length > 0 ? waiting[0].queuedAt : null,
    failed: refused,
    blocked: waiting.length > 0 ? blocked : null,
  };
}

/**
 * Whether the badge shows, and what it says.
 *
 * Shown after 60 s of waiting or on a failure — an update required, a login
 * expired, an item refused — and never for a younger queue: a set logged in
 * a dead spot and uploaded twenty seconds later is the outbox working, and a
 * badge flickering on every set would teach the athlete to ignore it.
 */
export function badgeState(
  status: OutboxStatus,
  now: number,
): { visible: boolean; message: MessageKey | null } {
  if (status.blocked === "update") {
    return { visible: true, message: "outbox.updateRequired" };
  }
  if (status.blocked === "signedOut") {
    return { visible: true, message: "outbox.signedOut" };
  }
  if (status.failed.length > 0) {
    return { visible: true, message: "outbox.failed" };
  }
  if (
    status.oldestQueuedAt !== null &&
    now - status.oldestQueuedAt >= BADGE_AFTER_MS
  ) {
    return { visible: true, message: "outbox.waiting" };
  }
  return { visible: false, message: null };
}

/** "Couldn't upload" as JSON, for copying or saving to a file. */
export function failedExport(items: FailedItem[]): string {
  return JSON.stringify(
    items.map(({ mutation, reason, failedAt }) => ({
      mutation,
      reason,
      failed_at: new Date(failedAt).toISOString(),
    })),
    null,
    2,
  );
}

/**
 * Run `run` now and on every occasion the outbox should try again: the app
 * becoming visible, the browser going online, and every 30 s while open.
 * Returns the function that stops it.
 */
export function startOutbox(
  run: () => Promise<FlushOutcome> = flush,
): () => void {
  const attempt = () => {
    void run();
  };
  const onVisible = () => {
    if (document.visibilityState !== "hidden") attempt();
  };
  attempt();
  window.addEventListener("online", attempt);
  document.addEventListener("visibilitychange", onVisible);
  const timer = window.setInterval(attempt, FLUSH_INTERVAL_MS);
  return () => {
    window.removeEventListener("online", attempt);
    document.removeEventListener("visibilitychange", onVisible);
    window.clearInterval(timer);
  };
}
