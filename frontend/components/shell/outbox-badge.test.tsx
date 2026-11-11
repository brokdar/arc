import "fake-indexeddb/auto";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { OutboxBadge } from "@/components/shell/outbox-badge";
import { addQueued } from "@/lib/offline/db";
import { uuid7 } from "@/lib/offline/uuid7";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * The badge over the signed-in shell, against the typed `/sync` handler.
 * Mounting it starts the outbox, so each test's launch flush is real.
 */

const SESSION = uuid7();

function setFor(session: string) {
  return {
    kind: "set_upsert" as const,
    mutation_id: uuid7(),
    client_edit_time: new Date().toISOString(),
    session_client_id: session,
    set_client_id: uuid7(),
    set: { exercise_id: "back_squat", reps: 5 },
  };
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
});

describe("OutboxBadge", () => {
  it("says Update arc to upload on version skew and keeps the queue", async () => {
    syncServer().version = 0;
    await addQueued(setFor(SESSION), Date.now());

    render(<OutboxBadge />);

    expect(await screen.findByTestId("outbox-badge")).toHaveTextContent(
      "Update arc to upload",
    );
    expect(syncServer().applied).toEqual([]);
  });

  it("lists what could not upload, with copy and export", async () => {
    const orphan = setFor(uuid7());
    await addQueued(orphan, Date.now());

    render(<OutboxBadge />);
    const badge = await screen.findByTestId("outbox-badge");
    expect(badge).toHaveTextContent("Couldn't upload 1");

    await userEvent.click(badge);

    const panel = screen.getByRole("region", { name: "Couldn't upload" });
    expect(panel).toHaveTextContent("set_upsert");
    expect(panel).toHaveTextContent(/no session/);
    const exported = screen.getByRole("link", { name: "Export" });
    expect(decodeURIComponent(exported.getAttribute("href") ?? "")).toContain(
      orphan.mutation_id,
    );
    expect(screen.getByRole("button", { name: "Copy" })).toBeVisible();
  });

  it("shows nothing for a queue younger than a minute", async () => {
    syncServer().healthy = false;
    await addQueued(setFor(SESSION), Date.now());

    const { container } = render(<OutboxBadge />);

    // Give the launch flush and the first read time to settle.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(container).toBeEmptyDOMElement();
  });
});
