import "fake-indexeddb/auto";

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { putPacket, queued } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * AC-60 on the gym screen: ⋯ → *Stop — something's wrong*. The text is shown
 * at once and offline; the outbox carries the red flag and then the finish,
 * in that order, so the server raises the flag before the session ends. What
 * `GET /cards` answers afterwards is `backend/tests/unit/test_safety.py`.
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
  // The basement gym: no signal, so the outbox keeps what the screen wrote
  // and the text must still appear.
  syncServer().healthy = false;
});

async function open() {
  await putPacket({
    packet: strengthPacket(today, new Date(), { mode: "standard" }),
    storedAt: Date.now(),
  });
  render(<LoggingScreen plannedSessionId={SESSION_ID} />);
  await screen.findByRole("heading", { name: "Set 1 of 3" });
}

async function stop(symptom: string) {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(
    screen.getByRole("menuitem", { name: "Stop — something's wrong" }),
  );
  const panel = await screen.findByRole("dialog", {
    name: "What is happening?",
  });
  fireEvent.click(within(panel).getByRole("button", { name: symptom }));
}

async function kinds(): Promise<string[]> {
  await writesSettled();
  return (await queued()).map((item) => item.mutation.kind);
}

describe("Stop — something's wrong", () => {
  it("chest pain says to call emergency services and ends the session", async () => {
    await open();

    await stop("Chest pain or pressure");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stop. Call emergency services now.",
    );
    expect(screen.queryByRole("button", { name: /Log set/ })).toBeNull();
    const sent = await kinds();
    expect(sent.slice(-2)).toEqual(["red_flag", "session_finish"]);
    const flag = (await queued())
      .map((item) => item.mutation)
      .find((m) => m.kind === "red_flag");
    expect(flag).toMatchObject({ symptom: "chest_pain" });
  });

  it("numbness says to get medical advice today and ends the session", async () => {
    await open();

    await stop("Numbness or tingling");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stop training and get medical advice today.",
    );
    expect((await kinds()).slice(-2)).toEqual(["red_flag", "session_finish"]);
  });

  it("the text is still there after a reload", async () => {
    await open();
    await stop("Fainting or nearly fainting");
    await screen.findByRole("alert");
    await writesSettled();
    cleanup();

    render(<LoggingScreen plannedSessionId={SESSION_ID} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stop. Call emergency services now.",
    );
  });

  it("going back from the list changes nothing", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Stop — something's wrong" }),
    );
    const panel = await screen.findByRole("dialog", {
      name: "What is happening?",
    });

    fireEvent.click(within(panel).getByRole("button", { name: "Back" }));

    expect(screen.getByRole("heading", { name: "Set 1 of 3" })).toBeVisible();
    expect(await kinds()).not.toContain("red_flag");
  });
});
