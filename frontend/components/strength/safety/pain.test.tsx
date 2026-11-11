import "fake-indexeddb/auto";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { setsLeftToday } from "@/components/strength/safety/safety-model";
import { type Mutation, putPacket, queued } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";

/**
 * AC-59 on the gym screen: ⋯ → Pain on the set just logged. The packet is
 * the captured one (three sets of a single-arm row, then a plank), so "sets
 * 3–4 of lunges" is here "set 3 of the row": what is checked is that sharp
 * pain leaves no set of the exercise to do today, and an ache leaves all of
 * them. The server half (the card, the block on increases) is
 * `backend/tests/unit/test_safety.py`.
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";
const ROW = "Single-Arm Dumbbell Row";

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
});

async function twoSetsLogged() {
  await putPacket({
    packet: strengthPacket(today, new Date(), { mode: "standard" }),
    storedAt: Date.now(),
  });
  render(<LoggingScreen plannedSessionId={SESSION_ID} />);
  for (const n of [1, 2]) {
    await screen.findByRole("heading", { name: `Set ${n} of 3` });
    fireEvent.click(
      screen.getByRole("button", { name: `Log set ${n}: 20 kg × 10` }),
    );
  }
  await screen.findByRole("heading", { name: "Set 3 of 3" });
}

async function reportPain(kind: string, band: string) {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pain" }));
  const panel = await screen.findByRole("dialog", {
    name: "Where is the pain?",
  });
  fireEvent.click(within(panel).getByRole("button", { name: kind }));
  fireEvent.click(within(panel).getByRole("button", { name: band }));
}

type SetUpsert = Extract<Mutation, { kind: "set_upsert" }>;

/** Each set's fields as the server ends up with them: patches folded in order. */
async function mergedSets(): Promise<Record<string, unknown>[]> {
  await writesSettled();
  const sets = new Map<string, Record<string, unknown>>();
  for (const { mutation } of await queued()) {
    if (mutation.kind !== "set_upsert") continue;
    const upsert = mutation as SetUpsert;
    sets.set(upsert.set_client_id, {
      ...(sets.get(upsert.set_client_id) ?? {}),
      ...upsert.set,
    });
  }
  return [...sets.values()].sort(
    (a, b) => Number(a.set_index) - Number(b.set_index),
  );
}

describe("pain on the gym screen", () => {
  it("sharp, 4–5 after set 2 stores the note on set 2 and drops the rest of the exercise today", async () => {
    await twoSetsLogged();

    await reportPain("Sharp, or in a joint", "4–5");

    // The screen moves on: no set 3 of the row is offered.
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });
    expect(
      screen.getByText(`${ROW}: stopped for today because of sharp pain.`),
    ).toBeVisible();
    const sets = await mergedSets();
    expect(sets).toHaveLength(2);
    expect(sets[1]).toMatchObject({ pain_kind: "sharp", pain_band: "4-5" });
    expect(sets[0].pain_kind).toBeUndefined();
    expect(setsLeftToday(3, ["sharp"])).toBe(0);
  });

  it("undoing the pain note gives the sets back", async () => {
    await twoSetsLogged();
    await reportPain("Sharp, or in a joint", "4–5");
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });

    fireEvent.click(
      screen.getByRole("button", { name: `Undo the pain note on ${ROW}` }),
    );

    await screen.findByRole("heading", { name: ROW, level: 1 });
    expect(screen.getByRole("heading", { name: "Set 3 of 3" })).toBeVisible();
    const sets = await mergedSets();
    expect(sets[1]).toMatchObject({ pain_kind: null, pain_band: null });
  });

  it("a muscle ache changes nothing but the stored note", async () => {
    await twoSetsLogged();

    await reportPain("Muscle ache or burn", "4–5");

    await writesSettled();
    expect(screen.getByRole("heading", { name: ROW, level: 1 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Set 3 of 3" })).toBeVisible();
    expect(screen.queryByText(/stopped for today/)).toBeNull();
    const sets = await mergedSets();
    expect(sets[1]).toMatchObject({ pain_kind: "ache", pain_band: "4-5" });
  });

  it("offers no pain note before a set is logged", async () => {
    await putPacket({
      packet: strengthPacket(today, new Date(), { mode: "standard" }),
      storedAt: Date.now(),
    });
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    await screen.findByRole("heading", { name: "Set 1 of 3" });

    fireEvent.click(screen.getByRole("button", { name: "More" }));

    expect(screen.getByRole("menuitem", { name: "Pain" })).toBeDisabled();
  });
});
