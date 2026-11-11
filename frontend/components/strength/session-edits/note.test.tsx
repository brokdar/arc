import "fake-indexeddb/auto";

import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { writesSettled } from "@/components/strength/logging-screen";
import {
  MONDAY_ID,
  menu,
  openScreen,
  outbox,
  storePacket,
} from "@/components/strength/session-edits/test-kit";
import { THURSDAY_ID } from "@/tests/mocks/handlers-strength";
import { resetSyncState, syncServer } from "@/tests/mocks/sync-state";

/**
 * AC-57: a note written on an exercise on Monday shows on that exercise in
 * Thursday's session with no signal. The packet stored on Monday predates
 * the note; nothing reaches the server in between (the network is down for
 * Thursday), so what Thursday shows is what the phone kept.
 */

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  resetSyncState();
});

async function writeNote(text: string) {
  await menu("Note");
  const dialog = await screen.findByRole("dialog", {
    name: "Note on Single-Arm Dumbbell Row",
  });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Note" }), {
    target: { value: text },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  await writesSettled();
}

async function openThursdayOffline() {
  cleanup();
  syncServer().healthy = false;
  await openScreen(THURSDAY_ID);
  await screen.findByRole("heading", {
    name: "Single-Arm Dumbbell Row",
    level: 1,
  });
}

describe("exercise notes (AC-57)", () => {
  it("a note saved on Monday shows on that exercise on Thursday, offline", async () => {
    syncServer().healthy = false;
    await storePacket({ thursday: true });
    await openScreen(MONDAY_ID);
    await writeNote("Straps from 22 kg");

    expect(screen.getByRole("region", { name: "Your note" })).toHaveTextContent(
      "Straps from 22 kg",
    );
    expect(await outbox()).toContainEqual(
      expect.objectContaining({
        kind: "note_upsert",
        exercise_id: "single_arm_dumbbell_row",
        text: "Straps from 22 kg",
      }),
    );

    await openThursdayOffline();
    expect(screen.getByRole("region", { name: "Your note" })).toHaveTextContent(
      "Straps from 22 kg",
    );
  });

  it("a note the server holds shows offline from the stored packet", async () => {
    syncServer().healthy = false;
    await storePacket({
      thursday: true,
      notes: [
        {
          exercise_id: "single_arm_dumbbell_row",
          text: "Bench at notch 3",
          updated_at: "2026-11-02T18:00:00Z",
        },
      ],
    });
    await openThursdayOffline();
    expect(screen.getByRole("region", { name: "Your note" })).toHaveTextContent(
      "Bench at notch 3",
    );
  });

  it("an empty note removes it", async () => {
    syncServer().healthy = false;
    await storePacket({
      thursday: true,
      notes: [
        {
          exercise_id: "single_arm_dumbbell_row",
          text: "Bench at notch 3",
          updated_at: "2026-11-02T18:00:00Z",
        },
      ],
    });
    await openScreen(MONDAY_ID);
    expect(screen.getByRole("region", { name: "Your note" })).toBeVisible();

    await writeNote("");

    expect(screen.queryByRole("region", { name: "Your note" })).toBeNull();
    expect(await outbox()).toContainEqual(
      expect.objectContaining({
        kind: "note_upsert",
        exercise_id: "single_arm_dumbbell_row",
        text: "",
      }),
    );
    await openThursdayOffline();
    expect(screen.queryByRole("region", { name: "Your note" })).toBeNull();
  });
});
