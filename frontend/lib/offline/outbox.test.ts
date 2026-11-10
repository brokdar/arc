import "fake-indexeddb/auto";

import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type Mutation, queued } from "@/lib/offline/db";
import {
  BADGE_AFTER_MS,
  badgeState,
  enqueue,
  FLUSH_INTERVAL_MS,
  failedExport,
  flush,
  outboxStatus,
  startOutbox,
} from "@/lib/offline/outbox";
import { uuid7 } from "@/lib/offline/uuid7";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * The outbox against the typed `/sync` handler (`tests/mocks/sync-state.ts`),
 * which applies what it is sent the way the server does: idempotent per
 * mutation id, all or nothing per batch, 409 for a newer schema, 401 without
 * a session. IndexedDB is `fake-indexeddb`, a fresh database per test.
 */

const SESSION = uuid7();
const T0 = Date.UTC(2026, 10, 10, 9, 0);

function start(session = SESSION): Mutation {
  return {
    kind: "session_start",
    mutation_id: uuid7(),
    client_edit_time: new Date(T0).toISOString(),
    session_client_id: session,
    start_time: new Date(T0).toISOString(),
  };
}

function logSet(index: number, session = SESSION): Mutation {
  return {
    kind: "set_upsert",
    mutation_id: uuid7(),
    client_edit_time: new Date(T0 + index * 60_000).toISOString(),
    session_client_id: session,
    set_client_id: uuid7(),
    set: { exercise_id: "back_squat", reps: 5, load_kg: 100, set_index: index },
  };
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("flush", () => {
  it("uploads queued mutations once, in order, and empties the queue", async () => {
    const items = [start(), logSet(0), logSet(1), logSet(2)];
    for (const item of items) await enqueue(item);

    expect(await flush()).toBe("drained");

    expect(syncServer().applied).toEqual(items.map((m) => m.mutation_id));
    expect(await queued()).toEqual([]);
    expect((await outboxStatus()).pending).toBe(0);
  });

  it("keeps every item and asks for an update on a version-skew 409", async () => {
    // The phone is on a newer build than the server it reaches.
    syncServer().version = 0;
    const items = [start(), logSet(0), logSet(1)];
    for (const item of items) await enqueue(item);

    expect(await flush()).toBe("updateRequired");

    expect((await queued()).map((q) => q.mutation)).toEqual(items);
    const status = await outboxStatus();
    expect(status.blocked).toBe("update");
    expect(badgeState(status, T0)).toEqual({
      visible: true,
      message: "outbox.updateRequired",
    });
    expect(syncServer().applied).toEqual([]);
  });

  it("keeps every item on a 401 and drains them after login", async () => {
    syncServer().signedIn = false;
    const items = [start(), logSet(0), logSet(1)];
    for (const item of items) await enqueue(item);

    expect(await flush()).toBe("signedOut");
    expect((await queued()).map((q) => q.mutation)).toEqual(items);
    expect((await outboxStatus()).blocked).toBe("signedOut");

    syncServer().signedIn = true;
    expect(await flush()).toBe("drained");

    expect(syncServer().applied).toEqual(items.map((m) => m.mutation_id));
    expect(await queued()).toEqual([]);
    expect((await outboxStatus()).blocked).toBeNull();
  });

  it("moves only the rejected item to Couldn't upload; the rest drain", async () => {
    const orphan = logSet(5, uuid7()); // a session never started
    const items = [start(), logSet(0), orphan, logSet(1)];
    for (const item of items) await enqueue(item);

    expect(await flush()).toBe("drained");

    expect(syncServer().applied).toEqual(
      [items[0], items[1], items[3]].map((m) => m.mutation_id),
    );
    const status = await outboxStatus();
    expect(status.pending).toBe(0);
    expect(status.failed.map((f) => f.mutation)).toEqual([orphan]);
    expect(status.failed[0].reason).toMatch(/no session/);
    expect(badgeState(status, T0)).toEqual({
      visible: true,
      message: "outbox.failed",
    });
  });

  it("keeps the queue untouched while the server cannot be reached", async () => {
    syncServer().healthy = false;
    await enqueue(start());

    expect(await flush()).toBe("offline");

    expect(await queued()).toHaveLength(1);
    expect(syncServer().requests).toBe(0);
  });

  it("re-sending an already applied item is harmless", async () => {
    const item = start();
    await enqueue(item);
    await flush();
    await enqueue(item);

    expect(await flush()).toBe("drained");
    expect(syncServer().applied).toEqual([item.mutation_id]);
  });
});

describe("the badge", () => {
  it("stays hidden for a queue younger than 60 s and shows after", async () => {
    await enqueue(start(), T0);
    const status = await outboxStatus();

    expect(badgeState(status, T0 + BADGE_AFTER_MS - 1).visible).toBe(false);
    expect(badgeState(status, T0 + BADGE_AFTER_MS)).toEqual({
      visible: true,
      message: "outbox.waiting",
    });
  });

  it("is hidden with nothing queued and nothing failed", async () => {
    expect(badgeState(await outboxStatus(), T0).visible).toBe(false);
  });
});

describe("Couldn't upload", () => {
  it("exports the rejected items as JSON a person can keep", async () => {
    const orphan = logSet(0, uuid7());
    await enqueue(orphan);
    await flush();

    const exported = JSON.parse(failedExport((await outboxStatus()).failed));
    expect(exported).toEqual([
      expect.objectContaining({ mutation: orphan, reason: expect.any(String) }),
    ]);
  });
});

describe("startOutbox", () => {
  it("flushes on launch, on online, on becoming visible and every 30 s", () => {
    vi.useFakeTimers();
    const run = vi.fn(async () => "drained" as const);
    const stop = startOutbox(run);
    expect(run).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new Event("online"));
    expect(run).toHaveBeenCalledTimes(2);

    document.dispatchEvent(new Event("visibilitychange"));
    expect(run).toHaveBeenCalledTimes(3);

    vi.advanceTimersByTime(FLUSH_INTERVAL_MS);
    expect(run).toHaveBeenCalledTimes(4);

    stop();
    vi.advanceTimersByTime(FLUSH_INTERVAL_MS);
    window.dispatchEvent(new Event("online"));
    expect(run).toHaveBeenCalledTimes(4);
  });
});
