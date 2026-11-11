import "fake-indexeddb/auto";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { flattenCatalogue } from "@/lib/i18n/catalogue";
import en from "@/lib/i18n/en.json";
import { type Mutation, putPacket, queued } from "@/lib/offline/db";
import { athleteToday } from "@/tests/mocks/fixtures";
import { startingFlowPacket } from "@/tests/mocks/handlers-strength";

/**
 * AC-58: the guided starting weight, through the logging screen, for a back
 * squat never logged (a packet line with `starting_flow: true`). What is
 * asserted is what reaches the outbox — the IndexedDB store `POST /sync`
 * uploads from — and the weight the first working set opens with.
 */

const today = athleteToday();
const SESSION_ID = "0199c000-0000-7000-8000-00000000a001";

beforeEach(async () => {
  await writesSettled();
  globalThis.indexedDB = new IDBFactory();
});

async function open() {
  await putPacket({
    packet: startingFlowPacket(today, new Date()),
    storedAt: Date.now(),
  });
  render(<LoggingScreen plannedSessionId={SESSION_ID} />);
  return screen.findByRole("region", { name: "Find your starting weight" });
}

type SetUpsert = Extract<Mutation, { kind: "set_upsert" }>;

async function setItems(): Promise<SetUpsert[]> {
  await writesSettled();
  return (await queued())
    .map((item) => item.mutation)
    .filter((m): m is SetUpsert => m.kind === "set_upsert");
}

async function answer(flow: HTMLElement, word: "Easy" | "Good" | "Hard") {
  fireEvent.click(
    within(flow).getByRole("button", { name: `${word} — log these 5 reps` }),
  );
  await writesSettled();
}

describe("AC-58: finding a starting weight", () => {
  it("Easy, Good, Hard from the bar steps 20 → 25 → 27.5 and settles 25 kg", async () => {
    const flow = await open();
    expect(
      within(flow).getByText("Do 5 easy reps with this weight."),
    ).toBeVisible();
    expect(within(flow).getByText("20 kg in total")).toBeVisible();

    await answer(flow, "Easy");
    expect(await within(flow).findByText("25 kg in total")).toBeVisible();
    await answer(flow, "Good");
    expect(await within(flow).findByText("27.5 kg in total")).toBeVisible();
    await answer(flow, "Hard");

    // Hard settles: the flow is gone and set 1 opens at the working weight.
    expect(
      await screen.findByRole("heading", { name: "Set 1 of 3" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "Find your starting weight" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Log set 1: 25 kg × 5" }),
    ).toBeVisible();

    const items = await setItems();
    expect(items.map((m) => m.set)).toEqual([
      expect.objectContaining({
        exercise_id: "back_squat",
        planned_line_ref: "0.0",
        set_index: 0,
        load_kg: 20,
        reps: 5,
        set_kind: "warmup",
      }),
      expect.objectContaining({
        set_index: 1,
        load_kg: 25,
        reps: 5,
        set_kind: "warmup",
      }),
      expect.objectContaining({
        set_index: 2,
        load_kg: 27.5,
        reps: 5,
        set_kind: "warmup",
      }),
    ]);
    expect(new Set(items.map((m) => m.set_client_id)).size).toBe(3);

    // The first working set is the settled weight, and not a warm-up.
    fireEvent.click(
      screen.getByRole("button", { name: "Log set 1: 25 kg × 5" }),
    );
    await screen.findByRole("heading", { name: "Set 2 of 3" });
    const working = (await setItems()).at(-1);
    expect(working?.set.load_kg).toBe(25);
    expect(working?.set.set_index).toBe(3);
    expect(working?.set).not.toHaveProperty("set_kind");
  });

  it("typing 60 kg skips the flow and stores no flow sets", async () => {
    const flow = await open();

    fireEvent.change(
      within(flow).getByRole("textbox", { name: "Your working weight" }),
      { target: { value: "60" } },
    );
    fireEvent.click(
      within(flow).getByRole("button", { name: "Use this weight" }),
    );

    expect(
      await screen.findByRole("button", { name: "Log set 1: 60 kg × 5" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "Find your starting weight" }),
    ).toBeNull();
    expect(await setItems()).toEqual([]);
  });

  it("shows only the flow's own wording (catalogue keys starting.*)", async () => {
    const flow = await open();

    const catalogue = flattenCatalogue(en);
    const flowTemplates = Object.entries(catalogue)
      .filter(([key]) => key.startsWith("starting."))
      .map(([, text]) => text);
    expect(flowTemplates.length).toBeGreaterThan(0);
    const patterns = flowTemplates.map(
      (text) =>
        new RegExp(
          `^${text
            .split(/\{[^}]+\}/)
            .map((part) => part.replace(/[.*+?^$()|[\]\\]/g, "\\$&"))
            .join(".+")}$`,
        ),
    );
    const fromFlow = (text: string) => patterns.some((re) => re.test(text));

    // Every piece of text and every accessible name inside the flow is one of
    // the starting.* strings, filled in.
    const shown: string[] = [];
    const walker = document.createTreeWalker(flow, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent?.trim();
      if (text) shown.push(text);
    }
    for (const element of flow.querySelectorAll("[aria-label]")) {
      shown.push(element.getAttribute("aria-label") ?? "");
    }
    shown.push(flow.getAttribute("aria-label") ?? "");
    expect(shown.length).toBeGreaterThan(5);
    expect(shown.filter((text) => !fromFlow(text))).toEqual([]);

    // And the logging screen's own current-set wording is not on screen
    // beside it: no set heading, no reason chip, no ✓, no effort question.
    expect(screen.queryByRole("heading", { name: /^Set \d/ })).toBeNull();
    expect(screen.queryByText(catalogue["train.reasonBlank"])).toBeNull();
    expect(screen.queryByRole("button", { name: /^Log set/ })).toBeNull();
    // (The flow asks "How did that feel?" in its own words, which happen to
    // match the effort row's in English — so look outside the flow.)
    expect(
      screen
        .queryAllByRole("group", { name: catalogue["effort.questionSimple"] })
        .filter((group) => !flow.contains(group)),
    ).toEqual([]);
    expect(
      screen.queryByRole("button", {
        name: catalogue["effort.withoutRatingLabel"],
      }),
    ).toBeNull();
  });
});
