import "fake-indexeddb/auto";

import { render, screen } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { OfflineReady } from "@/components/shell/offline-ready";
import { AthleteClock } from "@/lib/clock";
import { rememberContact } from "@/lib/last-contact";
import { putPacket } from "@/lib/offline/db";
import { ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";
import { packetFixture } from "@/tests/mocks/handlers-packet";

/**
 * The line on Today that says the phone can open arc without a network.
 *
 * "Ready" means a service worker controls this origin (it installed only once
 * Today and the offline page were in its cache); "synced" is the last time
 * this phone heard from the server, on the athlete's clock.
 */

function stubServiceWorker(value: unknown) {
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value,
  });
}

function renderLine() {
  return render(
    <AthleteClock timezone={ATHLETE_TIMEZONE}>
      <OfflineReady />
    </AthleteClock>,
  );
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
});

afterEach(() => {
  // jsdom has no service worker container; put that absence back.
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("OfflineReady", () => {
  it("says when the stored plan was fetched, on the athlete's clock", async () => {
    // 01:05 UTC is 15:05 in Kiritimati (UTC+14) and 14:05 the day before in
    // the runner's Midway — only the athlete's clock gives 15:05.
    const fetched = new Date("2026-11-10T01:05:00Z");
    await putPacket({
      packet: packetFixture("2026-11-10", fetched),
      storedAt: fetched.getTime(),
    });
    // The server was reached again hours later, without refreshing the plan:
    // the line must not claim the later time (strength UAT B4 — it said
    // "synced 12:18" over a packet from 12:16).
    rememberContact(new Date("2026-11-10T09:30:00Z"));
    stubServiceWorker({ ready: Promise.resolve({ active: {} }) });

    renderLine();

    // The time sits in its own monospace span, so the line is read whole.
    expect(await screen.findByTestId("offline-ready")).toHaveTextContent(
      /^Ready offline · plan from 15:05$/,
    );
  });

  it("says nothing where no worker can take over", () => {
    rememberContact(new Date("2026-11-10T01:05:00Z"));

    renderLine();

    expect(screen.queryByText(/Ready offline/)).not.toBeInTheDocument();
  });

  it("says nothing before the worker is ready", () => {
    rememberContact(new Date("2026-11-10T01:05:00Z"));
    stubServiceWorker({ ready: new Promise(() => {}) });

    renderLine();

    expect(screen.queryByText(/Ready offline/)).not.toBeInTheDocument();
  });

  it("says Ready offline without a time when no plan is stored", async () => {
    stubServiceWorker({ ready: Promise.resolve({ active: {} }) });

    renderLine();

    expect(await screen.findByTestId("offline-ready")).toHaveTextContent(
      /^Ready offline$/,
    );
  });
});
