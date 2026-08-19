import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { IntegrationsPanel } from "@/components/settings/integrations/integrations-panel";
import { AthleteClock } from "@/lib/clock";
import {
  ATHLETE_TIMEZONE,
  DROPBOX_CODE,
  DROPBOX_CONNECTION_ID,
  integrationsState,
  postedIntegrations,
  seedAppFolderSuspicion,
  seedDiscoveredFolders,
  seedDropboxAppKey,
  seedDropboxConnection,
  seedIntegration,
  WAHOO_NEWEST_AT,
  WAHOO_PATH,
} from "@/tests/mocks/fixtures";

/**
 * The flow is exercised through the panel that opens it, not in isolation.
 *
 * The thing AC-13 is about is "adding Wahoo is picking Wahoo", and that
 * sentence starts at the control on the settings page — a flow rendered with
 * hand-passed props could skip a step the panel never lets it skip.
 */
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

async function openFlow(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("region", { name: "Local drop" });
  await user.click(screen.getByRole("button", { name: /Add an integration/i }));
  return screen.findByTestId("add-integration-flow");
}

describe("choosing what to add", () => {
  it("lists Wahoo by name and never the local drop", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();

    const flow = await openFlow(user);

    expect(
      within(flow).getByRole("button", { name: "Wahoo" }),
    ).toBeInTheDocument();
    // The local drop is always present, so offering it here would be offering
    // something that cannot be done.
    expect(
      within(flow).queryByRole("button", { name: "Local drop" }),
    ).toBeNull();
  });

  it("skips the transport question when there is only one way to collect it", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    // Wahoo declares exactly one transport, so asking "how?" would be a
    // question with one answer.
    expect(screen.queryByTestId("transport-step")).toBeNull();
    expect(await screen.findByTestId("folder-step")).toBeInTheDocument();
  });
});

describe("the Dropbox transport's steps", () => {
  it("shows the app-registration checklist first when no app key is stored", async () => {
    const user = userEvent.setup();
    seedDropboxAppKey(false);
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    const checklist = await screen.findByTestId("app-key-step");
    expect(checklist).toHaveTextContent("DROPBOX__APP_KEY");
    expect(checklist).toHaveTextContent(/dropbox.com\/developers\/apps/i);
    expect(checklist).toHaveTextContent(/Full Dropbox/i);
    // Never past a step that has not been done.
    expect(screen.queryByTestId("connect-step")).toBeNull();
    expect(screen.queryByTestId("folder-step")).toBeNull();
  });

  it("skips the checklist when the app key is already stored", async () => {
    const user = userEvent.setup();
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    expect(screen.queryByTestId("app-key-step")).toBeNull();
    expect(await screen.findByTestId("connect-step")).toBeInTheDocument();
  });

  it("opens straight on the folder when the account is already connected", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    expect(screen.queryByTestId("app-key-step")).toBeNull();
    expect(screen.queryByTestId("connect-step")).toBeNull();
    expect(await screen.findByTestId("folder-step")).toBeInTheDocument();
  });

  it("walks connect then folder, and adds Wahoo at the end of it", async () => {
    const user = userEvent.setup();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    await user.click(
      await screen.findByRole("button", { name: /Connect Dropbox/i }),
    );
    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    // The connect step is done, so the flow moves on rather than asking again.
    const folder = await screen.findByTestId("folder-step");
    await user.click(
      within(folder).getByRole("button", { name: `Collect ${WAHOO_PATH}` }),
    );

    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
    expect(integrationsState().stored.get("wahoo")?.folders).toEqual([
      WAHOO_PATH,
    ]);
  });
});

describe("when it does not go through", () => {
  it("cancelling leaves nothing configured", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    await screen.findByTestId("folder-step");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByTestId("add-integration-flow")).toBeNull(),
    );
    expect(integrationsState().stored.size).toBe(0);
    expect(screen.queryByRole("region", { name: "Wahoo" })).toBeNull();
  });

  it("renders the server's refusal naming the holder and stays open", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    const folder = await screen.findByTestId("folder-step");

    await user.click(
      within(folder).getByRole("button", { name: `Collect ${WAHOO_PATH}` }),
    );

    // The server's own words, because it is the only party that knows who
    // holds the folder — and the flow stays up so the athlete can pick another.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Wahoo is already collecting ${WAHOO_PATH}`,
    );
    expect(screen.getByTestId("folder-step")).toBeInTheDocument();
    expect(integrationsState().stored.get("wahoo")?.folders).toEqual([
      WAHOO_PATH,
    ]);
  });
});

/**
 * AC-21 to AC-23: what arc found, named — and accepted in one click.
 *
 * The proposals are the moment the whole feature exists for: before this, the
 * one screen where the athlete decided something offered them
 * `/apps/wahoofitness` with a file count and left them to work out it meant
 * their bike computer.
 */
describe("what arc found in the athlete's Dropbox", () => {
  it("names the integration behind the folder and posts the proposal verbatim", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);

    const proposal = await within(flow).findByTestId("proposal-wahoo");
    // The source, by name — not the path the athlete would have to translate.
    expect(proposal).toHaveTextContent("Wahoo");
    expect(proposal).toHaveTextContent("3 activity files");
    await user.click(
      within(proposal).getByRole("button", { name: "Add Wahoo" }),
    );

    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
    // Verbatim: the proposal's own kind, transport, connection and path, so
    // accepting and adding by hand are one write path with one set of refusals.
    expect(postedIntegrations()).toEqual([
      {
        kind: "wahoo",
        transport: "cloud_folder",
        connection_id: DROPBOX_CONNECTION_ID,
        remote_path: WAHOO_PATH,
      },
    ]);
    expect(integrationsState().stored.get("wahoo")?.folders).toEqual([
      WAHOO_PATH,
    ]);
  });

  it("renders the newest stamp on the athlete's clock, not in UTC", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);

    const proposal = await within(flow).findByTestId("proposal-wahoo");

    // `2026-08-16T06:12:00Z` is 20:12 where this athlete lives (UTC+14). A
    // stamp shown in UTC is a stamp they compare against the wrong ride.
    expect(WAHOO_NEWEST_AT).toBe("2026-08-16T06:12:00Z");
    expect(proposal).toHaveTextContent("16.08 20:12");
    expect(proposal).not.toHaveTextContent("06:12");
  });

  it("makes the athlete name the source of a folder arc cannot", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedDiscoveredFolders({
      path: "/rides",
      activityFiles: 2,
      newestAt: "2026-08-15T05:00:00Z",
    });
    renderPanel();
    const flow = await openFlow(user);

    const proposal = await within(flow).findByTestId("proposal-/rides");
    // Nothing is guessed from the folder's name, so the accept control cannot
    // do anything until the athlete has said what writes there.
    const accept = within(proposal).getByRole("button", {
      name: "Add this folder",
    });
    expect(accept).toBeDisabled();

    await user.selectOptions(
      within(proposal).getByLabelText(/Which source writes to \/rides/i),
      "wahoo",
    );
    expect(accept).toBeEnabled();
    await user.click(accept);

    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
    expect(postedIntegrations()).toEqual([
      {
        kind: "wahoo",
        transport: "cloud_folder",
        connection_id: DROPBOX_CONNECTION_ID,
        remote_path: "/rides",
      },
    ]);
  });

  it("shows a folder arc already collects without a control to add it twice", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedIntegration("wahoo", [WAHOO_PATH]);
    renderPanel();
    const flow = await openFlow(user);

    const proposal = await within(flow).findByTestId("proposal-wahoo");

    // Reported rather than hidden — "arc already has these" is the answer to
    // the question the athlete came here with — but not offered again.
    expect(proposal).toHaveTextContent(/already collecting/i);
    expect(
      within(proposal).queryByRole("button", { name: "Add Wahoo" }),
    ).toBeNull();
  });

  it("renders the server's refusal and leaves the proposals on screen", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    const proposal = await within(flow).findByTestId("proposal-wahoo");

    // The folder is claimed after discovery read it and before the athlete
    // accepts — a second tab, or an add made earlier in this session. The
    // proposal on screen is now stale, and the server is the one that knows.
    seedIntegration("wahoo", [WAHOO_PATH]);
    await user.click(
      within(proposal).getByRole("button", { name: "Add Wahoo" }),
    );

    expect(await within(flow).findByRole("alert")).toHaveTextContent(
      `Wahoo is already collecting ${WAHOO_PATH}`,
    );
    // Still there: the athlete picks another rather than starting over.
    expect(within(flow).getByTestId("proposal-wahoo")).toBeInTheDocument();
  });

  it("says nothing is there rather than listing a folder with no rides", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedDiscoveredFolders();
    renderPanel();
    const flow = await openFlow(user);

    // UI convention 3: the remedy is beside the empty state — the catalogue
    // below it is how the athlete adds a source arc could not find.
    expect(await within(flow).findByTestId("discovery")).toHaveTextContent(
      /no training data/i,
    );
    expect(
      within(flow).getByRole("button", { name: "Wahoo" }),
    ).toBeInTheDocument();
  });
});

describe("when the Dropbox app cannot see the athlete's Dropbox", () => {
  it("diagnoses App-folder access instead of rendering an empty tree", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedAppFolderSuspicion();
    renderPanel();
    const flow = await openFlow(user);

    const alert = await within(flow).findByRole("alert");
    expect(alert).toHaveTextContent("App folder");
    // The remedy carries a cost, and saying so is the point: Dropbox will not
    // change an app's access type, so this is a new app or nothing.
    expect(alert).toHaveTextContent("cannot change");
    expect(alert).toHaveTextContent("register a new app");
    expect(screen.queryByTestId("folder-tree")).toBeNull();
  });

  it("keeps saying so at the folder step, where the empty tree used to be", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    seedAppFolderSuspicion();
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    const step = await screen.findByTestId("folder-step");
    expect(within(step).getByRole("alert")).toHaveTextContent("App folder");
    expect(screen.queryByTestId("folder-tree")).toBeNull();
  });
});
