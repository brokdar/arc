import "fake-indexeddb/auto";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import {
  logNext,
  MONDAY_ID,
  menu,
  openScreen,
  outbox,
  storePacket,
} from "@/components/strength/session-edits/test-kit";
import type { ActiveSession } from "@/components/strength/session-model";
import { putTraining } from "@/lib/offline/db";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * The session's life beyond logging: "Log it now", the 24-hour reopen, the
 * three-hour close of an abandoned session, and a session with no plan. The
 * network is down throughout, so what each writes stays in the outbox to be
 * read; the server's half of each rule is `test_sync_api.py`.
 */

const HOUR = 60 * 60 * 1000;

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  syncServer().healthy = false;
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

function stored(overrides: Partial<ActiveSession>): ActiveSession {
  const now = Date.now();
  return {
    plannedSessionId: MONDAY_ID,
    sessionClientId: "0199c000-0000-7000-8000-0000000aa001",
    startedAt: now - 4 * HOUR,
    sets: [],
    lineIndex: 0,
    rest: null,
    finished: false,
    ...overrides,
  };
}

function rowSet(n: number, completedAt: number) {
  return {
    clientId: `0199c000-0000-7000-8000-0000000bb00${n}`,
    lineRef: "0.0",
    exerciseId: "single_arm_dumbbell_row",
    warmup: false,
    loadKg: 20,
    reps: 10,
    durationS: null,
    perSide: true,
    rir: null,
    rirCapped: false,
    effortSimple: null,
    completedAt,
  };
}

describe("Log it now", () => {
  it("✓ all as planned stores every prescribed set as a recap", async () => {
    window.history.replaceState({}, "", "/?recap=1");
    await storePacket();
    render(<LoggingScreen plannedSessionId={MONDAY_ID} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Log every set as planned" }),
    );
    await screen.findByRole("heading", { name: "Session saved" });

    const items = await outbox();
    expect(items.map((m) => m.kind)).toEqual([
      "session_start",
      ...Array(6).fill("set_upsert"),
      "session_finish",
    ]);
    const sets = items.flatMap((m) => (m.kind === "set_upsert" ? [m.set] : []));
    for (const set of sets) {
      expect(set.source).toBe("recap");
      expect(set.completed_at).toBeUndefined();
    }
    expect(sets.map((s) => s.exercise_id)).toEqual([
      ...Array(3).fill("single_arm_dumbbell_row"),
      ...Array(3).fill("front_plank"),
    ]);
  });
});

describe("abandoned sessions close at their last set", () => {
  it("three hours after the last set it opens as saved, ended at that set", async () => {
    const last = Date.now() - 3 * HOUR - 60_000;
    await storePacket();
    await putTraining(
      MONDAY_ID,
      stored({ sets: [rowSet(1, last - 3 * 60_000), rowSet(2, last)] }),
    );
    render(<LoggingScreen plannedSessionId={MONDAY_ID} />);

    await screen.findByRole("heading", { name: "Session saved" });
    const finish = (await outbox()).find((m) => m.kind === "session_finish");
    expect(finish).toMatchObject({ end_time: new Date(last).toISOString() });
  });

  it("two hours fifty-nine after it, the session resumes", async () => {
    const last = Date.now() - 3 * HOUR + 60_000;
    await storePacket();
    await putTraining(MONDAY_ID, stored({ sets: [rowSet(1, last)] }));
    render(<LoggingScreen plannedSessionId={MONDAY_ID} />);

    await screen.findByRole("heading", { name: "Session in progress" });
    expect(await outbox()).toEqual([]);
  });
});

describe("reopening a finished session", () => {
  it("is offered after Finish and writes session_reopen", async () => {
    await storePacket();
    await openScreen();
    await logNext();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Finish without rating" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Reopen the session" }),
    );

    await screen.findByRole("heading", { name: "Session in progress" });
    expect((await outbox()).map((m) => m.kind).at(-1)).toBe("session_reopen");
  });

  it("is gone 24 hours after the end", async () => {
    await storePacket();
    await putTraining(
      MONDAY_ID,
      stored({ finished: true, endedAt: Date.now() - 24 * HOUR - 60_000 }),
    );
    render(<LoggingScreen plannedSessionId={MONDAY_ID} />);

    await screen.findByRole("heading", { name: "Session saved" });
    expect(
      screen.queryByRole("button", { name: "Reopen the session" }),
    ).toBeNull();
  });
});

describe("a session with no plan", () => {
  it("starts empty, takes an exercise from the library and logs it", async () => {
    await storePacket();
    render(<LoggingScreen plannedSessionId="unplanned" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Add an exercise" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Add an exercise",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Add Back Squat" }),
    );
    await screen.findByRole("heading", { name: "Back Squat", level: 1 });
    await logNext();

    const items = await outbox();
    const start = items.find((m) => m.kind === "session_start");
    expect(start).not.toHaveProperty("planned_session_id");
    const set = items.find((m) => m.kind === "set_upsert");
    expect(set?.kind === "set_upsert" && set.set).toMatchObject({
      exercise_id: "back_squat",
      set_index: 0,
    });
    expect(set?.kind === "set_upsert" && set.set).not.toHaveProperty(
      "planned_line_ref",
    );
  });

  it("an exercise added to a planned session comes after the shown one", async () => {
    await storePacket();
    await openScreen();
    await menu("Add an exercise");
    fireEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Add an exercise" }),
      ).getByRole("button", { name: "Add Back Squat" }),
    );
    expect(
      await screen.findByRole("note", { name: "Exercise 2 of 3" }),
    ).toBeVisible();
  });
});
