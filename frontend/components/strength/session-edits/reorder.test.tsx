import "fake-indexeddb/auto";

import { fireEvent, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { writesSettled } from "@/components/strength/logging-screen";
import {
  foldedSets,
  logNext,
  menu,
  openScreen,
  outbox,
  storePacket,
} from "@/components/strength/session-edits/test-kit";

/**
 * AC-54, the phone's half: reordering and adding a set write outbox
 * mutations that, folded the way the server folds them, store the sets in the
 * new exercise order. The plan is row (1), plank (2), curl (3); the server's
 * replay of the same shape is `test_sync_api.py`.
 */

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
});

async function moveUp(exercise: string, times: number) {
  const dialog = await screen.findByRole("dialog", {
    name: "Reorder exercises",
  });
  for (let i = 0; i < times; i++) {
    fireEvent.click(
      within(dialog).getByRole("button", { name: `Move ${exercise} up` }),
    );
  }
  fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
}

describe("reorder and add a set (AC-54)", () => {
  it("stores curl sets, then row sets, then plank sets plus the added one", async () => {
    await storePacket({ curl: true });
    await openScreen();
    await logNext(); // row set 1, before the reorder

    await menu("Reorder exercises");
    await moveUp("Dumbbell Curl", 2);
    expect(
      screen.getByRole("note", { name: "Exercise 2 of 3" }),
    ).toBeInTheDocument();

    await logNext(); // row 2
    await logNext(); // row 3
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });
    await logNext(); // plank 1
    await menu("Add a set");
    await screen.findByRole("heading", { name: "Set 2 of 4" });
    for (let n = 0; n < 3; n++) await logNext(); // plank 2-4
    await screen.findByRole("heading", { name: "Dumbbell Curl", level: 1 });
    for (let n = 0; n < 3; n++) await logNext(); // curl 1-3

    const sets = [...(await foldedSets()).values()].sort(
      (a, b) => Number(a.set_index) - Number(b.set_index),
    );
    expect(sets.map((s) => s.set_index)).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
    ]);
    expect(sets.map((s) => s.exercise_id)).toEqual([
      ...Array(3).fill("dumbbell_curl"),
      ...Array(3).fill("single_arm_dumbbell_row"),
      ...Array(4).fill("front_plank"),
    ]);
    // The added set is a fourth plank hold, a clone of the third, answering
    // the plank's planned line.
    expect(sets[9]).toMatchObject({
      duration_s: 45,
      planned_line_ref: "1.0",
    });
  });

  it("an added set opens with the last set's numbers and is logged only by ✓", async () => {
    await storePacket({ curl: true });
    await openScreen();
    for (let n = 0; n < 3; n++) await logNext();
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });
    await menu("Previous exercise");
    await menu("Add a set");

    await screen.findByRole("heading", { name: "Set 4 of 4" });
    // Nothing is stored until the set is lifted and logged; it is now the
    // row's last set, so its ✓ is the effort row.
    expect((await foldedSets()).size).toBe(3);
    await logNext();
    const sets = [...(await foldedSets()).values()];
    expect(sets).toHaveLength(4);
    expect(sets[3]).toMatchObject({
      exercise_id: "single_arm_dumbbell_row",
      load_kg: 20,
      reps: 10,
      planned_line_ref: "0.0",
    });
  });
});

describe("Move sets to… (AC-54 edge)", () => {
  it("moving two sets to another exercise keeps their client ids", async () => {
    await storePacket({ curl: true });
    await openScreen();
    await logNext();
    await logNext();
    const before = [...(await foldedSets()).keys()];
    expect(before).toHaveLength(2);

    await menu("Move sets to…");
    const dialog = await screen.findByRole("dialog", { name: "Move sets to…" });
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "Set 1: 20 kg × 10" }),
    );
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "Set 2: 20 kg × 10" }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Move to Dumbbell Curl" }),
    );
    await writesSettled();

    const items = await outbox();
    expect(items.filter((m) => m.kind === "set_delete")).toEqual([]);
    const folded = await foldedSets();
    expect([...folded.keys()]).toEqual(before);
    for (const id of before) {
      expect(folded.get(id)).toMatchObject({
        exercise_id: "dumbbell_curl",
        planned_line_ref: "2.0",
        load_kg: 20,
        reps: 10,
      });
    }
  });
});
