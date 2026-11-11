import type { components } from "@/generated/api/schema";

/**
 * The phone's own store for what it has not uploaded yet: IndexedDB.
 *
 * IndexedDB rather than `localStorage` because the outbox must survive a
 * reload, an app update and a closed tab with no signal, holds structured
 * items in a fixed order, and grows to a day of gym logging — `localStorage`
 * is a few megabytes of synchronous strings. Three object stores:
 *
 * - **`outbox`** — mutations waiting to upload, keyed by an auto-increment
 *   `seq`, so reading the store back in key order *is* the order they were
 *   made in (the order `POST /sync` must apply them in);
 * - **`failed`** — mutations the server refused for good ("Couldn't upload"),
 *   kept with the reason so the athlete can copy or export them; never
 *   retried, never silently dropped;
 * - **`meta`** — the outbox's standing refusal (`blocked`): an update
 *   required or a login expired, remembered across reloads so the badge says
 *   so before the next attempt;
 * - **`packet`** — the last session packet the server prepared
 *   (`GET /strength/packet`, `lib/offline/packet-store.ts`): one record, the
 *   next seven days of strength sessions with every number the gym screen
 *   shows. Added in version 2; an upgrade from 1 keeps the outbox intact.
 * - **`training`** — the session being logged on this phone, keyed by its
 *   planned session id (`components/strength/session-store.ts`): what was
 *   logged, where the screen is and when the rest ends, so a reload or a
 *   crash resumes at the next unlogged set with the timer intact. Added in
 *   version 3; the outbox is the record of what to upload, this is only the
 *   screen's memory of it.
 *
 * A connection is opened per operation and closed after it: the outbox is
 * touched a few times a minute, and a long-lived handle would block the
 * version upgrade a later build's schema needs.
 */

type Schemas = components["schemas"];

/**
 * A set edit carries only the fields it changed. The contract leaves every
 * `SyncSet` field optional, but the generated type marks the ones with a
 * server default as required (openapi-typescript's `defaultNonNullable`) —
 * and sending a default would be an edit: `per_side: false` at a newer stamp
 * overwrites the stored value under the conflict rule.
 */
export type SetPatch = Partial<Schemas["SyncSet"]>;

/** One outbox item: a `POST /sync` mutation, set edits as patches. */
export type Mutation =
  | Exclude<Schemas["Mutation"], { kind: "set_upsert" }>
  | (Omit<Schemas["SetUpsert"], "set"> & { set: SetPatch });

/** Why the outbox is not draining, until the next attempt says otherwise. */
export type Blocked = "update" | "signedOut" | null;

export interface QueuedItem {
  seq: number;
  mutation: Mutation;
  /** When it was queued, epoch milliseconds — the badge's 60 s clock. */
  queuedAt: number;
}

export interface FailedItem {
  id?: number;
  mutation: Mutation;
  /** The server's sentence, shown beside the item. */
  reason: string;
  failedAt: number;
}

const DB_NAME = "arc-offline";
const DB_VERSION = 3;
const OUTBOX = "outbox";
const FAILED = "failed";
const META = "meta";
const PACKET = "packet";
const PACKET_KEY = "current";
const TRAINING = "training";

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(OUTBOX)) {
        db.createObjectStore(OUTBOX, { keyPath: "seq", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains(FAILED)) {
        db.createObjectStore(FAILED, { keyPath: "id", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains(META)) {
        db.createObjectStore(META);
      }
      if (!db.objectStoreNames.contains(PACKET)) {
        db.createObjectStore(PACKET);
      }
      if (!db.objectStoreNames.contains(TRAINING)) {
        db.createObjectStore(TRAINING);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Run `body` in one transaction over `stores`; resolves once it commits. */
async function inTransaction<T>(
  stores: string[],
  mode: IDBTransactionMode,
  body: (tx: IDBTransaction) => Promise<T> | T,
): Promise<T> {
  const db = await open();
  try {
    const tx = db.transaction(stores, mode);
    const done = new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    const result = await body(tx);
    await done;
    return result;
  } finally {
    db.close();
  }
}

/** Append one mutation to the outbox. */
export function addQueued(mutation: Mutation, queuedAt: number): Promise<void> {
  return inTransaction([OUTBOX], "readwrite", (tx) => {
    tx.objectStore(OUTBOX).add({ mutation, queuedAt });
  });
}

/** Everything waiting, oldest first. */
export function queued(): Promise<QueuedItem[]> {
  return inTransaction([OUTBOX], "readonly", (tx) =>
    request(tx.objectStore(OUTBOX).getAll() as IDBRequest<QueuedItem[]>),
  );
}

/**
 * Take back queued items by mutation id, in one transaction. Resolves to the
 * ids that were still waiting and are now gone; an id not returned was
 * already uploaded (or never queued), so taking it back needs a compensating
 * mutation instead.
 */
export function removeQueuedMutations(
  mutationIds: string[],
): Promise<string[]> {
  const wanted = new Set(mutationIds);
  return inTransaction([OUTBOX], "readwrite", async (tx) => {
    const store = tx.objectStore(OUTBOX);
    const items = (await request(store.getAll())) as QueuedItem[];
    const removed: string[] = [];
    for (const item of items) {
      if (wanted.has(item.mutation.mutation_id)) {
        store.delete(item.seq);
        removed.push(item.mutation.mutation_id);
      }
    }
    return removed;
  });
}

/** Drop uploaded items. */
export function removeQueued(seqs: number[]): Promise<void> {
  return inTransaction([OUTBOX], "readwrite", (tx) => {
    const store = tx.objectStore(OUTBOX);
    for (const seq of seqs) store.delete(seq);
  });
}

/** Move one item from the outbox to "Couldn't upload", in one transaction. */
export function moveToFailed(
  item: QueuedItem,
  reason: string,
  failedAt: number,
): Promise<void> {
  return inTransaction([OUTBOX, FAILED], "readwrite", (tx) => {
    tx.objectStore(OUTBOX).delete(item.seq);
    tx.objectStore(FAILED).add({ mutation: item.mutation, reason, failedAt });
  });
}

/** Everything the server refused for good, oldest first. */
export function failed(): Promise<FailedItem[]> {
  return inTransaction([FAILED], "readonly", (tx) =>
    request(tx.objectStore(FAILED).getAll() as IDBRequest<FailedItem[]>),
  );
}

/** Forget the refused items — only on the athlete's say-so. */
export function clearFailed(): Promise<void> {
  return inTransaction([FAILED], "readwrite", (tx) => {
    tx.objectStore(FAILED).clear();
  });
}

export function getBlocked(): Promise<Blocked> {
  return inTransaction([META], "readonly", async (tx) => {
    const value = await request(tx.objectStore(META).get("blocked"));
    return value === "update" || value === "signedOut" ? value : null;
  });
}

export function setBlocked(blocked: Blocked): Promise<void> {
  return inTransaction([META], "readwrite", (tx) => {
    tx.objectStore(META).put(blocked, "blocked");
  });
}

/** A session packet as the phone keeps it: the server's document, unchanged. */
export interface StoredPacket {
  packet: Schemas["PacketRead"];
  /** When this phone stored it, epoch milliseconds. */
  storedAt: number;
}

/** Replace the stored packet. */
export function putPacket(stored: StoredPacket): Promise<void> {
  return inTransaction([PACKET], "readwrite", (tx) => {
    tx.objectStore(PACKET).put(stored, PACKET_KEY);
  });
}

/** The stored packet, or null when this phone has never synced one. */
export function getPacket(): Promise<StoredPacket | null> {
  return inTransaction([PACKET], "readonly", async (tx) => {
    const value = (await request(tx.objectStore(PACKET).get(PACKET_KEY))) as
      | StoredPacket
      | undefined;
    return value ?? null;
  });
}

/** One record of the `training` store, opaque here: its owner types it. */
export function putTraining(key: string, value: unknown): Promise<void> {
  return inTransaction([TRAINING], "readwrite", (tx) => {
    tx.objectStore(TRAINING).put(value, key);
  });
}

export function getTraining<T>(key: string): Promise<T | null> {
  return inTransaction([TRAINING], "readonly", async (tx) => {
    const value = (await request(tx.objectStore(TRAINING).get(key))) as
      | T
      | undefined;
    return value ?? null;
  });
}
