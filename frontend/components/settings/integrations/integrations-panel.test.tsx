import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { IntegrationsPanel } from "@/components/settings/integrations/integrations-panel";
import { AthleteClock } from "@/lib/clock";
import {
  ATHLETE_TIMEZONE,
  dropboxFeed,
  INBOX_PATH,
  integrationsState,
  seedDropboxConnection,
  seedIntegration,
  WAHOO_PATH,
} from "@/tests/mocks/fixtures";

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <IntegrationsPanel />
      </AthleteClock>
    </QueryClientProvider>,
  );
}

/**
 * One entry, by the name the athlete knows it as.
 *
 * `findByRole("region", …)` rather than a testid: AC-12 is about the entry's
 * *accessible name*, so reaching it any other way would let a panel that
 * labelled every entry "Dropbox" pass.
 */
function entry(name: string): Promise<HTMLElement> {
  return screen.findByRole("region", { name });
}

describe("what the panel lists", () => {
  it("has replaced the Dropbox-named panel rather than shadowing it", async () => {
    renderPanel();
    await entry("Local drop");

    // Gone, not hidden: a panel called "Dropbox" told the athlete they were
    // setting up a file host, when what they wanted was their rides.
    expect(screen.queryByTestId("dropbox-panel")).toBeNull();
  });

  it("states what the local drop brings in, where from, and what to configure", async () => {
    renderPanel();

    const local = await entry("Local drop");
    expect(within(local).getByTestId("integration-provides")).toHaveTextContent(
      "Rides and workouts",
    );
    expect(within(local).getByTestId("integration-source")).toHaveTextContent(
      INBOX_PATH,
    );
    const setup = within(local).getByTestId("integration-setup");
    expect(setup).toHaveTextContent("30");
    // Reads as active: the sweep has been running since long before the
    // athlete opened this page, and calling it unconfigured would be a lie
    // with a remedy attached.
    expect(setup).toHaveTextContent(/collecting/i);
    expect(local).not.toHaveTextContent(/not configured/i);
  });

  it("names Wahoo as the entry, and Dropbox only as where the data comes from", async () => {
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();

    const wahoo = await entry("Wahoo");
    expect(within(wahoo).getByTestId("integration-provides")).toHaveTextContent(
      "Rides and workouts",
    );
    const source = within(wahoo).getByTestId("integration-source");
    expect(source).toHaveTextContent("Dropbox");
    expect(source).toHaveTextContent(WAHOO_PATH);
    expect(within(wahoo).getByTestId("integration-setup")).toHaveTextContent(
      /folder/i,
    );
    // "Dropbox" is where the rides come from, never the name of the thing
    // being set up — so it appears on exactly one line of this entry.
    const elsewhere = [
      within(wahoo).getByTestId("integration-provides").textContent ?? "",
      within(wahoo).getByTestId("integration-setup").textContent ?? "",
      wahoo.getAttribute("aria-label") ?? "",
    ].join(" ");
    expect(elsewhere).not.toMatch(/dropbox/i);
  });

  it("prompts for the source of a folder nobody has classified", async () => {
    seedDropboxConnection({ feeds: [dropboxFeed({ remote_path: "/photos" })] });
    renderPanel();

    const loose = await entry("/photos");
    // UI convention 3: the prompt names the missing input *and* the action
    // that supplies it, rather than leaving a folder with a shrug beside it.
    const setup = within(loose).getByTestId("integration-setup");
    expect(setup).toHaveTextContent(/does not know which source/i);
    expect(setup).toHaveTextContent(/add the integration it belongs to/i);
    expect(within(loose).getByTestId("integration-provides")).toHaveTextContent(
      /has not been told/i,
    );
    // And it is its own entry — not folded into Wahoo, which was never added.
    expect(screen.queryByRole("region", { name: "Wahoo" })).toBeNull();
  });

  it("offers the add flow as the remedy when nothing but the local drop exists", async () => {
    renderPanel();
    await entry("Local drop");

    expect(screen.queryByRole("region", { name: "Wahoo" })).toBeNull();
    // The empty state is not "no integrations": it is the control that adds
    // one, beside the source that is already collecting.
    expect(
      screen.getByRole("button", { name: /Add an integration/i }),
    ).toBeInTheDocument();
  });

  it("lists every folder of an integration that has two", async () => {
    seedDropboxConnection();
    seedIntegration("wahoo", ["/apps/wahoo-backup", WAHOO_PATH]);
    renderPanel();

    const wahoo = await entry("Wahoo");

    const folders = within(wahoo).getAllByTestId("integration-folder");
    expect(folders.map((row) => row.textContent)).toEqual([
      expect.stringContaining("/apps/wahoo-backup"),
      expect.stringContaining(WAHOO_PATH),
    ]);
  });
});

describe("removing an integration", () => {
  it("asks first, then drops it from the list", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    const wahoo = await entry("Wahoo");

    await user.click(within(wahoo).getByRole("button", { name: /Remove/i }));
    await user.click(
      await screen.findByRole("button", { name: "Remove Wahoo" }),
    );

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Wahoo" })).toBeNull(),
    );
    expect(integrationsState().stored.size).toBe(0);
    // The local drop is untouched: it has no row to remove.
    expect(await entry("Local drop")).toBeInTheDocument();
  });

  it("pauses one folder and stops watching another, through the integration", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", ["/apps/wahoo-backup", WAHOO_PATH]);
    renderPanel();
    const wahoo = await entry("Wahoo");

    const [backup, main] = within(wahoo).getAllByTestId("integration-folder");
    await user.click(within(backup).getByRole("button", { name: "Pause" }));

    // Paused, not removed: the cursor survives, so a folder switched off for
    // a week resumes where it stopped.
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Wahoo" })).getAllByTestId(
          "integration-folder",
        )[0],
      ).toHaveAttribute("data-enabled", "false"),
    );

    await user.click(
      within(main).getByRole("button", {
        name: `Stop watching ${WAHOO_PATH}`,
      }),
    );

    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Wahoo" })).getAllByTestId(
          "integration-folder",
        ),
      ).toHaveLength(1),
    );
    expect(integrationsState().stored.get("wahoo")?.folders).toEqual([
      "/apps/wahoo-backup",
    ]);
  });

  it("takes the integration with the last folder that is removed", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    const wahoo = await entry("Wahoo");

    await user.click(
      within(wahoo).getByRole("button", {
        name: `Stop watching ${WAHOO_PATH}`,
      }),
    );

    // No integration ever exists with zero transports: an entry arc claims to
    // collect from and cannot reach is worse than no entry at all.
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Wahoo" })).toBeNull(),
    );
    expect(integrationsState().stored.size).toBe(0);
  });

  it("offers no removal at all for the local drop", async () => {
    renderPanel();

    const local = await entry("Local drop");

    expect(within(local).queryByRole("button", { name: /Remove/i })).toBeNull();
  });
});

describe("the account the folders are collected through", () => {
  it("says how many integrations a disconnect takes with it, by name", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    await entry("Wahoo");

    await user.click(
      screen.getByRole("button", { name: /Disconnect Dropbox/i }),
    );

    const question = await screen.findByRole("alertdialog");
    // Not "the folders go too": the athlete added *Wahoo*, and that is the
    // thing they would miss.
    expect(question).toHaveAccessibleName(/1 integration/i);
    expect(question).toHaveAccessibleName(/Wahoo/);
  });
});
