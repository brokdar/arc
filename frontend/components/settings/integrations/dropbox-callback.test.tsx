import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DropboxCallback } from "@/components/settings/integrations/dropbox-callback";
import {
  connectionsState,
  DROPBOX_CODE,
  DROPBOX_VERIFICATION_DEFERRED,
  startDropboxAuthorization,
} from "@/tests/mocks/fixtures";

/**
 * The redirect flow's last step, which is a page rather than a step.
 *
 * The e2e suite owns the round trip — a real navigation to dropbox.com and
 * back — and this file owns what the page does with what comes back: the same
 * confirmation the paste flow ends on, because the proof is a property of the
 * connection and not of the route the athlete took to it.
 */
let search = new URLSearchParams();
const replace = vi.fn<(href: string) => void>();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => search,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: React.PropsWithChildren<{ href: string }>) => (
    <a href={href}>{children}</a>
  ),
}));

function renderCallback() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DropboxCallback />
    </QueryClientProvider>,
  );
}

/**
 * Start a redirect authorization and come back the way Dropbox would.
 *
 * The nonce is the one the server minted, read back out of the mock state:
 * the completion is refused unless it round-trips exactly, so a test that
 * invented one would be asserting against a refusal.
 */
function returnFromDropbox() {
  startDropboxAuthorization("http://localhost:3000/settings/dropbox/callback");
  search = new URLSearchParams({
    code: DROPBOX_CODE,
    state: connectionsState().authorizationState as string,
  });
}

beforeEach(() => {
  replace.mockClear();
  search = new URLSearchParams();
});

describe("coming back from Dropbox", () => {
  it("names the account, and waits to be sent on", async () => {
    const user = userEvent.setup();
    returnFromDropbox();

    renderCallback();

    // The same words the paste flow ends on: which flow the deployment could
    // offer is not something the athlete should be able to read off the
    // confirmation.
    const confirmation = await screen.findByTestId("connect-confirmation");
    expect(confirmation).toHaveTextContent(
      "Connected as Ada Lovelace (ada@example.com)",
    );
    expect(confirmation).toHaveTextContent(/read your Dropbox/i);
    // Not a page that flashes past on its way somewhere else.
    expect(replace).not.toHaveBeenCalled();

    await user.click(
      within(confirmation).getByRole("button", { name: /Choose the folder/i }),
    );
    expect(replace).toHaveBeenCalledWith("/settings");
  });

  it("carries the server's word for a connection it could not prove", async () => {
    connectionsState().verificationNote = DROPBOX_VERIFICATION_DEFERRED;
    returnFromDropbox();

    renderCallback();

    expect(await screen.findByTestId("connect-confirmation")).toHaveTextContent(
      /first time it looks for new rides/i,
    );
  });
});
