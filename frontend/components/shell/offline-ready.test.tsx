import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { OfflineReady } from "@/components/shell/offline-ready";
import { AthleteClock } from "@/lib/clock";
import { rememberContact } from "@/lib/last-contact";
import { ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";

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

afterEach(() => {
  // jsdom has no service worker container; put that absence back.
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("OfflineReady", () => {
  it("says Ready offline with the last sync time on the athlete's clock", async () => {
    // 01:05 UTC is 15:05 in Kiritimati (UTC+14) and 14:05 the day before in
    // the runner's Midway — only the athlete's clock gives 15:05.
    rememberContact(new Date("2026-11-10T01:05:00Z"));
    stubServiceWorker({ ready: Promise.resolve({ active: {} }) });

    renderLine();

    // The time sits in its own monospace span, so the line is read whole.
    expect(await screen.findByTestId("offline-ready")).toHaveTextContent(
      /^Ready offline · synced 15:05$/,
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

  it("says Ready offline without a time when it has never synced", async () => {
    stubServiceWorker({ ready: Promise.resolve({ active: {} }) });

    renderLine();

    expect(await screen.findByTestId("offline-ready")).toHaveTextContent(
      /^Ready offline$/,
    );
  });
});
