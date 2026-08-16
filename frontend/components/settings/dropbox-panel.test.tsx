import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { DropboxPanel } from "@/components/settings/dropbox-panel";
import {
  DROPBOX_CODE,
  dropboxFeed,
  seedDropboxConnection,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DropboxPanel />
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
  it("shows the folder and its last delivery as a monospace timestamp", async () => {
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
    expect(delivery).toHaveTextContent("16.08 06:12");
  });

  it("says a folder has delivered nothing rather than showing a blank", async () => {
    seedDropboxConnection({
      feeds: [dropboxFeed({ last_delivery_at: null })],
    });
    renderPanel();

    const surface = await panel();
    // Not an em dash in a slot: "never delivered" is the fact the athlete is
    // looking for when a week is empty.
    expect(within(surface).getByText(/no deliveries yet/i)).toBeInTheDocument();
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
