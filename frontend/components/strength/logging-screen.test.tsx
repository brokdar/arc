import "fake-indexeddb/auto";

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { type Mutation, putPacket, queued } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * AC-50: what the logging screen writes to the outbox — the IndexedDB store
 * `POST /sync` uploads from, read back here as the uploader reads it. The
 * packet is stored the way a sync stores it; the plan is the captured one
 * (three sets of a one-arm row at 20 kg, then a plank).
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
});

afterEach(() => {
  vi.useRealTimers();
});

async function open(options: Parameters<typeof strengthPacket>[2]) {
  await putPacket({
    packet: strengthPacket(today, new Date(), options),
    storedAt: Date.now(),
  });
  render(<LoggingScreen plannedSessionId={SESSION_ID} />);
  await screen.findByRole("heading", { name: "Set 1 of 3" });
}

async function logAsPlanned(n: number) {
  fireEvent.click(
    await screen.findByRole("button", { name: `Log set ${n}: 20 kg × 10` }),
  );
  await screen.findByRole("heading", { name: `Set ${n + 1} of 3` });
}

type SetUpsert = Extract<Mutation, { kind: "set_upsert" }>;

async function setItems(): Promise<SetUpsert[]> {
  await writesSettled();
  return (await queued())
    .map((item) => item.mutation)
    .filter((m): m is SetUpsert => m.kind === "set_upsert");
}

/** Each set's fields as the server ends up with them: patches folded in order. */
async function mergedSets(): Promise<Map<string, Record<string, unknown>>> {
  const sets = new Map<string, Record<string, unknown>>();
  for (const item of await setItems()) {
    sets.set(item.set_client_id, {
      ...(sets.get(item.set_client_id) ?? {}),
      ...item.set,
    });
  }
  return sets;
}

describe("Standard: rating the last set rates the exercise", () => {
  it("4+ writes rir 4, capped, for all three sets", async () => {
    await open({ mode: "standard", plannedRir: 2 });
    await logAsPlanned(1);
    await logAsPlanned(2);

    expect(
      screen.getByText("How many more reps could you have done?"),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", {
        name: "4 or more reps left, felt easy — log the set",
      }),
    );
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });

    const items = await setItems();
    const ids = [...new Set(items.map((m) => m.set_client_id))];
    expect(ids).toHaveLength(3);
    for (const id of ids) {
      // Every set has an outbox item carrying the rating itself.
      expect(
        items.some(
          (m) =>
            m.set_client_id === id &&
            m.set.rir === 4 &&
            m.set.rir_capped === true,
        ),
      ).toBe(true);
    }
    for (const fields of (await mergedSets()).values()) {
      expect(fields).toMatchObject({
        rir: 4,
        rir_capped: true,
        reps: 10,
        load_kg: 20,
        per_side: true,
        planned_line_ref: "0.0",
      });
    }
  });

  it("log without rating writes rir null on all three — never the target", async () => {
    await open({ mode: "standard", plannedRir: 2 });
    await logAsPlanned(1);
    await logAsPlanned(2);

    fireEvent.click(
      screen.getByRole("button", { name: "Log the set without rating" }),
    );
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });

    const items = await setItems();
    const ids = new Set(items.map((m) => m.set_client_id));
    expect(ids.size).toBe(3);
    for (const id of ids) {
      expect(
        items.some((m) => m.set_client_id === id && m.set.rir === null),
      ).toBe(true);
    }
    // No item anywhere carries a reps-left number: not the planned 2.
    expect(items.filter((m) => typeof m.set.rir === "number")).toEqual([]);
    for (const fields of (await mergedSets()).values()) {
      expect(fields.rir).toBeNull();
      expect(fields.rir_capped).toBe(false);
    }
  });
});

describe("Simple: Easy / Good / Hard", () => {
  it("shows the three words, no reps-left row, and stores effort_simple", async () => {
    await open({ mode: "simple", plannedRir: 2 });
    await logAsPlanned(1);
    await logAsPlanned(2);

    expect(screen.getByText("How did that feel?")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Easy — log the set" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Hard — log the set" }),
    ).toBeVisible();
    expect(
      screen.queryByText("How many more reps could you have done?"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reps left/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Good — log the set" }));
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });

    const merged = await mergedSets();
    expect(merged.size).toBe(3);
    for (const fields of merged.values()) {
      expect(fields.effort_simple).toBe("good");
      expect(fields).not.toHaveProperty("rir");
      expect(fields).not.toHaveProperty("rir_capped");
    }
  });
});

describe("undo", () => {
  it("within 6 s removes the set's outbox item", async () => {
    await open({ mode: "standard" });
    await logAsPlanned(1);
    const [logged] = await setItems();
    expect(logged).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Undo the last set" }));
    await screen.findByRole("heading", { name: "Set 1 of 3" });

    await vi.waitFor(async () =>
      expect(
        (await queued()).filter(
          (item) =>
            item.mutation.kind === "set_upsert" &&
            item.mutation.set_client_id === logged.set_client_id,
        ),
      ).toEqual([]),
    );
    // Taken back, not deleted after the fact: no tombstone was queued.
    expect(
      (await queued()).some((item) => item.mutation.kind === "set_delete"),
    ).toBe(false);
    // The session start stays.
    expect((await queued()).map((item) => item.mutation.kind)).toEqual([
      "session_start",
    ]);
  });

  it("is gone after 6 s and the set stays", async () => {
    await open({ mode: "standard" });
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setTimeout", "clearTimeout"],
    });
    await logAsPlanned(1);
    expect(
      screen.getByRole("button", { name: "Undo the last set" }),
    ).toBeVisible();

    act(() => {
      vi.advanceTimersByTime(6_000);
    });

    expect(
      screen.queryByRole("button", { name: "Undo the last set" }),
    ).not.toBeInTheDocument();
    expect(await setItems()).toHaveLength(1);
  });
});

describe("the offline shell", () => {
  it("opens the session named in the address bar, not the shell's own id", async () => {
    // Offline, the service worker answers every `/train/<id>` with the one
    // cached `/train/shell` page (`public/sw.js`).
    await putPacket({
      packet: strengthPacket(today, new Date()),
      storedAt: Date.now(),
    });
    window.history.pushState({}, "", `/train/${SESSION_ID}`);
    try {
      render(<LoggingScreen plannedSessionId="shell" />);
      expect(
        await screen.findByRole("heading", {
          name: "Single-Arm Dumbbell Row",
          level: 1,
        }),
      ).toBeVisible();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});

describe("numbers and fixes", () => {
  it("steps the weight through the gym's dumbbells and logs what is shown", async () => {
    await open({ mode: "standard" });
    fireEvent.click(screen.getByRole("button", { name: "Weight: more" }));

    fireEvent.click(
      await screen.findByRole("button", { name: "Log set 1: 22 kg × 10" }),
    );
    await screen.findByRole("heading", { name: "Set 2 of 3" });

    const [logged] = await setItems();
    expect(logged.set.load_kg).toBe(22);
  });

  it("edits a logged row by patching that set", async () => {
    await open({ mode: "standard" });
    await logAsPlanned(1);
    const [logged] = await setItems();

    fireEvent.click(
      screen.getByRole("button", { name: "Edit set 1: 20 kg × 10" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Reps" }), {
      target: { value: "8" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(
      await screen.findByRole("button", { name: "Edit set 1: 20 kg × 8" }),
    ).toBeInTheDocument();
    const items = await setItems();
    expect(items.at(-1)).toMatchObject({
      set_client_id: logged.set_client_id,
      set: { reps: 8, load_kg: 20 },
    });
  });

  it("finishes with the rating, don't learn and its private reason", async () => {
    await open({ mode: "standard" });
    // Finishing flushes the outbox; with signal the upload can empty it
    // before the read below, so these read it as a basement with none.
    syncServer().healthy = false;
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Don't learn from today" }),
    );
    fireEvent.change(
      screen.getByRole("combobox", { name: "Reason, only for you" }),
      { target: { value: "bad sleep" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "7 of 10 — finish" }));

    expect(
      await screen.findByRole("heading", { name: "Session saved" }),
    ).toBeVisible();
    await writesSettled();
    const kinds = (await queued()).map((item) => item.mutation);
    expect(kinds.map((m) => m.kind)).toEqual([
      "session_start",
      "session_rating",
      "dont_learn",
      "session_finish",
    ]);
    expect(kinds[1]).toMatchObject({ session_rating: 7 });
    expect(kinds[2]).toMatchObject({ dont_learn: true, reason: "bad sleep" });
  });

  it("finishing without a rating leaves it empty", async () => {
    await open({ mode: "standard" });
    // Finishing flushes the outbox; with signal the upload can empty it
    // before the read below, so these read it as a basement with none.
    syncServer().healthy = false;
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Finish without rating" }),
    );
    await screen.findByRole("heading", { name: "Session saved" });
    await writesSettled();
    expect((await queued()).map((item) => item.mutation.kind)).toEqual([
      "session_start",
      "session_finish",
    ]);
  });
});

describe("AC-74: a missed set steps the rest down, on the screen", () => {
  const squat = { kg: 100, sets: 4, reps: 8 };

  async function openSquat(followExactly = false) {
    await putPacket({
      packet: strengthPacket(today, new Date(), {
        barbell: squat,
        followExactly,
      }),
      storedAt: Date.now(),
    });
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    await screen.findByRole("heading", { name: "Set 1 of 4" });
  }

  async function logSet(n: number, summary: string, reps = 8) {
    for (let r = 8; r > reps; r--) {
      fireEvent.click(screen.getByRole("button", { name: "Reps: less" }));
    }
    fireEvent.click(
      await screen.findByRole("button", { name: `Log set ${n}: ${summary}` }),
    );
    await screen.findByRole("heading", { name: `Set ${n + 1} of 4` });
  }

  it("set 2 at 6 reps: sets 3 and 4 show 97.5 kg, with the reason", async () => {
    await openSquat();
    await logSet(1, "100 kg × 8");
    await logSet(2, "100 kg × 6", 6);

    expect(
      screen.getByRole("button", { name: "Log set 3: 97.5 kg × 8" }),
    ).toBeVisible();
    expect(screen.getByText("next: set 4 · 97.5 kg × 8")).toBeVisible();
    expect(
      screen.getByText("Next sets 2.5 kg lighter: that set was a lot harder"),
    ).toBeVisible();
    // The logged rows keep what was lifted.
    expect(
      screen.getByRole("button", { name: "Edit set 1: 100 kg × 8" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Edit set 2: 100 kg × 6" }),
    ).toBeVisible();

    const [first, second] = await setItems();
    expect(first.set.load_kg).toBe(100);
    expect(second.set).toMatchObject({ load_kg: 100, reps: 6 });
  });

  it("set 2 at 7 reps changes nothing", async () => {
    await openSquat();
    await logSet(1, "100 kg × 8");
    await logSet(2, "100 kg × 7", 7);

    expect(
      screen.getByRole("button", { name: "Log set 3: 100 kg × 7" }),
    ).toBeVisible();
    expect(screen.queryByText(/lighter/)).toBeNull();
    expect(screen.getByText("From your plan")).toBeVisible();
  });

  it("on a follow-exactly plan nothing changes", async () => {
    await openSquat(true);
    await logSet(1, "100 kg × 8");
    await logSet(2, "100 kg × 6", 6);

    expect(
      screen.getByRole("button", { name: "Log set 3: 100 kg × 6" }),
    ).toBeVisible();
    expect(screen.queryByText(/lighter/)).toBeNull();
  });

  it("a second miss lowers one more step", async () => {
    await openSquat();
    await logSet(1, "100 kg × 6", 6);
    await logSet(2, "97.5 kg × 6", 6);

    expect(
      screen.getByRole("button", { name: "Log set 3: 95 kg × 8" }),
    ).toBeVisible();
    expect(
      screen.getByText("Next sets 2.5 kg lighter: that set was a lot harder"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Edit set 1: 100 kg × 6" }),
    ).toBeVisible();
  });
});
