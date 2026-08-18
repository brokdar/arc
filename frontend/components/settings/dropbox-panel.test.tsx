import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { DropboxPanel } from "@/components/settings/dropbox-panel";
import { AthleteClock } from "@/lib/clock";
import { formatUtcStamp } from "@/lib/format";
import {
  ATHLETE_TIMEZONE,
  DROPBOX_CODE,
  dropboxFeed,
  seedAppFolderDropbox,
  seedDropboxConnection,
  WAHOO_ACTIVITY_FILES,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <DropboxPanel />
      </AthleteClock>
    </QueryClientProvider>,
  );
}

/**
 * The panel, once its first query has settled.
 *
 * Waiting on the *account label* rather than on the panel element: the panel
 * renders immediately with "Loading…" inside it, so a `findByTestId` would
 * hand back a surface that has not yet been told anything.
 */
async function panel(): Promise<HTMLElement> {
  await screen.findByText("Ada Lovelace (ada@example.com)");
  return screen.getByTestId("dropbox-panel");
}

describe("with nothing connected", () => {
  it("offers to connect and says in one sentence what that will do", async () => {
    renderPanel();

    expect(
      await screen.findByRole("button", { name: "Connect Dropbox" }),
    ).toBeInTheDocument();
    // Not "no connection": the empty state names what arc will do with the
    // credential, which is the thing the athlete is deciding about.
    expect(
      screen.getByText(/watch a folder in your Dropbox/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Disconnect/)).not.toBeInTheDocument();
  });

  it("renders the authorization link and takes the pasted code", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(
      await screen.findByRole("button", { name: "Connect Dropbox" }),
    );

    const link = await screen.findByRole("link", {
      name: /Open Dropbox to authorise arc/i,
    });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining("https://www.dropbox.com/oauth2/authorize"),
    );

    await user.type(screen.getByLabelText(/Authorisation code/i), DROPBOX_CODE);
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    expect(
      await screen.findByText("Ada Lovelace (ada@example.com)"),
    ).toBeInTheDocument();
  });

  it("shows a refusal in an alert and keeps the code that was pasted", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(
      await screen.findByRole("button", { name: "Connect Dropbox" }),
    );
    const field = await screen.findByLabelText(/Authorisation code/i);
    await user.type(field, "a-code-that-was-already-used");
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/already been used/i);
    // The athlete's paste survives the refusal: retyping a 43-character code
    // because the server said no is the ritual this feature exists to end.
    expect(field).toHaveValue("a-code-that-was-already-used");
  });
});

describe("with a connected account", () => {
  it("names the account, the permissions in plain words, and each folder", async () => {
    seedDropboxConnection({
      feeds: [dropboxFeed({ remote_path: "/apps/wahoofitness" })],
    });
    renderPanel();

    const surface = await panel();
    expect(
      within(surface).getByText("Ada Lovelace (ada@example.com)"),
    ).toBeInTheDocument();
    // Plain words, not scope strings: `files.metadata.read` tells the athlete
    // nothing about what arc will do with their Dropbox.
    expect(
      within(surface).getByText(/List the folders in your Dropbox/i),
    ).toBeInTheDocument();
    expect(
      within(surface).getByText(/Download the activity files it finds/i),
    ).toBeInTheDocument();
    expect(
      within(surface).queryByText("files.metadata.read"),
    ).not.toBeInTheDocument();
    expect(within(surface).getByText("/apps/wahoofitness")).toBeInTheDocument();
  });

  it("asks before disconnecting, and only then removes the account", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    // Behind a confirm: the credential is gone locally the moment this goes
    // through, and getting it back is the whole connect ritual again.
    const question = await screen.findByRole("alertdialog");
    expect(question).toBeInTheDocument();
    expect(
      screen.getByText("Ada Lovelace (ada@example.com)"),
    ).toBeInTheDocument();

    await user.click(
      within(question).getByRole("button", { name: "Disconnect Dropbox" }),
    );

    expect(
      await screen.findByRole("button", { name: "Connect Dropbox" }),
    ).toBeInTheDocument();
  });

  it("renders its dates and counts in monospace", async () => {
    seedDropboxConnection({
      feeds: [
        dropboxFeed(),
        dropboxFeed({
          id: "0199b000-0000-7000-8000-00000000f002",
          remote_path: "/apps/healthfit",
        }),
      ],
    });
    renderPanel();

    const surface = await panel();
    expect(within(surface).getByTestId("dropbox-connected-at")).toHaveClass(
      "font-mono",
    );
    expect(within(surface).getByTestId("dropbox-feed-count")).toHaveClass(
      "font-mono",
    );
    expect(within(surface).getByTestId("dropbox-feed-count")).toHaveTextContent(
      "2",
    );
  });
});

describe("what each folder has delivered", () => {
  it("labels the stamp as when arc last checked, not when a ride landed", async () => {
    seedDropboxConnection({
      feeds: [
        dropboxFeed({
          remote_path: "/apps/wahoofitness",
          cursor: "cursor-1",
          last_delivery_at: "2026-08-16T06:12:00Z",
        }),
      ],
    });
    renderPanel();

    const surface = await panel();
    const row = within(surface).getByTestId("dropbox-feed");
    expect(within(row).getByText("/apps/wahoofitness")).toBeInTheDocument();
    const delivery = within(row).getByTestId("dropbox-feed-delivery");
    // A timestamp is a numeral: convention 5.
    expect(delivery).toHaveClass("font-mono");
    // 06:12 UTC is 20:12 the same day for the `Pacific/Kiritimati` athlete the
    // fake backend serves. On the athlete's clock, not the server's: this row
    // is read against *now* to judge whether the feed is alive, and a stamp
    // fourteen hours out makes a poll from ten minutes ago look like a dead
    // feed. That the two differ at all is what the suite's zones are for — the
    // browser runs at UTC-11, so a component reading either wrong clock lands
    // on neither of these strings.
    expect(delivery).toHaveTextContent("16.08 20:12");
    // The field moves on every completed poll, empty folder included, so an
    // unlabelled stamp read as "a ride arrived then" — which made a rest week
    // and a broken feed look alike, the one thing this row must separate.
    expect(within(row).getByText(/last checked/i)).toBeInTheDocument();
  });

  it("says a folder has never been reached rather than showing a blank", async () => {
    seedDropboxConnection({
      feeds: [dropboxFeed({ last_delivery_at: null })],
    });
    renderPanel();

    const surface = await panel();
    // Not an em dash in a slot: "arc has not got through to this folder yet"
    // is the fact the athlete is looking for when a week is empty.
    expect(within(surface).getByText(/not checked yet/i)).toBeInTheDocument();
  });

  it("renders a feed's own error beside its folder", async () => {
    seedDropboxConnection({
      feeds: [
        dropboxFeed({
          remote_path: "/apps/wahoofitness",
          last_error: "ride.fit could not be downloaded: Dropbox answered 503",
        }),
      ],
    });
    renderPanel();

    const surface = await panel();
    const row = within(surface).getByTestId("dropbox-feed");
    expect(within(row).getByText("/apps/wahoofitness")).toBeInTheDocument();
    expect(
      within(row).getByText(/could not be downloaded/i),
    ).toBeInTheDocument();
  });

  it("distinguishes a paused folder from one that is merely quiet", async () => {
    seedDropboxConnection({
      feeds: [
        dropboxFeed({
          id: "0199b000-0000-7000-8000-00000000f0a1",
          remote_path: "/apps/silent",
          enabled: true,
          last_delivery_at: null,
        }),
        dropboxFeed({
          id: "0199b000-0000-7000-8000-00000000f0a2",
          remote_path: "/apps/switched-off",
          enabled: false,
          last_delivery_at: null,
        }),
      ],
    });
    renderPanel();

    const surface = await panel();
    const [quiet, paused] = within(surface).getAllByTestId("dropbox-feed");
    // Both are silent; only one of them is silent on purpose, and the athlete
    // has to be able to tell which without reading the buttons.
    expect(quiet).toHaveAttribute("data-enabled", "true");
    expect(paused).toHaveAttribute("data-enabled", "false");
    expect(within(paused).getByText(/paused/i)).toBeInTheDocument();
    expect(within(quiet).queryByText(/paused/i)).not.toBeInTheDocument();
  });
});

describe("the folder picker", () => {
  it("lists the folders arc can watch and starts watching the chosen one", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();

    await user.click(
      await screen.findByRole("button", { name: "Add a folder" }),
    );

    const picker = await screen.findByTestId("dropbox-folder-picker");
    await user.click(within(picker).getByRole("button", { name: "Open Apps" }));
    await user.click(
      await within(picker).findByRole("button", { name: "Watch WahooFitness" }),
    );

    expect(await screen.findByText("/apps/wahoofitness")).toBeInTheDocument();
  });

  it("says a folder is empty and still offers the root", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    renderPanel();

    await user.click(
      await screen.findByRole("button", { name: "Add a folder" }),
    );
    const picker = await screen.findByTestId("dropbox-folder-picker");
    await user.click(within(picker).getByRole("button", { name: "Open Apps" }));
    await user.click(
      await within(picker).findByRole("button", { name: "Open WahooFitness" }),
    );

    expect(
      await within(picker).findByText(/no folders inside it/i),
    ).toBeInTheDocument();
    // An empty box would be a dead end; the root is always a legal answer.
    expect(
      within(picker).getByRole("button", {
        name: /Watch the whole Dropbox/i,
      }),
    ).toBeInTheDocument();
  });
});

describe("the folders arc found by itself", () => {
  /** The stamp the panel should show, derived from the files the mock holds. */
  const NEWEST_WAHOO_STAMP = formatUtcStamp(
    WAHOO_ACTIVITY_FILES.map((file) => file.client_modified)
      .toSorted()
      .at(-1) as string,
  );

  async function openPicker(): Promise<HTMLElement> {
    const user = userEvent.setup();
    renderPanel();
    await user.click(
      await screen.findByRole("button", { name: "Add a folder" }),
    );
    return screen.findByTestId("dropbox-folder-picker");
  }

  it("names the folder the activity files are in, with how many and how recent", async () => {
    seedDropboxConnection();
    const picker = await openPicker();

    const candidate = await within(picker).findByTestId("dropbox-candidate");
    expect(within(candidate).getByText("/apps/wahoofitness")).toBeVisible();
    // Counted from the files the mock Dropbox actually holds, so a panel that
    // read the wrong field would disagree with the fixture rather than match
    // a number typed into it.
    expect(candidate).toHaveTextContent(String(WAHOO_ACTIVITY_FILES.length));
    expect(candidate).toHaveTextContent(NEWEST_WAHOO_STAMP);
  });

  it("watches the candidate at its own path and then lists it as watched", async () => {
    const user = userEvent.setup();
    seedDropboxConnection();
    const picker = await openPicker();

    await user.click(
      await within(picker).findByRole("button", {
        name: "Watch /apps/wahoofitness",
      }),
    );

    // The path goes back verbatim: rebuilding it from the display name is how
    // a feed ends up pointed at a folder that does not exist.
    const surface = await panel();
    const watched = within(surface).getByTestId("dropbox-feed");
    expect(within(watched).getByText("/apps/wahoofitness")).toBeVisible();
    expect(within(surface).getByTestId("dropbox-feed-count")).toHaveTextContent(
      "1",
    );
  });

  it("shows a candidate arc already watches without a control that would 409", async () => {
    seedDropboxConnection({
      feeds: [dropboxFeed({ remote_path: "/apps/wahoofitness" })],
    });
    const picker = await openPicker();

    const candidate = await within(picker).findByTestId("dropbox-candidate");
    expect(within(candidate).getByText(/already watching/i)).toBeVisible();
    expect(
      within(candidate).queryByRole("button", {
        name: /^Watch /,
      }),
    ).not.toBeInTheDocument();
  });

  it("keeps the candidates on screen and shows why the server refused", async () => {
    const user = userEvent.setup();
    // The folder was watched from another tab between the discovery read and
    // this click — a 409 the real API produces, not an invented failure.
    server.use(
      http.post("/api/v1/feeds", ({ response }) =>
        response(409).json({
          detail:
            "arc is already watching /apps/wahoofitness on this connection.",
        }),
      ),
    );
    seedDropboxConnection();
    const picker = await openPicker();

    await user.click(
      await within(picker).findByRole("button", {
        name: "Watch /apps/wahoofitness",
      }),
    );

    expect(await within(picker).findByRole("alert")).toHaveTextContent(
      /already watching/i,
    );
    expect(within(picker).getByTestId("dropbox-candidate")).toBeVisible();
  });

  it("says an App-folder app is the likely cause instead of drawing an empty tree", async () => {
    seedDropboxConnection();
    seedAppFolderDropbox();
    const picker = await openPicker();

    const diagnosis = await within(picker).findByTestId(
      "dropbox-access-type-suspect",
    );
    // Worded as something the athlete can go and check: no Dropbox API reports
    // an app's access type, so arc is inferring and has to say so.
    expect(diagnosis).toHaveTextContent(/App folder/);
    expect(diagnosis).toHaveTextContent(/cannot change/);
    expect(diagnosis).toHaveTextContent(/register a new app/);
    // An empty tree is what this replaces: it looked like a Dropbox with
    // nothing in it, which is the one thing it was not.
    expect(
      within(picker).queryByTestId("dropbox-folder-tree"),
    ).not.toBeInTheDocument();
  });
});

describe("when the credential has died", () => {
  it("names the account and offers to reconnect", async () => {
    seedDropboxConnection({
      status: "needs_reauth",
      last_error: "Dropbox refused arc's refresh token.",
    });
    renderPanel();

    const surface = await panel();
    expect(
      within(surface).getByText("Ada Lovelace (ada@example.com)"),
    ).toBeInTheDocument();
    expect(
      within(surface).getByRole("button", { name: "Reconnect Dropbox" }),
    ).toBeInTheDocument();
    expect(
      within(surface).getByText(/refused arc's refresh token/i),
    ).toBeInTheDocument();
    // Reconnecting is the remedy; browsing folders with a dead credential is
    // not on offer.
    expect(
      within(surface).queryByRole("button", { name: "Add a folder" }),
    ).not.toBeInTheDocument();
  });
});

describe("when the connections read fails outright", () => {
  it("says so instead of loading forever", async () => {
    server.use(
      http.untyped.get("*/api/v1/connections", () => HttpResponse.error()),
    );
    renderPanel();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Could not load/i);
    await waitFor(() => {
      expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    });
  });
});
