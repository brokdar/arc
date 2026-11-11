import { act, fireEvent, render, screen } from "@testing-library/react";

import {
  LoggingScreen,
  writesSettled,
} from "@/components/strength/logging-screen";
import { type Mutation, putPacket, queued } from "@/lib/offline/db";
import { flush } from "@/lib/offline/outbox";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";

/**
 * What the session-edit tests share: storing the packet the way a sync does,
 * opening the logging screen, logging the next set the way the athlete does
 * (✓, or "log without rating" where the effort row stands in for it), and
 * reading the outbox back the way the uploader does.
 */

export const today = athleteToday();
export const MONDAY_ID = "0199c000-0000-7000-8000-00000000a001";

export async function storePacket(
  options: Parameters<typeof strengthPacket>[2] = {},
  generatedAt: Date = new Date(Date.now() - 60_000),
): Promise<void> {
  await putPacket({
    packet: strengthPacket(today, generatedAt, options),
    storedAt: Date.now(),
  });
}

export async function openScreen(id: string = MONDAY_ID): Promise<void> {
  render(<LoggingScreen plannedSessionId={id} />);
  await screen.findByRole("button", { name: "More" });
}

/** Tap whatever logs the next set: the ✓, or "log without rating". */
export async function logNext(): Promise<void> {
  const button =
    screen.queryByRole("button", { name: "Log the set without rating" }) ??
    (await screen.findByRole("button", { name: /^Log set \d+:/ }));
  fireEvent.click(button);
  await act(() => writesSettled());
}

export async function menu(item: string): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

export async function outbox(): Promise<Mutation[]> {
  await writesSettled();
  return (await queued()).map((item) => item.mutation);
}

/** One sync, as the outbox runs it. */
export async function sync(): Promise<void> {
  await act(async () => {
    await writesSettled();
    await flush();
  });
}

/** Each set as the server ends up with it: the outbox's patches folded in order. */
export async function foldedSets(): Promise<
  Map<string, Record<string, unknown>>
> {
  const sets = new Map<string, Record<string, unknown>>();
  for (const m of await outbox()) {
    if (m.kind === "set_upsert") {
      sets.set(m.set_client_id, {
        ...(sets.get(m.set_client_id) ?? {}),
        ...m.set,
      });
    } else if (m.kind === "set_delete") {
      sets.delete(m.set_client_id);
    }
  }
  return sets;
}
