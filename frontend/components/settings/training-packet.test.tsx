import "fake-indexeddb/auto";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import { TrainingSettingsPanel } from "@/components/settings/training-settings";
import { storedPacket, syncNow } from "@/lib/offline/packet-store";
import { athleteToday } from "@/tests/mocks/fixtures";
import { packetServer } from "@/tests/mocks/handlers-packet";

/**
 * UAT B4: the offline gym screen kept showing kg and Simple-mode wording after
 * Settings switched to lb and Standard, because the packet is re-fetched at
 * most every five minutes and a settings change did not touch it.
 */
beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  packetServer.today = athleteToday();
  packetServer.served = [];
});

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TrainingSettingsPanel />
    </QueryClientProvider>,
  );
}

describe("a settings change reaches the stored packet", () => {
  it("re-fetches the packet at once, past the five-minute throttle", async () => {
    await syncNow();
    expect((await storedPacket())?.packet.settings.units).toBe("kg");
    const user = userEvent.setup();
    renderPanel();
    const region = await screen.findByRole("region", { name: "Training" });

    await user.selectOptions(
      await within(region).findByLabelText("Units"),
      "lb",
    );
    await user.selectOptions(within(region).getByLabelText("Mode"), "full");

    await waitFor(async () => {
      const stored = await storedPacket();
      expect(stored?.packet.settings).toMatchObject({
        units: "lb",
        mode: "full",
      });
    });
  });
});
