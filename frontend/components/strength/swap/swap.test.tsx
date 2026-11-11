import "fake-indexeddb/auto";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { type Mutation, putPacket, queued } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * AC-76 / AC-77 on the gym screen: ⋯ → Swap → pick, from the packet stored
 * on the phone. The plan is the captured one (three sets of a one-arm row at
 * 20 kg, then a plank); the row's three swaps are the server's ranking
 * (`handlers-packet.ts`). Every test runs with the network down: a swap needs
 * nothing but the packet.
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

type SetUpsert = Extract<Mutation, { kind: "set_upsert" }>;
type Swap = Extract<Mutation, { kind: "swap" }>;

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  syncServer().healthy = false;
});

async function open(options: Parameters<typeof strengthPacket>[2] = {}) {
  await putPacket({
    packet: strengthPacket(today, new Date(), options),
    storedAt: Date.now(),
  });
  render(<LoggingScreen plannedSessionId={SESSION_ID} />);
  await screen.findByRole("heading", { name: "Set 1 of 3" });
}

async function mutations(): Promise<Mutation[]> {
  await writesSettled();
  return (await queued()).map((item) => item.mutation);
}

async function sets(): Promise<SetUpsert[]> {
  return (await mutations()).filter(
    (m): m is SetUpsert => m.kind === "set_upsert",
  );
}

/** The three taps: ⋯, Swap, and the pick. Returns the tap count. */
function swapTo(name: string, scope?: "Rest of block"): number {
  let taps = 0;
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  taps++;
  fireEvent.click(screen.getByRole("menuitem", { name: "Swap" }));
  taps++;
  const panel = screen.getByRole("dialog", {
    name: "Swap Single-Arm Dumbbell Row",
  });
  if (scope) fireEvent.click(within(panel).getByRole("radio", { name: scope }));
  fireEvent.click(within(panel).getByRole("button", { name }));
  taps++;
  return taps;
}

describe("swapping in the gym", () => {
  it("takes three taps, offline, from the packet's three swaps", async () => {
    await open();

    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Swap" }));
    const panel = screen.getByRole("dialog", {
      name: "Swap Single-Arm Dumbbell Row",
    });
    // Exactly the packet's three, in its order; no network was asked.
    expect(
      within(panel)
        .getAllByRole("button", { name: /^(Barbell|Pendlay|Inverted) Row$/ })
        .map((b) => b.textContent),
    ).toEqual(["Barbell Row", "Pendlay Row", "Inverted Row"]);
    // "This session" is the default: no extra tap to pick a scope.
    expect(
      within(panel).getByRole("radio", { name: "This session" }),
    ).toBeChecked();
    fireEvent.click(within(panel).getByRole("button", { name: "Barbell Row" }));

    expect(
      await screen.findByRole("heading", { name: "Barbell Row", level: 1 }),
    ).toBeVisible();
    expect(syncServer().requests).toBe(0);
  });

  it("logs the substitute's id with the planned exercise as replaced_exercise_id", async () => {
    await open({ barbellRowHistory: { loadKg: 40, reps: 8 } });

    expect(swapTo("Barbell Row")).toBe(3);
    fireEvent.click(
      await screen.findByRole("button", { name: "Log set 1: 40 kg × 8" }),
    );
    await screen.findByRole("heading", { name: "Set 2 of 3" });

    const [logged] = await sets();
    expect(logged.set).toMatchObject({
      exercise_id: "barbell_row",
      replaced_exercise_id: "single_arm_dumbbell_row",
      planned_line_ref: "0.0",
      load_kg: 40,
      reps: 8,
    });
    // A barbell row is two-handed: the one-arm line's per-side flag stays behind.
    expect(logged.set).not.toHaveProperty("per_side");
    const swaps = (await mutations()).filter(
      (m): m is Swap => m.kind === "swap",
    );
    expect(swaps).toHaveLength(1);
    expect(swaps[0]).toMatchObject({
      exercise_id: "single_arm_dumbbell_row",
      swap_exercise_id: "barbell_row",
      scope: "session",
    });
  });

  it("opens the substitute on its own history, never the planned 20 kg", async () => {
    await open({ barbellRowHistory: { loadKg: 40, reps: 8 } });

    swapTo("Barbell Row");

    expect(
      await screen.findByRole("button", { name: "Log set 1: 40 kg × 8" }),
    ).toBeVisible();
    expect(screen.getByText("Same as last time")).toBeVisible();
  });

  it("opens a never-logged substitute on the starting flow, never the planned 20 kg", async () => {
    await open();

    swapTo("Pendlay Row");

    await screen.findByRole("heading", { name: "Pendlay Row", level: 1 });
    expect(screen.getByText("No history yet")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /Log set 1: 20 kg/ }),
    ).not.toBeInTheDocument();
  });

  it("completes the planned line: three substitute sets move on to the plank", async () => {
    await open({ barbellRowHistory: { loadKg: 40, reps: 8 } });
    swapTo("Barbell Row");

    for (const n of [1, 2]) {
      fireEvent.click(
        await screen.findByRole("button", { name: `Log set ${n}: 40 kg × 8` }),
      );
      await screen.findByRole("heading", { name: `Set ${n + 1} of 3` });
    }
    fireEvent.click(screen.getByRole("button", { name: "Good — log the set" }));

    expect(
      await screen.findByRole("heading", { name: "Front Plank", level: 1 }),
    ).toBeVisible();
    const logged = await sets();
    const created = logged.filter((m) => m.set.exercise_id !== undefined);
    expect(created).toHaveLength(3);
    for (const m of created) {
      expect(m.set).toMatchObject({
        exercise_id: "barbell_row",
        replaced_exercise_id: "single_arm_dumbbell_row",
        planned_line_ref: "0.0",
      });
    }
  });

  it("stores 'rest of block' as a swap the server turns into a pending card", async () => {
    await open();

    swapTo("Inverted Row", "Rest of block");

    await screen.findByRole("heading", { name: "Inverted Row", level: 1 });
    const [swap] = (await mutations()).filter(
      (m): m is Swap => m.kind === "swap",
    );
    expect(swap).toMatchObject({
      swap_exercise_id: "inverted_row",
      scope: "rest_of_block",
    });
  });
});

describe("an avoided exercise", () => {
  it("opens on the swap prompt at session start", async () => {
    await open({ avoided: true });

    const prompt = screen.getByRole("region", {
      name: "You avoid Single-Arm Dumbbell Row",
    });
    expect(
      within(prompt)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Barbell Row", "Pendlay Row", "Inverted Row"]);

    fireEvent.click(
      within(prompt).getByRole("button", { name: "Barbell Row" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Barbell Row", level: 1 }),
    ).toBeVisible();
    expect(
      screen.queryByRole("region", { name: /You avoid/ }),
    ).not.toBeInTheDocument();
  });

  it("shows no prompt for an exercise the athlete does not avoid", async () => {
    await open();

    expect(
      screen.queryByRole("region", { name: /You avoid/ }),
    ).not.toBeInTheDocument();
  });
});
