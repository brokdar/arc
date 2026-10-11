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
  minutesAgo,
  seedDropboxConnection,
  seedIntegration,
  WAHOO_PATH,
  WAHOO_PATH_DISPLAY,
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

  it("names a watched folder as the athlete's Dropbox spells it", async () => {
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    // What the watch stored: the normalised path is the folder's identity,
    // the display path is its name.
    integrationsState().displays.set(WAHOO_PATH, WAHOO_PATH_DISPLAY);
    renderPanel();

    const wahoo = await entry("Wahoo");
    const folder = within(wahoo).getByTestId("integration-folder");

    // `remote_path` is `path_lower` and a lie about the folder's name;
    // `/apps/wahoofitness` in front of somebody looking at
    // `/Apps/WahooFitness` in Dropbox reads as a case bug in arc.
    expect(folder).toHaveTextContent(WAHOO_PATH_DISPLAY);
    expect(folder.textContent).not.toContain(WAHOO_PATH);
    expect(
      within(folder).getByRole("button", {
        name: `Stop watching ${WAHOO_PATH_DISPLAY}`,
      }),
    ).toBeInTheDocument();
  });

  it("falls back to the stored path for a folder watched before spellings", async () => {
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();

    const wahoo = await entry("Wahoo");

    // `null` is a state — arc never learned this folder's spelling — and the
    // row shows what it has always shown rather than a guessed capitalisation.
    expect(within(wahoo).getByTestId("integration-folder")).toHaveTextContent(
      WAHOO_PATH,
    );
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

  it("names no integration arc cannot deliver", async () => {
    const { container } = renderPanel();
    await entry("Local drop");

    // AC-28: what this exists to catch is a "coming soon" card — a Strava or
    // Garmin entry rendered for something arc cannot deliver.
    const text = (container.textContent ?? "").toLowerCase();
    for (const absent of ["strava", "zwift", "garmin", "apple"]) {
      expect(text).not.toContain(absent);
    }
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
  /** The line that states the account's health, whichever state it is in. */
  function health(): Promise<HTMLElement> {
    return screen.findByTestId("account-health");
  }

  it("states that the connection works, and when that was last true", async () => {
    // AC-11. Health used to be the *absence* of red text: a working account
    // and one whose grant had been revoked in the Dropbox console rendered
    // identically until an unrelated screen failed.
    seedDropboxConnection({ last_verified_at: minutesAgo(4) });
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    await entry("Wahoo");

    const line = await health();
    expect(line).toHaveTextContent("Connected as Ada Lovelace");
    expect(line).toHaveTextContent("last checked 4 minutes ago");
  });

  it("says nobody has checked yet rather than inventing a time", async () => {
    // AC-11 edge. A connection stored when Dropbox could not answer the
    // connect-time probe has no stamp until its first poll, and `created_at`
    // is the substitute that would report a check that never ran.
    seedDropboxConnection({ last_verified_at: null });
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    await entry("Wahoo");

    const line = await health();
    expect(line).toHaveTextContent("Connected as Ada Lovelace");
    expect(line).toHaveTextContent("not checked yet");
    expect(line).not.toHaveTextContent(/ago/);
  });

  it("renders the reconnect remedy the poll wrote, on the next fetch", async () => {
    // AC-9 edge: the payload shape the poll leaves behind after a scope
    // refusal. The panel is the first thing the athlete sees afterwards, and
    // it has to carry Dropbox's own remedy rather than a shrug.
    const refusal =
      "Dropbox will not let arc read your files. Open your app at " +
      "https://www.dropbox.com/developers/apps, tick files.metadata.read on " +
      "its Permissions tab, choose Submit, then disconnect this Dropbox " +
      "account here and connect it again — Dropbox gives arc a newly ticked " +
      "permission only on a connection made after you submit it.";
    seedDropboxConnection({
      status: "needs_reauth",
      last_error: refusal,
      // The stamp survives the flip: "it stopped working" and "nobody ever
      // checked" are different sentences, and the row knows which is true.
      last_verified_at: minutesAgo(120),
    });
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    await entry("Wahoo");

    const line = await health();
    expect(line).toHaveTextContent("files.metadata.read");
    expect(line).toHaveTextContent("Permissions");
    expect(line).toHaveTextContent("Submit");
    // The healthy sentence is gone, not merely joined by a warning.
    expect(line).not.toHaveTextContent("Connected as");
  });

  it("names no mechanism the athlete cannot see", async () => {
    seedDropboxConnection({ last_verified_at: minutesAgo(4) });
    seedIntegration("wahoo", [WAHOO_PATH]);
    const { container } = renderPanel();
    await entry("Wahoo");

    const text = (container.textContent ?? "").toLowerCase();
    for (const word of ["token", "credential", "the api"]) {
      expect(text).not.toContain(word);
    }
  });

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
