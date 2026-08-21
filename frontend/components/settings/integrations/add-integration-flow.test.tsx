import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { IntegrationsPanel } from "@/components/settings/integrations/integrations-panel";
import { AthleteClock } from "@/lib/clock";
import {
  ATHLETE_TIMEZONE,
  addIntegration,
  connectionsState,
  DROPBOX_CODE,
  DROPBOX_CONNECTION_ID,
  dropboxFolderPage,
  integrationCatalogue,
  integrationsState,
  postedIntegrations,
  seedAppFolderSuspicion,
  seedDiscoveredFolders,
  seedDropboxAppKey,
  seedDropboxConnection,
  seedIntegration,
  WAHOO_NEWEST_AT,
  WAHOO_PATH,
  WAHOO_PATH_DISPLAY,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

/**
 * What the API answers when arc's permission to read the Dropbox is gone.
 *
 * Spelled out rather than imported: it is the *backend's* sentence
 * (`app.connectors.dropbox.PERMISSION_LOST`), and a test that built it from
 * the same source as the code would pass while the two drifted apart.
 */
const PERMISSION_LOST =
  "arc lost its permission to read your Dropbox. Disconnect and connect again to fix it.";

/**
 * Walk the picker to the Wahoo folder and start watching it.
 *
 * Two clicks where there used to be one: the shortcut *navigates* now instead
 * of committing on arc's guess, so the athlete reads what is actually in the
 * folder before they tell arc to watch it. Shared because half this file ends
 * on the same commitment and each of them used to spell out a button label
 * carrying a path.
 */
async function watchWahooFolder(
  user: ReturnType<typeof userEvent.setup>,
  step: HTMLElement,
) {
  await user.click(
    within(step).getByRole("button", { name: "Go to Wahoo's folder" }),
  );
  await within(step).findByText(/files here/);
  await user.click(
    within(step).getByRole("button", { name: "Watch this folder" }),
  );
}

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
    // The key is pasted here, not into `.env`: the paste field is the step.
    expect(
      within(checklist).getByLabelText(/Dropbox app key/i),
    ).toBeInTheDocument();
    expect(checklist).toHaveTextContent(/dropbox.com\/developers\/apps/i);
    expect(checklist).toHaveTextContent(/Full Dropbox/i);
    // Never past a step that has not been done.
    expect(screen.queryByTestId("connect-step")).toBeNull();
    expect(screen.queryByTestId("folder-step")).toBeNull();
  });

  it("saves the pasted app key and then offers to connect", async () => {
    const user = userEvent.setup();
    seedDropboxAppKey(false);
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    const checklist = await screen.findByTestId("app-key-step");

    await user.type(
      within(checklist).getByLabelText(/Dropbox app key/i),
      "abc123def456",
    );
    await user.click(screen.getByRole("button", { name: "Save app key" }));

    // The save asks the flow to re-read the catalogue, whose `app_configured`
    // is derived from the same state the PUT wrote — so the checklist is done
    // and the next unanswered step is the account.
    expect(await screen.findByTestId("connect-step")).toBeInTheDocument();
    expect(screen.queryByTestId("app-key-step")).toBeNull();
    expect(connectionsState().storedAppKey).toBe("abc123def456");
  });

  it("returns to the checklist when the stored key is removed", async () => {
    const user = userEvent.setup();
    // A key stored in-app and nothing behind it: removing it leaves arc with
    // no key from either source, so the registration step is owed again.
    seedDropboxAppKey(false);
    connectionsState().storedAppKey = "stored-key";
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    await screen.findByTestId("connect-step");

    await user.click(
      await screen.findByRole("button", { name: "Use a different app" }),
    );

    expect(await screen.findByTestId("app-key-step")).toBeInTheDocument();
    expect(screen.queryByTestId("connect-step")).toBeNull();
    expect(connectionsState().storedAppKey).toBeNull();
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

    // The paste flow, deliberately: this test is about which step the flow
    // asks for next, and the redirect flow's answer is a navigation to
    // dropbox.com that no component test can follow. Which flow is chosen,
    // and what each one sends, is `dropbox-connect-step.test.tsx`.
    await user.click(await screen.findByTestId("use-paste-flow"));
    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    // The connect confirms itself before the flow moves on: the folder step
    // does not exist until the athlete has read which account was connected.
    await user.click(
      within(await screen.findByTestId("connect-confirmation")).getByRole(
        "button",
        { name: /Choose the folder/i },
      ),
    );

    // The connect step is done, so the flow moves on rather than asking again.
    const folder = await screen.findByTestId("folder-step");
    await watchWahooFolder(user, folder);

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

    await watchWahooFolder(user, folder);

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

  it("prints what the server said about a folder it could not list", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    // The API answered, and what it answered names the remedy. "Could not
    // load that folder. Is the API reachable?" is the sentence that sent a
    // real athlete hunting a folder-path problem that did not exist.
    server.use(
      http.get("/api/v1/connections/{connection_id}/folders", ({ response }) =>
        response(409).json({ detail: PERMISSION_LOST }),
      ),
    );
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    const folder = await screen.findByTestId("folder-step");

    expect(await within(folder).findByRole("alert")).toHaveTextContent(
      PERMISSION_LOST,
    );
    expect(within(folder).queryByText(/Is the API reachable/)).toBeNull();
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

/**
 * AC-12: the derivation that decides what to render, rendered.
 *
 * The steps stay derived — nothing here counts pages or re-asks a stored
 * answer. What changes is that the derivation's *output* is on screen: the
 * flow spans two applications and an OAuth round trip, and before this the
 * athlete could not see how much of it remained.
 */
describe("the map of the flow", () => {
  /** The three rows, by the state each one is in. */
  function rowStates() {
    return {
      appKey: screen.getByTestId("step-app-key").dataset.state,
      account: screen.getByTestId("step-account").dataset.state,
      folder: screen.getByTestId("step-folder").dataset.state,
    };
  }

  it("shows the stored app key done, the account open and the folder to come", async () => {
    const user = userEvent.setup();
    // The seeded instance holds `DROPBOX__APP_KEY` and no connection: the
    // first step is answered, the second is owed.
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    await screen.findByTestId("step-map");
    expect(rowStates()).toEqual({
      appKey: "done",
      account: "current",
      folder: "upcoming",
    });
    // All three in one render: the done one summarised, the current one
    // expanded where it stands, the last one named.
    expect(screen.getByTestId("step-app-key")).toHaveTextContent(
      /Register a Dropbox app/i,
    );
    expect(screen.getByTestId("step-app-key")).toHaveTextContent(
      /DROPBOX__APP_KEY/,
    );
    expect(
      within(screen.getByTestId("step-account")).getByTestId("connect-step"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("step-folder")).toHaveTextContent(/folder/i);
    expect(
      within(screen.getByTestId("step-folder")).queryByTestId("folder-step"),
    ).toBeNull();
  });

  it("names every step from the start, with the first one open", async () => {
    const user = userEvent.setup();
    seedDropboxAppKey(false);
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    await screen.findByTestId("step-map");
    // Nothing is hidden at the start either: a flow whose length is unknown
    // is one the athlete cannot decide to begin.
    expect(rowStates()).toEqual({
      appKey: "current",
      account: "upcoming",
      folder: "upcoming",
    });
    expect(screen.getByTestId("step-account")).toHaveTextContent(
      /Connect the Dropbox account/i,
    );
  });

  it("keeps the app key's last characters instead of asking for it again", async () => {
    const user = userEvent.setup();
    seedDropboxAppKey(false);
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    await screen.findByTestId("app-key-step");

    await user.type(
      screen.getByLabelText(/Dropbox app key/i),
      "abc123def456xyz",
    );
    await user.click(screen.getByRole("button", { name: "Save app key" }));

    // The checkmark moves and the answer is summarised, never re-asked. The
    // tail is enough to recognise the key by and not enough to be it.
    expect(await screen.findByTestId("connect-step")).toBeInTheDocument();
    expect(rowStates()).toEqual({
      appKey: "done",
      account: "current",
      folder: "upcoming",
    });
    expect(screen.getByTestId("step-app-key")).toHaveTextContent("6xyz");
    expect(screen.queryByLabelText(/Dropbox app key/i)).toBeNull();
  });

  it("puts a connect that came back by redirect where a pasted one lands", async () => {
    const user = userEvent.setup();
    // The state the browser returns in after Dropbox redirected to arc's
    // callback and the athlete read the confirmation there: an account is
    // connected, and only the folder is still owed.
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);

    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    await screen.findByTestId("step-map");
    expect(rowStates()).toEqual({
      appKey: "done",
      account: "done",
      folder: "current",
    });
    // The account row states which account, so the athlete does not have to
    // remember which of their Dropboxes they authorised.
    expect(screen.getByTestId("step-account")).toHaveTextContent(
      "Ada Lovelace (ada@example.com)",
    );
    expect(
      within(screen.getByTestId("step-folder")).getByTestId("folder-step"),
    ).toBeInTheDocument();
  });

  it("puts a pasted connect on the same row the redirect one reaches", async () => {
    const user = userEvent.setup();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));

    await user.click(await screen.findByTestId("use-paste-flow"));
    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));
    // The confirmation is the connect step's own completed state, and it is
    // still the current row until the athlete moves the flow on.
    const confirmation = await screen.findByTestId("connect-confirmation");
    expect(
      within(screen.getByTestId("step-account")).getByTestId(
        "connect-confirmation",
      ),
    ).toBeInTheDocument();
    await user.click(
      within(confirmation).getByRole("button", { name: /Choose the folder/i }),
    );

    await screen.findByTestId("folder-step");
    expect(rowStates()).toEqual({
      appKey: "done",
      account: "done",
      folder: "current",
    });
  });
});

/**
 * AC-15: the flow ends on a statement rather than by vanishing.
 *
 * Every other confirmation in this feature exists for the same reason: a
 * screen that disappears is indistinguishable from a screen that crashed, and
 * the athlete has just told arc to go and read a folder on a cadence.
 */
describe("when the folder is chosen", () => {
  it("says what arc will now do, and how to undo it", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    const folder = await screen.findByTestId("folder-step");

    await watchWahooFolder(user, folder);

    const done = await screen.findByTestId("flow-complete");
    // Display case, because the picker knew it: the completion names the
    // folder the athlete will go looking for in Dropbox, not the identity arc
    // stored (which `postedIntegrations` still asserts is `path_lower`).
    expect(done).toHaveTextContent(WAHOO_PATH_DISPLAY);
    // When the first check happens, in the athlete's terms — the folder is
    // watched from now on, and nothing has arrived yet.
    expect(done).toHaveTextContent(/first check/i);
    // UI convention 3 applied to a commitment: the undo is named where it is
    // made, not discovered later.
    expect(done).toHaveTextContent(/Pause/);
    expect(done).toHaveTextContent(/Stop watching/i);
    expect(done).toHaveTextContent(/Settings/);
    // The map is still there with the last row ticked, and the flow is still
    // open: the athlete closes it, not the render.
    expect(screen.getByTestId("step-folder").dataset.state).toBe("done");
    expect(screen.getByTestId("add-integration-flow")).toBeInTheDocument();
  });

  it("closes only when the athlete says so", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    const folder = await screen.findByTestId("folder-step");
    await watchWahooFolder(user, folder);
    const done = await screen.findByTestId("flow-complete");

    await user.click(within(done).getByRole("button", { name: "Done" }));

    await waitFor(() =>
      expect(screen.queryByTestId("add-integration-flow")).toBeNull(),
    );
    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
  });

  it("states the same thing when arc found the folder itself", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    const proposal = await within(flow).findByTestId("proposal-wahoo");

    await user.click(
      within(proposal).getByRole("button", { name: "Add Wahoo" }),
    );

    // Accepting a proposal is the same commitment reached by a shorter road,
    // so it ends on the same sentence rather than on a panel closing itself.
    const done = await screen.findByTestId("flow-complete");
    expect(done).toHaveTextContent(WAHOO_PATH);
    expect(done).toHaveTextContent(/first check/i);
  });
});

/**
 * AC-16 to AC-21: the one screen where the athlete decides something.
 *
 * Every assertion here is about a fact the picker used to withhold or get
 * wrong: the folder's real name, what is inside it, where the athlete is, what
 * the action they are about to take will do, and what the server said when a
 * listing failed.
 */
describe("choosing the folder arc watches", () => {
  /** Open the flow, pick Wahoo, and hand back the picker. */
  async function openPicker(user: ReturnType<typeof userEvent.setup>) {
    seedDropboxConnection();
    renderPanel();
    const flow = await openFlow(user);
    await user.click(within(flow).getByRole("button", { name: "Wahoo" }));
    return screen.findByTestId("folder-step");
  }

  it("renders Dropbox's own capitalisation and never the path arc stores", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);

    await user.click(await within(step).findByRole("button", { name: "Apps" }));

    // The row's own name, as the athlete's Dropbox spells it.
    expect(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    ).toBeInTheDocument();
    await user.click(
      within(step).getByRole("button", { name: "WahooFitness" }),
    );
    await waitFor(() =>
      expect(within(step).getByText("WahooFitness")).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );
    // The whole point: `/apps/wahoofitness` matches nothing the athlete can
    // see in Dropbox, and showing it read as a case bug in arc.
    expect(step.textContent).not.toContain(WAHOO_PATH);
    expect(step.textContent).not.toContain("wahoofitness");
    expect(step.textContent).toContain("Apps");
  });

  it("stores the path Dropbox canonicalises, not the one on screen", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    await user.click(await within(step).findByRole("button", { name: "Apps" }));
    await user.click(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    );
    await within(step).findByText(/files here/);

    await user.click(
      within(step).getByRole("button", { name: "Watch this folder" }),
    );

    // Display case is a rendering; `path_lower` is the identity the feed row
    // and `uq_feeds_connection_id_remote_path` are written against.
    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
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

  it("ends on the folder as the athlete's Dropbox spells it", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    await user.click(await within(step).findByRole("button", { name: "Apps" }));
    await user.click(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    );
    await within(step).findByText(/files here/);

    await user.click(
      within(step).getByRole("button", { name: "Watch this folder" }),
    );

    const done = await screen.findByTestId("flow-complete");
    expect(done).toHaveTextContent(WAHOO_PATH_DISPLAY);
    expect(done.textContent).not.toContain(WAHOO_PATH);
  });

  it("says how many files are here and how many arc can read", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    await user.click(await within(step).findByRole("button", { name: "Apps" }));

    await user.click(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    );

    // Five files, three of them rides: the gap is the CSV and the PNG, and it
    // is how the athlete recognises the folder their head unit writes to.
    // "Nothing but files in here" asserted a fact the old response could not
    // support — it listed folders only.
    await waitFor(() =>
      expect(step).toHaveTextContent(
        "No subfolders. 5 files here, 3 arc can read.",
      ),
    );
    expect(step).not.toHaveTextContent(/Nothing but files/);
  });

  it("counts the current folder's files while it still has subfolders", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);

    // The root: two subfolders and Dropbox's own getting-started PDF, which
    // arc cannot read — so the two numbers differ and neither is a subfolder
    // count.
    await waitFor(() =>
      expect(step).toHaveTextContent("1 file here, none arc can read."),
    );
    expect(step).not.toHaveTextContent(/No subfolders/);
  });

  it("says a folder is empty rather than guessing what is in it", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);

    await user.click(
      await within(step).findByRole("button", { name: "Photos" }),
    );

    await waitFor(() => expect(step).toHaveTextContent(/This folder is empty/));
    expect(step).not.toHaveTextContent(/files here/);
  });

  it("navigates by breadcrumb, with the root a single plain segment", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    const trail = within(step).getByRole("navigation", { name: "Folder path" });

    // At the root there is one segment and nothing to navigate to.
    expect(within(trail).getByText("Dropbox")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(trail).queryByRole("button")).toBeNull();
    // And the controls it replaced are gone.
    expect(
      within(step).queryByRole("button", { name: /Up one folder/i }),
    ).toBeNull();
    expect(within(step).queryByText(/Collect/)).toBeNull();

    await user.click(await within(step).findByRole("button", { name: "Apps" }));
    await user.click(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    );
    await within(step).findByText(/files here/);

    // Two levels down, every ancestor is one click away — not two presses of
    // an "up" button that cannot say how far up it goes.
    await user.click(within(trail).getByRole("button", { name: "Dropbox" }));

    await waitFor(() =>
      expect(within(trail).getByText("Dropbox")).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );
    expect(
      within(step).getByRole("button", { name: "Apps" }),
    ).toBeInTheDocument();
  });

  it("offers exactly one watch action, for the folder it names", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    await within(step).findByText(/file here/);

    // The screen used to carry one commit button per row, so a tree of eight
    // folders offered eight irreversible actions and named none of them.
    const watches = within(step).getAllByRole("button", { name: /watch/i });
    expect(watches).toHaveLength(1);
    expect(watches[0]).toHaveAccessibleName("Watch this folder");
    // What it does, and how to undo it, beside the control that does it.
    expect(step).toHaveTextContent(/every few minutes/);
    expect(step).toHaveTextContent(
      /Pause or stop watching any time in Settings/,
    );
    // A row does one thing: it opens.
    await user.click(within(step).getByRole("button", { name: "Apps" }));
    expect(
      await within(step).findByRole("button", { name: "WahooFitness" }),
    ).toBeInTheDocument();
  });

  it("refuses a second watch while the first is still in flight", async () => {
    const user = userEvent.setup();
    // Held open rather than delayed by a timer: the assertion is about the
    // state between the click and the answer, and a race decided by a
    // stopwatch is one that passes on a fast machine and not on a slow one.
    let answer = () => {};
    const held = new Promise<void>((resolve) => {
      answer = resolve;
    });
    server.use(
      http.post("/api/v1/integrations", async ({ request, response }) => {
        const body = await request.json();
        await held;
        const result = addIntegration(body);
        return "integration" in result
          ? response(201).json(result.integration)
          : response(409).json({ detail: result.detail });
      }),
    );
    const step = await openPicker(user);
    await within(step).findByText(/file here/);

    const watch = within(step).getByRole("button", {
      name: "Watch this folder",
    });
    await user.click(watch);

    await waitFor(() => expect(watch).toBeDisabled());
    // A second press has nothing to land on, so arc cannot be told twice to
    // watch the same folder and answer itself with its own 409.
    await user.click(watch);
    answer();

    expect(
      await screen.findByRole("region", { name: "Wahoo" }),
    ).toBeInTheDocument();
    expect(postedIntegrations()).toHaveLength(1);
  });

  it("keeps the athlete where they were when a listing fails", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);
    await user.click(await within(step).findByRole("button", { name: "Apps" }));
    await within(step).findByRole("button", { name: "WahooFitness" });

    // The next listing fails once. The athlete has not moved yet — the tree
    // they can see is still `/Apps`, and it stays clickable.
    let failures = 1;
    server.use(
      http.get(
        "/api/v1/connections/{connection_id}/folders",
        ({ query, response }) => {
          if (failures > 0) {
            failures -= 1;
            return response(409).json({ detail: PERMISSION_LOST });
          }
          const listing = dropboxFolderPage(query.get("path") ?? "");
          return listing === null
            ? response(404).json({ detail: "no such folder" })
            : response(200).json(listing);
        },
      ),
    );
    await user.click(
      within(step).getByRole("button", { name: "WahooFitness" }),
    );

    // The server's own sentence, not "Could not load that folder. Is the API
    // reachable?" — and not instead of the screen.
    const alert = await within(step).findByRole("alert");
    expect(alert).toHaveTextContent(PERMISSION_LOST);
    expect(
      within(step).getByRole("navigation", { name: "Folder path" }),
    ).toHaveTextContent("Apps");
    expect(
      within(step).getByRole("button", { name: "WahooFitness" }),
    ).toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    // The retry re-issues the listing that failed, and its success clears the
    // failure rather than leaving a stale red line under a working screen.
    await waitFor(() => expect(step).toHaveTextContent(/5 files here/));
    expect(within(step).queryByRole("alert")).toBeNull();
  });

  it("joins the shortcut to the tree with the reason for both", async () => {
    const user = userEvent.setup();
    const step = await openPicker(user);

    // The rationale used to live in the component's docstring, where the
    // athlete could not read it.
    expect(step).toHaveTextContent(/Wahoo usually writes to one folder/);
    expect(step).toHaveTextContent(/if your head unit files somewhere else/);

    // And the shortcut goes there rather than committing on arc's guess: the
    // contents line proves the folder before the athlete acts on it.
    await user.click(
      within(step).getByRole("button", { name: "Go to Wahoo's folder" }),
    );

    await waitFor(() =>
      expect(
        within(step).getByRole("navigation", { name: "Folder path" }),
      ).toHaveTextContent("WahooFitness"),
    );
    expect(step).toHaveTextContent(/5 files here, 3 arc can read/);
    expect(postedIntegrations()).toEqual([]);
  });

  it("leaves the tree standing alone when there is no folder to suggest", async () => {
    const user = userEvent.setup();
    // A source arc has no default path for. The copy about the shortcut is
    // about the shortcut, so with no shortcut there is nothing to explain.
    server.use(
      http.get("/api/v1/integration-catalogue", ({ response }) => {
        const catalogue = integrationCatalogue();
        return response(200).json({
          ...catalogue,
          items: catalogue.items.map((item) =>
            item.kind === "wahoo"
              ? {
                  ...item,
                  transports: item.transports.map((transport) => ({
                    ...transport,
                    default_path: null,
                  })),
                }
              : item,
          ),
        });
      }),
    );
    const step = await openPicker(user);
    await within(step).findByText(/file here/);

    expect(
      within(step).queryByRole("button", { name: /Go to Wahoo's folder/ }),
    ).toBeNull();
    expect(step).not.toHaveTextContent(/usually writes to one folder/);
    // The tree is still the way in, and still the only way in.
    expect(
      within(step).getByRole("button", { name: "Apps" }),
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
