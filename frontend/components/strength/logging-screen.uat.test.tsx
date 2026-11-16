import "fake-indexeddb/auto";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { toLocalInput } from "@/components/strength/session-edits/lifecycle";
import {
  foldedSets,
  logNext,
  menu,
  openScreen,
  outbox,
  storePacket,
  sync,
  today,
} from "@/components/strength/session-edits/test-kit";
import { putPacket } from "@/lib/offline/db";
import { resetActiveSessions } from "@/lib/offline/outbox";
import { strengthPacket } from "@/tests/mocks/handlers-strength";
import { resetSyncState, syncServer } from "@/tests/mocks/sync-state";

/**
 * The gym screen's UAT findings (B2, B7, B11, W3, W5-W7, W13, U4, U7-U9,
 * U11, U14), each through the screen the way the athlete reaches it. The
 * packet is the captured one (three sets of a one-arm row at 20 kg, then a
 * plank), with the captured curl line and a barbell squat where a test needs
 * them. Findings that belong to a surface of their own (the Why sheet, the
 * hand-over, the pain dialog) are pinned in that surface's test file.
 */

const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  resetSyncState();
  resetActiveSessions();
  // The basement gym unless a test says otherwise: what the screen wrote
  // stays in the outbox to be read.
  syncServer().healthy = false;
});

afterEach(() => {
  window.history.pushState({}, "", "/");
});

async function editReps(from: string, reps: number) {
  fireEvent.click(screen.getByRole("button", { name: `Edit set ${from}` }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Reps" }), {
    target: { value: String(reps) },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
}

async function byIndex(): Promise<Record<string, unknown>[]> {
  return [...(await foldedSets()).values()].sort(
    (a, b) => Number(a.set_index) - Number(b.set_index),
  );
}

describe("B2: the one rating describes only the sets it can describe", () => {
  it("leaves an earlier set at other reps unrated", async () => {
    await storePacket({ mode: "standard" });
    await openScreen();
    await logNext();
    await editReps("1: 20 kg × 10", 9);
    await logNext();
    // The chip's own accessible name is singular for one rep (W5).
    expect(
      await screen.findByRole("button", { name: "1 rep left — log the set" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "2 reps left — log the set" }),
    );
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });
    await writesSettled();

    const sets = await byIndex();
    expect(sets).toHaveLength(3);
    // 20 × 9 was a different effort: not rated by an answer about 20 × 10.
    expect(sets[0]).toMatchObject({ reps: 9, rir: null, rir_capped: false });
    expect(sets[1]).toMatchObject({ reps: 10, rir: 2 });
    expect(sets[2]).toMatchObject({ reps: 10, rir: 2 });
  });
});

describe("B11: the next set opens at the plan's reps", () => {
  it("after a set edited to 9 of 10, set 2 offers 10 again", async () => {
    await storePacket({ mode: "standard" });
    await openScreen();
    await logNext();
    await editReps("1: 20 kg × 10", 9);

    expect(
      await screen.findByRole("button", { name: "Log set 2: 20 kg × 10" }),
    ).toBeVisible();
  });
});

describe("B7: a moved set does not set the target's numbers", () => {
  const squat = { kg: 100, sets: 4, reps: 8 };

  async function curlMovedToSquat() {
    await storePacket({ barbell: squat, curl: true });
    await openScreen();
    await menu("Next exercise");
    await menu("Next exercise");
    await screen.findByRole("heading", { name: "Dumbbell Curl", level: 1 });
    await logNext();
    await menu("Move sets to…");
    const dialog = await screen.findByRole("dialog", { name: "Move sets to…" });
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "Set 1: 10 kg × 12" }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Move to Back Squat" }),
    );
    await writesSettled();
    await menu("Previous exercise");
    await menu("Previous exercise");
    await screen.findByRole("heading", { name: "Back Squat", level: 1 });
  }

  it("keeps the squat at its own weight, never the curl's 10 kg", async () => {
    await curlMovedToSquat();

    // The moved set counts as set 1 of the squat...
    await screen.findByRole("heading", { name: "Set 2 of 4" });
    expect(
      screen.getByRole("button", { name: "Edit set 1: 10 kg × 12" }),
    ).toBeVisible();
    // ...but the next set opens at the plan's weight, above the 20 kg bar.
    expect(
      screen.getByRole("button", { name: "Log set 2: 100 kg × 8" }),
    ).toBeVisible();
    expect(screen.queryByText("Same as last time")).toBeNull();
  });

  it("words the plates line for the bar alone and for less than the bar", async () => {
    await curlMovedToSquat();
    const type = async (kg: string) => {
      fireEvent.click(
        screen.getByRole("button", { name: /^Weight: type a value/ }),
      );
      const input = await screen.findByRole("textbox", { name: "Weight" });
      fireEvent.change(input, { target: { value: kg } });
      fireEvent.keyDown(input, { key: "Enter" });
    };

    await type("20");
    expect(await screen.findByText("just the bar · 20 kg")).toBeVisible();
    await type("12");
    expect(
      await screen.findByText("lighter than the bar (20 kg)"),
    ).toBeVisible();
    expect(screen.queryByText(/per side: —/)).toBeNull();
  });
});

describe("W3: the chip describes the weight on screen", () => {
  it("names today's last set once the weight differs from the plan's", async () => {
    await putPacket({
      packet: strengthPacket(today, new Date(), {
        barbell: { kg: 100, sets: 4, reps: 8 },
      }),
      storedAt: Date.now(),
    });
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    await screen.findByRole("heading", { name: "Set 1 of 4" });
    // Set 1 at 100 × 6: a miss, so set 2 opens at 97.5 with the step-down
    // reason; once set 2 is logged at 97.5 the step-down is spent, and set 3
    // must not claim "from your plan" or an unchanged weight.
    for (let r = 8; r > 6; r--) {
      fireEvent.click(screen.getByRole("button", { name: "Reps: less" }));
    }
    fireEvent.click(
      screen.getByRole("button", { name: "Log set 1: 100 kg × 6" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Log set 2: 97.5 kg × 8" }),
    );
    await screen.findByRole("heading", { name: "Set 3 of 4" });

    expect(
      screen.getByRole("button", { name: "Log set 3: 97.5 kg × 8" }),
    ).toBeVisible();
    expect(screen.getByText("Same as your last set today")).toBeVisible();
    expect(screen.queryByText("From your plan")).toBeNull();
  });
});

describe("W5: counts of one", () => {
  it("shows 0/0 in an empty unplanned session, not 1/0", async () => {
    await storePacket();
    render(<LoggingScreen plannedSessionId="unplanned" />);

    expect(
      await screen.findByRole("note", { name: "Exercise 0 of 0" }),
    ).toBeVisible();
    expect(screen.getByText("0/0")).toBeVisible();
  });

  it("says 1 set logged, not 1 sets", async () => {
    await storePacket({ mode: "standard" });
    await openScreen();
    await logNext();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Finish without rating" }),
    );

    expect(await screen.findByText(/^1 set logged\./)).toBeVisible();
    expect(screen.queryByText(/1 sets/)).toBeNull();
  });
});

describe("W6: the saved screen says what the outbox really holds", () => {
  it("waits, then reports everything uploaded once the queue drained", async () => {
    await storePacket({ mode: "standard" });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <LoggingScreen plannedSessionId={SESSION_ID} />
      </QueryClientProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Log set 1: 20 kg × 10" }),
    );
    await screen.findByRole("heading", { name: "Set 2 of 3" });
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Finish without rating" }),
    );

    expect(
      await screen.findByText(/Not uploaded yet\. It goes up as soon as/),
    ).toBeVisible();

    syncServer().healthy = true;
    await sync();

    expect(await screen.findByText(/Everything is uploaded\./)).toBeVisible();
    expect(screen.queryByText(/Not uploaded yet/)).toBeNull();
  });
});

describe("W7: a one-arm row is said per side", () => {
  it("shows the reps per side on the set, and in the Log it now recap", async () => {
    await storePacket();
    await openScreen();
    expect(
      screen.getByRole("button", {
        name: "Reps: type a value (10 reps per side)",
      }),
    ).toBeVisible();
  });

  it("the recap says 20 kg × 10 per side × 3", async () => {
    await storePacket();
    window.history.pushState({}, "", "/?recap=1");
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);

    expect(await screen.findByText(/20 kg × 10 per side × 3/)).toBeVisible();
  });
});

describe("U4: warm-ups show their weight and reps", () => {
  it("lists each warm-up, ticking them off with ✓ all", async () => {
    const packet = strengthPacket(today, new Date(), {
      barbell: { kg: 100, sets: 4, reps: 8 },
    });
    // The ramp the server prepared for this lift (from a real packet).
    packet.sessions[0].lines[0].warmups = [
      { kg: 20, reps: 10, plates_per_side: null },
      { kg: 40, reps: 5, plates_per_side: null },
      { kg: 60, reps: 3, plates_per_side: null },
      { kg: 80, reps: 2, plates_per_side: null },
    ];
    await putPacket({ packet, storedAt: Date.now() });
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    await screen.findByRole("heading", { name: "Set 1 of 4" });

    for (const text of ["20 kg × 10", "40 kg × 5", "60 kg × 3", "80 kg × 2"]) {
      expect(screen.getByText(text)).toBeVisible();
    }

    fireEvent.click(screen.getByRole("button", { name: "Log all warm-ups" }));
    expect(await screen.findByText("✓ 80 kg × 2")).toBeVisible();
    expect(screen.getByText("✓ 20 kg × 10")).toBeVisible();
  });
});

describe("U7: pain gets an acknowledgement and one undo at a time", () => {
  async function report(kind: string) {
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Pain" }));
    const panel = await screen.findByRole("dialog", { name: "Report pain" });
    fireEvent.click(within(panel).getByRole("button", { name: kind }));
    fireEvent.click(within(panel).getByRole("button", { name: "4–5" }));
  }

  it("an ache says it was noted, and can be taken back", async () => {
    await storePacket();
    await openScreen();
    await logNext();

    await report("Muscle ache or burn");

    expect(
      await screen.findByText(
        "Noted: muscle ache on Single-Arm Dumbbell Row. The sets stay as planned.",
      ),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Undo the pain note on Single-Arm Dumbbell Row",
      }),
    );
    await writesSettled();
    expect(screen.queryByText(/^Noted:/)).toBeNull();
    expect((await byIndex())[0]).toMatchObject({
      pain_kind: null,
      pain_band: null,
    });
  });

  it("after a sharp stop only one Undo is on screen", async () => {
    await storePacket();
    await openScreen();
    await logNext();

    await report("Sharp, or in a joint");
    await screen.findByRole("heading", { name: "Front Plank", level: 1 });

    expect(screen.getAllByRole("button", { name: /^Undo/ })).toHaveLength(1);
  });
});

describe("U11: Log it now sends when it happened", () => {
  it("records the start and length the athlete entered", async () => {
    await storePacket();
    window.history.pushState({}, "", "/?recap=1");
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    const started = await screen.findByLabelText("When did you start?");
    const twoDaysAgo = Date.now() - 2 * 24 * 60 * 60_000;
    fireEvent.change(started, { target: { value: toLocalInput(twoDaysAgo) } });
    fireEvent.change(screen.getByLabelText("How long, in minutes?"), {
      target: { value: "50" },
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Log every set as planned" }),
    );
    await screen.findByRole("heading", { name: "Session saved" });
    await writesSettled();

    const items = await outbox();
    const start = items.find((m) => m.kind === "session_start");
    const finish = items.find((m) => m.kind === "session_finish");
    const startMs = new Date(toLocalInput(twoDaysAgo)).getTime();
    expect(start && "start_time" in start && Date.parse(start.start_time)).toBe(
      startMs,
    );
    expect(finish && "end_time" in finish && Date.parse(finish.end_time)).toBe(
      startMs + 50 * 60_000,
    );
  });

  it("defaults to a session that just ended, not one lasting seconds", async () => {
    await storePacket();
    window.history.pushState({}, "", "/?recap=1");
    render(<LoggingScreen plannedSessionId={SESSION_ID} />);
    await screen.findByLabelText("When did you start?");
    fireEvent.click(
      screen.getByRole("button", { name: "Log every set as planned" }),
    );
    await screen.findByRole("heading", { name: "Session saved" });
    await writesSettled();

    const items = await outbox();
    const start = items.find((m) => m.kind === "session_start");
    const finish = items.find((m) => m.kind === "session_finish");
    const seconds =
      start &&
      finish &&
      "start_time" in start &&
      "end_time" in finish &&
      (Date.parse(finish.end_time) - Date.parse(start.start_time)) / 1000;
    expect(seconds).toBeGreaterThanOrEqual(20 * 60 - 60);
  });
});

describe("U14: End with nothing logged discards the session", () => {
  async function endEmpty() {
    await storePacket();
    await openScreen();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    expect(
      await screen.findByRole("heading", { name: "Nothing logged yet" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: /Finish/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Discard session" }));
    await screen.findByRole("heading", { name: "Session discarded" });
    await writesSettled();
  }

  it("takes back a session whose start never uploaded: nothing is sent", async () => {
    await endEmpty();

    expect(await outbox()).toEqual([]);
    // And reopening starts fresh instead of resuming a dead session.
    expect((await outbox()).length).toBe(0);
  });

  it("tells the server to drop a session whose start already uploaded", async () => {
    await storePacket();
    await openScreen();
    syncServer().healthy = true;
    await sync();
    expect(syncServer().synced.size).toBe(1);
    syncServer().healthy = false;

    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Discard session" }),
    );
    await screen.findByRole("heading", { name: "Session discarded" });
    await writesSettled();
    expect((await outbox()).map((m) => m.kind)).toEqual(["session_discard"]);

    syncServer().healthy = true;
    await act(async () => {
      await sync();
    });
    expect(syncServer().synced.size).toBe(0);
    expect(await outbox()).toEqual([]);
  });

  it("with a set logged, End still saves the session", async () => {
    await storePacket({ mode: "standard" });
    await openScreen();
    await logNext();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));

    expect(
      await screen.findByRole("button", { name: "Finish without rating" }),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Nothing logged yet" })).toBe(
      null,
    );
  });
});
