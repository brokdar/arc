import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { IntegrationsPanel } from "@/components/settings/integrations/integrations-panel";
import { AthleteClock } from "@/lib/clock";
import {
  ATHLETE_TIMEZONE,
  DROPBOX_CODE,
  integrationsState,
  seedDropboxAppKey,
  seedDropboxConnection,
  seedIntegration,
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
