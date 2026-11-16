import "fake-indexeddb/auto";

import { fireEvent, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { writesSettled } from "@/components/strength/logging-screen";
import { deviceId } from "@/components/strength/session-edits/device";
import {
  foldedSets,
  logNext,
  MONDAY_ID,
  menu,
  openScreen,
  outbox,
  storePacket,
  sync,
} from "@/components/strength/session-edits/test-kit";
import type { components } from "@/generated/api/schema";
import type { Mutation as OutboxMutation } from "@/lib/offline/db";
import { resetActiveSessions } from "@/lib/offline/outbox";
import {
  applyBatch,
  resetSyncState,
  syncServer,
} from "@/tests/mocks/sync-state";

/**
 * AC-56: one session, two phones. Each test drives one phone through the
 * screen; the other phone is its uploads, applied to the fake server as
 * `POST /sync` would receive them — the same mutations its own screen
 * writes, so the server state the screen meets is one the real server
 * could hold.
 */

type Mutation = components["schemas"]["Mutation"];
/** A mutation before its id is minted, kind by kind; set edits as patches. */
type Unsent = OutboxMutation extends infer M
  ? M extends unknown
    ? Omit<M, "mutation_id">
    : never
  : never;

const OTHER_DEVICE = "0199c000-0000-7000-8000-0000000dd0a1";
const SESSION = "0199c000-0000-7000-8000-0000000ee0a1";
const START = new Date(Date.now() - 20 * 60_000).toISOString();
let n = 0;

function from(mutations: Unsent[]): void {
  const answer = applyBatch({
    schema_version: 1,
    mutations: mutations.map((m) => ({
      ...m,
      mutation_id: `0199c000-0000-7000-8000-${String(++n).padStart(12, "0")}`,
    })) as unknown as Mutation[],
  });
  expect(answer.status).toBe(200);
}

/** The other phone started this session and logged its first row set. */
function otherPhoneStarted(): void {
  from([
    {
      kind: "session_start",
      client_edit_time: START,
      session_client_id: SESSION,
      start_time: START,
      device_id: OTHER_DEVICE,
      planned_session_id: MONDAY_ID,
    },
    {
      kind: "set_upsert",
      client_edit_time: START,
      session_client_id: SESSION,
      set_client_id: "0199c000-0000-7000-8000-0000000ff001",
      set: {
        exercise_id: "single_arm_dumbbell_row",
        planned_line_ref: "0.0",
        set_index: 0,
        reps: 10,
        load_kg: 20,
        per_side: true,
        completed_at: START,
      },
    },
  ]);
}

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  resetSyncState();
  resetActiveSessions();
});

describe("a second phone opening an active session (AC-56)", () => {
  it("gets Continue here?, and accepting takes the session over", async () => {
    otherPhoneStarted();
    await storePacket();
    await openScreenExpectingPrompt();

    fireEvent.click(screen.getByRole("button", { name: "Continue here" }));

    // The other phone's set is here, and the next set is the second.
    expect(
      await screen.findByRole("button", { name: "Edit set 1: 20 kg × 10" }),
    ).toBeVisible();
    await screen.findByRole("heading", { name: "Set 2 of 3" });

    // The claim reached the server: this phone holds the session now.
    await sync();
    expect(await outbox()).toEqual([]);
    const held = syncServer().synced.get(SESSION);
    expect(held?.deviceId).toBe(await deviceId());
    expect(held?.deviceId).not.toBe(OTHER_DEVICE);
  });

  // UAT B3: the taking-over phone rebuilt the plan, not the session as the
  // other phone had edited it — Back Squat instead of the swapped Box Squat,
  // and its next set uploaded as the wrong exercise.
  it("takes over the session as the other phone shaped it: swap and order", async () => {
    await storePacket({ curl: true });
    from([
      {
        kind: "session_start",
        client_edit_time: START,
        session_client_id: SESSION,
        start_time: START,
        device_id: OTHER_DEVICE,
        planned_session_id: MONDAY_ID,
      },
      // The curl moved to the front, then the row swapped for a barbell row.
      {
        kind: "set_upsert",
        client_edit_time: START,
        session_client_id: SESSION,
        set_client_id: "0199c000-0000-7000-8000-0000000ff011",
        set: {
          exercise_id: "dumbbell_curl",
          planned_line_ref: "2.0",
          set_index: 0,
          reps: 12,
          load_kg: 10,
          completed_at: START,
        },
      },
      {
        kind: "set_upsert",
        client_edit_time: START,
        session_client_id: SESSION,
        set_client_id: "0199c000-0000-7000-8000-0000000ff012",
        set: {
          exercise_id: "barbell_row",
          replaced_exercise_id: "single_arm_dumbbell_row",
          planned_line_ref: "0.0",
          set_index: 1,
          reps: 10,
          load_kg: 40,
          completed_at: START,
        },
      },
    ]);
    await openScreenExpectingPrompt();
    fireEvent.click(screen.getByRole("button", { name: "Continue here" }));

    // On the open exercise: the curl is not done (1 of 3), and it is first.
    expect(
      await screen.findByRole("note", { name: "Exercise 1 of 3" }),
    ).toBeVisible();
    await screen.findByRole("heading", { name: "Dumbbell Curl", level: 1 });
    await logNext();
    await menu("Next exercise");
    // The swap survived: the row's line is the barbell row, set 2 next.
    expect(
      await screen.findByRole("heading", { name: "Barbell Row", level: 1 }),
    ).toBeVisible();
    await screen.findByRole("heading", { name: "Set 2 of 3" });
    await logNext();
    const mine = [...(await foldedSets()).values()].filter(
      (s) => s.reps !== undefined && s.exercise_id === "barbell_row",
    );
    expect(mine.length).toBeGreaterThan(0);
    expect(mine.every((s) => s.replaced_exercise_id !== undefined)).toBe(true);
  });

  it("offline overlap keeps both phones' sets", async () => {
    otherPhoneStarted();
    await storePacket();
    await openScreenExpectingPrompt();
    fireEvent.click(screen.getByRole("button", { name: "Continue here" }));
    await screen.findByRole("heading", { name: "Set 2 of 3" });
    await sync();

    // Both phones lose signal; the other one, not knowing it handed over,
    // logs a set, and so does this one. Both upload later.
    syncServer().healthy = false;
    from([
      {
        kind: "set_upsert",
        client_edit_time: new Date().toISOString(),
        session_client_id: SESSION,
        set_client_id: "0199c000-0000-7000-8000-0000000ff002",
        set: {
          exercise_id: "single_arm_dumbbell_row",
          planned_line_ref: "0.0",
          set_index: 1,
          reps: 9,
          load_kg: 20,
          per_side: true,
        },
      },
    ]);
    await logNext();
    syncServer().healthy = true;
    await sync();

    const sets = syncServer().synced.get(SESSION)?.sets;
    expect(sets?.size).toBe(3);
    expect([...(sets?.values() ?? [])].map((s) => s.reps)).toEqual([10, 9, 10]);
  });

  it("with no signal there is nobody to ask: a fresh session starts", async () => {
    otherPhoneStarted();
    syncServer().healthy = false;
    await storePacket();
    await openScreen();

    expect(screen.queryByRole("dialog", { name: "Continue here?" })).toBeNull();
    await screen.findByRole("heading", { name: "Set 1 of 3" });
  });
});

describe("the first phone, once the session moved (AC-56)", () => {
  it("is read-only within one sync", async () => {
    await storePacket();
    await openScreen();
    await logNext();
    await sync();
    const mine = syncServer().synced.values().next().value;
    expect(mine?.deviceId).toBeTruthy();

    // The second phone accepted "Continue here?".
    from([
      {
        kind: "session_claim",
        client_edit_time: new Date().toISOString(),
        session_client_id: mine?.clientId ?? "",
        device_id: OTHER_DEVICE,
      },
    ]);
    expect(screen.getByRole("button", { name: /^Log set 2:/ })).toBeVisible();

    await sync();

    expect(
      await screen.findByText(
        "You went on with this session on another phone.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Log set/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
    // Read-only means nothing on this phone writes any more: not End (whose
    // session_finish would end the session the other phone is training in),
    // not Undo, not the warm-ups, not an edit of a logged row.
    expect(screen.queryByRole("button", { name: "End session" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Undo/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Log all warm-ups" }),
    ).toBeNull();
    const before = (await outbox()).length;
    const row = screen.getByRole("button", { name: /^Edit set 1:/ });
    expect(row).toBeDisabled();
    fireEvent.click(row);
    expect(screen.queryByRole("dialog")).toBeNull();
    await writesSettled();
    expect(await outbox()).toHaveLength(before);
  });
});

describe("the first phone after a reload (UAT B3)", () => {
  it("opens on the read-only screen, not on Resume", async () => {
    await storePacket();
    const first = await openScreenKeep();
    await logNext();
    await sync();
    const mine = syncServer().synced.values().next().value;
    from([
      {
        kind: "session_claim",
        client_edit_time: new Date().toISOString(),
        session_client_id: mine?.clientId ?? "",
        device_id: OTHER_DEVICE,
      },
    ]);
    first.unmount();
    await writesSettled();

    const { render } = await import("@testing-library/react");
    const { LoggingScreen } = await import(
      "@/components/strength/logging-screen"
    );
    render(<LoggingScreen plannedSessionId={MONDAY_ID} />);

    expect(
      await screen.findByText(
        "You went on with this session on another phone.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/^Resume/)).toBeNull();
  });
});

async function openScreenKeep() {
  const { render } = await import("@testing-library/react");
  const { LoggingScreen } = await import(
    "@/components/strength/logging-screen"
  );
  const view = render(<LoggingScreen plannedSessionId={MONDAY_ID} />);
  await screen.findByRole("button", { name: "More" });
  return view;
}

async function openScreenExpectingPrompt() {
  const { render } = await import("@testing-library/react");
  const { LoggingScreen } = await import(
    "@/components/strength/logging-screen"
  );
  render(<LoggingScreen plannedSessionId={MONDAY_ID} />);
  const dialog = await screen.findByRole("dialog", { name: "Continue here?" });
  expect(
    within(dialog).getByText("This session is open on another phone."),
  ).toBeVisible();
}
