import "fake-indexeddb/auto";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import { HttpResponse } from "msw";
import { createElement } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { TodayView } from "@/components/today/today-view";
import { AthleteClock } from "@/lib/clock";
import { addDays } from "@/lib/dates";
import { putPacket } from "@/lib/offline/db";
import {
  isStale,
  PACKET_SCHEMA_VERSION,
  refreshPacket,
  STALE_AFTER_MS,
  storedPacket,
  syncNow,
} from "@/lib/offline/packet-store";
import { ATHLETE_TIMEZONE, athleteToday } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { packetFixture, packetServer } from "@/tests/mocks/handlers-packet";
import { server } from "@/tests/mocks/server";
import { syncServer } from "@/tests/mocks/sync-state";

/**
 * AC-48: after a sync the phone holds the packet in IndexedDB, and with no
 * server Today lists the session from it. The packet comes from the typed
 * `GET /strength/packet` handler (`tests/mocks/handlers-packet.ts`), a
 * document captured from the real endpoint; IndexedDB is `fake-indexeddb`,
 * fresh per test. "Offline" is the network failing — `HttpResponse.error()`,
 * what a fetch with no signal sees — never a mocked client.
 */

const today = athleteToday();

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  packetServer.today = today;
  packetServer.served = [];
});

function goOffline() {
  syncServer().healthy = false;
  server.use(
    http.get("/api/v1/plan/week", ({ response }) =>
      response.untyped(HttpResponse.error()),
    ),
  );
}

function renderToday() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      // A `.ts` file (the criterion names it), so no JSX: `AthleteClock`
      // types `children` as a required prop, which positional children of
      // `createElement` do not satisfy.
      createElement(AthleteClock, {
        timezone: ATHLETE_TIMEZONE,
        // biome-ignore lint/correctness/noChildrenProp: no JSX in a .ts test
        children: createElement(TodayView),
      }),
    ),
  );
}

describe("syncing the packet", () => {
  it("stores the packet the server served, unchanged, after a sync", async () => {
    expect(await storedPacket()).toBeNull();

    await syncNow();

    const stored = await storedPacket();
    expect(packetServer.served).toHaveLength(1);
    expect(stored?.packet).toEqual(packetServer.served[0]);
  });

  it("keeps the stored packet when the refresh cannot reach a server", async () => {
    await syncNow();
    const before = await storedPacket();
    goOffline();

    expect(await refreshPacket()).toBe("offline");

    expect(await storedPacket()).toEqual(before);
  });

  it("refuses a packet newer than this build reads and keeps the old one", async () => {
    await syncNow();
    const before = await storedPacket();
    server.use(
      http.get("/api/v1/strength/packet", ({ response }) =>
        response(200).json({
          ...packetFixture(today, new Date()),
          schema_version: PACKET_SCHEMA_VERSION + 1,
        }),
      ),
    );

    expect(await refreshPacket()).toBe("refused");

    expect(await storedPacket()).toEqual(before);
  });

  it("does not re-fetch a packet stored moments ago on every outbox tick", async () => {
    const t0 = Date.now();
    await syncNow(() => t0);
    await syncNow(() => t0 + 30_000);

    expect(packetServer.served).toHaveLength(1);
  });
});

describe("Today offline", () => {
  it("lists today's session from the stored packet when no server answers", async () => {
    await syncNow();
    goOffline();

    renderToday();

    const section = await screen.findByTestId("offline-sessions");
    expect(
      within(section).getByText("Single-Arm Dumbbell Row"),
    ).toBeInTheDocument();
    expect(within(section).getByText("Front Plank")).toBeInTheDocument();
    // The packet's own pre-fill, not a number computed here.
    expect(
      within(section).getByText("3×10 per side · 20 kg"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Could not load today/)).not.toBeInTheDocument();
    expect(within(section).queryByText("Connect to refresh")).toBeNull();
  });

  it("shows a packet older than seven days with Connect to refresh, still listed", async () => {
    const eightDaysAgo = new Date(Date.now() - STALE_AFTER_MS - 86_400_000);
    const old = packetFixture(addDays(today, -8), eightDaysAgo);
    expect(isStale(old, Date.now())).toBe(true);
    await putPacket({ packet: old, storedAt: eightDaysAgo.getTime() });
    goOffline();

    renderToday();

    const section = await screen.findByTestId("offline-sessions");
    expect(within(section).getByRole("status")).toHaveTextContent(
      "Connect to refresh",
    );
    expect(
      within(section).getByText("Single-Arm Dumbbell Row"),
    ).toBeInTheDocument();
  });

  it("says Today could not load when no packet was ever stored", async () => {
    goOffline();

    renderToday();

    expect(await screen.findByText(/Could not load today/)).toBeInTheDocument();
    expect(screen.queryByTestId("offline-sessions")).toBeNull();
  });
});
