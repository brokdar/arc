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
 *   so before the next attempt.
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
const DB_VERSION = 1;
const OUTBOX = "outbox";
const FAILED = "failed";
const META = "meta";

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
