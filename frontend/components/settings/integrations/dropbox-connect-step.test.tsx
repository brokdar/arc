import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  DropboxAppKeyStep,
  DropboxConnectStep,
} from "@/components/settings/integrations/dropbox-connect-step";
import {
  connectionsState,
  MAX_APP_KEY_LENGTH,
  seedDropboxAppKey,
} from "@/tests/mocks/fixtures";

/**
 * The two steps are exercised as the add flow renders them: with real
 * providers, against the typed MSW handlers, never against a mocked client.
 * What the flow *around* them decides — which step is owed — is
 * `add-integration-flow.test.tsx`'s business; what each step posts, echoes
 * and offers is this file's.
 */
function renderStep(step: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{step}</QueryClientProvider>,
  );
}

describe("the app-key step", () => {
  it("carries the registration checklist and the paste field, no .env in it", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onRecheck={() => {}} checking={false} />);

    const link = await screen.findByRole("link", { name: /developers\/apps/i });
    expect(link).toHaveAttribute(
      "href",
      "https://www.dropbox.com/developers/apps",
    );
    // The irreversible choice, named where the decision is made: an
    // App-folder app connects perfectly and then reads nothing.
    const steps = screen.getAllByRole("listitem");
    const accessType = steps.filter((step) =>
      step.textContent?.includes("Full Dropbox"),
    );
    expect(accessType).toHaveLength(1);
    expect(accessType[0]).toHaveTextContent(/cannot/i);
    // The key goes into the field below, not into a file and a restart.
    expect(screen.getByLabelText(/Dropbox app key/i)).toBeInTheDocument();
    expect(screen.queryByText(/DROPBOX__APP_KEY/)).not.toBeInTheDocument();
  });

  it("stores the pasted key and asks the flow to recheck", async () => {
    const user = userEvent.setup();
    const onRecheck = vi.fn();
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onRecheck={onRecheck} checking={false} />);

    await user.type(
      await screen.findByLabelText(/Dropbox app key/i),
      "abc123def456",
    );
    await user.click(screen.getByRole("button", { name: "Save app key" }));

    // The key the form was told, not a canned reply: the step is useless if
    // it posts anything else.
    await waitFor(() =>
      expect(connectionsState().storedAppKey).toBe("abc123def456"),
    );
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal of a key it will not take", async () => {
    const user = userEvent.setup();
    const onRecheck = vi.fn();
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onRecheck={onRecheck} checking={false} />);

    // Longer than MAX_APP_KEY_LENGTH: a paste of the console page's URL, or
    // of the wrong field entirely. The form cannot judge it — the server
    // does, and what it says has to reach the athlete.
    await user.click(await screen.findByLabelText(/Dropbox app key/i));
    await user.paste("k".repeat(MAX_APP_KEY_LENGTH + 1));
    await user.click(screen.getByRole("button", { name: "Save app key" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /at most 128 characters/i,
    );
    expect(connectionsState().storedAppKey).toBeNull();
    expect(onRecheck).not.toHaveBeenCalled();
  });
});

describe("the connect step's app-key line", () => {
  it("names the environment key and offers no way to remove it here", async () => {
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    expect(
      await screen.findByText(/app key from DROPBOX__APP_KEY/i),
    ).toBeInTheDocument();
    // A remove control against an environment key would appear to do
    // nothing: that key is undone in `.env`, not here.
    expect(
      screen.queryByRole("button", { name: "Use a different app" }),
    ).not.toBeInTheDocument();
  });

  it("connects on the stored key, which wins over the environment", async () => {
    const user = userEvent.setup();
    connectionsState().storedAppKey = "stored-key-wins";
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    expect(await screen.findByText(/app key saved here/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Connect Dropbox" }));

    // The link carries the key in force, echoed by the handler rather than
    // canned: a connect offered on the wrong key would fail on Dropbox's own
    // error page, minutes later.
    const link = await screen.findByRole("link", {
      name: /Open Dropbox to authorise arc/i,
    });
    expect(link.getAttribute("href")).toContain("client_id=stored-key-wins");
  });

  it("falls back to the environment key when the stored one is removed", async () => {
    const user = userEvent.setup();
    connectionsState().storedAppKey = "wrong-app-key";
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    await user.click(
      await screen.findByRole("button", { name: "Use a different app" }),
    );

    await waitFor(() => expect(connectionsState().storedAppKey).toBeNull());
    // The seed still holds `DROPBOX__APP_KEY`, so the line switches source
    // rather than disappearing — and says which one is in force now.
    expect(
      await screen.findByText(/app key from DROPBOX__APP_KEY/i),
    ).toBeInTheDocument();
  });
});
