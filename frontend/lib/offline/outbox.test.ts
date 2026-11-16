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
  withdrawSession,
} from "@/lib/offline/outbox";
import { uuid7 } from "@/lib/offline/uuid7";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import { applyBatch, syncServer } from "@/tests/mocks/sync-state";

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
  it("uploads what was queued during an upload when a flush is asked for then", async () => {
    // The logging screen finishes a session while the outbox's own flush is
    // mid-upload: the finish must not wait 30 s for the next tick.
    let entered = false;
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/sync", async ({ request, response }) => {
        entered = true;
        await gate;
        const answer = applyBatch(await request.json());
        if (answer.status !== 200) throw new Error("unexpected refusal");
        return response(200).json(answer.body);
      }),
    );
    const first = start();
    await enqueue(first);
    const uploading = flush();
    await vi.waitFor(() => expect(entered).toBe(true));

    const late = logSet(0);
    await enqueue(late);
    const asked = flush();
    release();

    expect(await asked).toBe("drained");
    expect(await uploading).toBe("drained");
    expect(syncServer().applied).toEqual([first.mutation_id, late.mutation_id]);
    expect(await queued()).toEqual([]);
  });

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

  // UAT U6: the browser's `online` event fires before the network answers, so
  // the `/health` probe in that first flush failed and the queue then waited
  // for the 30 s tick (about 20 s after the reconnect).
  it("retries a flush the online event started until the network answers", async () => {
    vi.useFakeTimers();
    const outcomes = ["offline", "offline", "drained"] as const;
    let call = 0;
    const run = vi.fn(async () =>
      call++ === 0 ? ("drained" as const) : outcomes[call - 2],
    );
    const stop = startOutbox(run);
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(run).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(run).toHaveBeenCalledTimes(4);

    // Drained: no further retries before the regular tick.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(run).toHaveBeenCalledTimes(4);
    stop();
  });

  it("stops retrying when it is stopped", async () => {
    vi.useFakeTimers();
    const run = vi.fn(async () => "offline" as const);
    const stop = startOutbox(run);
    await vi.advanceTimersByTimeAsync(0);
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    const before = run.mock.calls.length;
    stop();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(run).toHaveBeenCalledTimes(before);
  });
});

describe("withdrawSession", () => {
  it("takes a session's queued items back, and says whether the server knew it", async () => {
    syncServer().healthy = false;
    const other = uuid7();
    const first = start();
    await enqueue(first);
    await enqueue(logSet(0, other));

    const out = await withdrawSession(SESSION);

    expect(out).toEqual({ serverKnows: false });
    expect(
      (await queued()).map((i) =>
        "session_client_id" in i.mutation ? i.mutation.session_client_id : null,
      ),
    ).toEqual([other]);
  });

  it("knows the server has it when the start already uploaded", async () => {
    await enqueue(start());
    await flush();
    await enqueue(logSet(0));

    expect(await withdrawSession(SESSION)).toEqual({ serverKnows: true });
    expect(await queued()).toEqual([]);
  });
});
