import "fake-indexeddb/auto";

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { putPacket } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";

/**
 * AC-53: the logging screen by role and name alone — what a screen reader
 * reads. Every query here is a role query; nothing reaches for a test id or
 * a class. The rest end is heard through a live region (`role="alert"`).
 *
 * Time is a test-owned clock handed to the screen, so the 5 s rest of this
 * plan can end without waiting for it.
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

let offset = 0;
const clock = {
  wall: () => Date.now() + offset,
  mono: () => performance.now() + offset,
};

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  offset = 0;
  await putPacket({
    packet: strengthPacket(today, new Date(), { mode: "standard", restS: 5 }),
    storedAt: Date.now(),
  });
});

function render_() {
  return render(<LoggingScreen plannedSessionId={SESSION_ID} clock={clock} />);
}

const NAMED_ROLES = [
  "button",
  "link",
  "checkbox",
  "combobox",
  "textbox",
  "spinbutton",
  "menuitem",
] as const;

function expectEveryControlNamed() {
  for (const role of NAMED_ROLES) {
    for (const control of screen.queryAllByRole(role)) {
      expect(control).toHaveAccessibleName();
    }
  }
}

describe("a screen-reader flow", () => {
  it("logs a set, rates the exercise, and hears the rest end", async () => {
    render_();
    expect(
      await screen.findByRole("heading", {
        name: "Single-Arm Dumbbell Row",
        level: 1,
      }),
    ).toBeVisible();
    expectEveryControlNamed();

    fireEvent.click(
      screen.getByRole("button", { name: "Log set 1: 20 kg × 10" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Set 2 of 3" }),
    ).toBeVisible();
    expect(screen.getByRole("timer", { name: "Rest" })).toBeVisible();
    expectEveryControlNamed();

    // The rest runs out while the page is open: announced, not only drawn.
    offset += 6_000;
    await waitFor(
      () =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Rest is over. Time for the next set.",
        ),
      { timeout: 3_000 },
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Log set 2: 20 kg × 10" }),
    );
    await screen.findByRole("heading", { name: "Set 3 of 3" });

    const effort = screen.getByRole("group", {
      name: "How many more reps could you have done?",
    });
    expectEveryControlNamed();
    fireEvent.click(
      within(effort).getByRole("button", { name: "2 reps left — log the set" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Front Plank", level: 1 }),
    ).toBeVisible();
  });

  it("names every control of the finish panel", async () => {
    render_();
    fireEvent.click(await screen.findByRole("button", { name: "End session" }));
    expect(
      await screen.findByRole("group", {
        name: "How hard was the whole session?",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "Don't learn from today" }),
    ).toBeInTheDocument();
    expectEveryControlNamed();
  });
});

describe("effort chips", () => {
  it("carry text labels, not colour only", async () => {
    render_();
    for (const n of [1, 2]) {
      fireEvent.click(
        await screen.findByRole("button", { name: `Log set ${n}: 20 kg × 10` }),
      );
      await screen.findByRole("heading", { name: `Set ${n + 1} of 3` });
    }
    const effort = screen.getByRole("group", {
      name: "How many more reps could you have done?",
    });
    const chips = within(effort).getAllByRole("button");
    // The visible text, which is what a sighted athlete with no colour
    // vision reads: each chip says its value in words or digits.
    expect(chips.map((chip) => chip.textContent?.trim())).toEqual([
      "0none",
      "1",
      "2",
      "3",
      "4+felt easy",
      "✓ log without rating",
    ]);
  });
});
