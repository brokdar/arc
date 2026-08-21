import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DropboxAppKeyStep,
  DropboxConnectStep,
} from "@/components/settings/integrations/dropbox-connect-step";
import { takeAddFlow } from "@/lib/dropbox-redirect";
import {
  connectionsState,
  DROPBOX_CODE,
  DROPBOX_VERIFICATION_DEFERRED,
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

/**
 * Where the browser is, which is what chooses the flow.
 *
 * jsdom serves `http://localhost:3000`, which Dropbox *will* redirect to — so
 * the default here is the redirect flow, and the paste flow has to be reached
 * by moving the window somewhere Dropbox refuses. Both are real deployments
 * and both are asserted.
 */
function atOrigin(origin: string) {
  const url = new URL(origin);
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: {
      ...window.location,
      origin: url.origin,
      href: `${url.origin}/settings`,
      protocol: url.protocol,
      host: url.host,
      hostname: url.hostname,
      assign: navigated,
    },
  });
}

/** Every URL the step handed the browser to. */
const navigated = vi.fn<(url: string) => void>();
const realLocation = window.location;

beforeEach(() => {
  navigated.mockClear();
  atOrigin("http://localhost:3000");
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: realLocation,
  });
});

describe("the app-key step", () => {
  it("carries the registration checklist and the paste field, no .env in it", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);

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

  it("gives the redirect URI to register, as text and as a copy", async () => {
    const user = userEvent.setup();
    const clipboard = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboard },
    });
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);

    // As text, and not only behind a button: a browser that refuses clipboard
    // access must not leave the athlete unable to complete the step, and a
    // mistyped redirect URI fails on dropbox.com naming neither arc nor the
    // character that is wrong.
    expect(await screen.findByTestId("redirect-uri")).toHaveTextContent(
      "http://localhost:3000/settings/dropbox/callback",
    );
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(clipboard).toHaveBeenCalledWith(
      "http://localhost:3000/settings/dropbox/callback",
    );
  });

  it("asks for no redirect URI where Dropbox would refuse one", async () => {
    atOrigin("http://192.168.1.50");
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);

    // A step that cannot be completed is worse than one that is not there:
    // Dropbox refuses to register this URI at all, so the checklist says what
    // happens instead.
    expect(
      await screen.findByText(/show you a code to copy at the end/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("redirect-uri")).toBeNull();
  });

  it("stores the pasted key and hands it back to the flow", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={onSaved} checking={false} />);

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
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal of a key it will not take", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={onSaved} checking={false} />);

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
    expect(onSaved).not.toHaveBeenCalled();
  });
});

/**
 * AC-13: the checklist spans two applications, and says so on every line.
 *
 * The run-through this plan came from had the athlete looking for a Dropbox
 * console field in arc's Settings. Nothing on the list said which side of the
 * boundary an instruction belonged to, and one line was on both sides at once.
 */
describe("where each registration step happens", () => {
  /** Every checklist row, with the side it says it belongs to. */
  function checklistRows() {
    return screen.getAllByRole("listitem").map((item) => ({
      item,
      where: within(item).getAllByTestId("where"),
    }));
  }

  it("badges every checklist item with one side of the boundary", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);
    await screen.findByRole("link", { name: /developers\/apps/i });

    const rows = checklistRows();
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      // Exactly one: an item carrying two locations is the defect AC-13 names,
      // and an item carrying none is the orientation gap it closes.
      expect(row.where).toHaveLength(1);
      expect(["on dropbox.com", "in arc"]).toContain(
        row.where[0].textContent?.trim(),
      );
    }
  });

  it("splits copying the App key from pasting it into arc", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);
    await screen.findByRole("link", { name: /developers\/apps/i });

    const rows = checklistRows();
    const copy = rows.filter((row) =>
      /Copy the/i.test(row.item.textContent ?? ""),
    );
    const paste = rows.filter((row) =>
      /Paste/i.test(row.item.textContent ?? ""),
    );
    expect(copy).toHaveLength(1);
    expect(paste).toHaveLength(1);
    // Copying is done on the console; pasting is done here. One item that said
    // both was one item the athlete could only half finish where they stood.
    expect(copy[0].where[0]).toHaveTextContent("on dropbox.com");
    expect(copy[0].item).not.toHaveTextContent(/paste/i);
    expect(paste[0].where[0]).toHaveTextContent("in arc");
  });

  it("badges the redirect-URI item too", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);

    const uri = await screen.findByTestId("redirect-uri");
    const item = uri.closest("li");
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByTestId("where")).toHaveTextContent(
      "on dropbox.com",
    );
  });
});

/**
 * AC-14: the form judges the *shape* of an app key and nothing more.
 *
 * arc cannot tell a real key from a plausible one without an OAuth round trip,
 * so it never says a key looks right. It does catch the two paste accidents it
 * can see — a value with whitespace in it, and the address of the console page
 * the key was on — because both fail minutes later on somebody else's site.
 */
describe("what the app-key field will accept", () => {
  async function pasteKey(value: string) {
    const user = userEvent.setup();
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);
    await user.click(await screen.findByLabelText(/Dropbox app key/i));
    await user.paste(value);
    return user;
  }

  it("refuses a key with a space in it and says what one looks like", async () => {
    await pasteKey("abc123 def456");

    expect(screen.getByRole("button", { name: "Save app key" })).toBeDisabled();
    // Names the thing being asked for. It never claims the key is right —
    // only arc's own failure at the next step could say that.
    expect(screen.getByTestId("app-key-hint")).toHaveTextContent(
      /letters and digits/i,
    );
    expect(screen.queryByText(/looks valid/i)).toBeNull();
  });

  it("refuses the console page's address, then takes the key off it", async () => {
    const user = await pasteKey(
      "https://www.dropbox.com/developers/apps/info/abc123def456",
    );

    expect(screen.getByRole("button", { name: "Save app key" })).toBeDisabled();
    expect(screen.getByTestId("app-key-hint")).toHaveTextContent(/address/i);

    await user.clear(screen.getByLabelText(/Dropbox app key/i));
    await user.paste("abc123def456");

    expect(screen.getByRole("button", { name: "Save app key" })).toBeEnabled();
    expect(screen.queryByTestId("app-key-hint")).toBeNull();
  });

  it("says nothing about an empty field", async () => {
    seedDropboxAppKey(false);
    renderStep(<DropboxAppKeyStep onSaved={() => {}} checking={false} />);

    // A hint on an untouched field is a refusal of something nobody typed.
    await screen.findByLabelText(/Dropbox app key/i);
    expect(screen.queryByTestId("app-key-hint")).toBeNull();
    expect(screen.getByRole("button", { name: "Save app key" })).toBeDisabled();
  });

  it("trims the spaces around a pasted key rather than refusing it", async () => {
    const user = await pasteKey("  abc123def456  ");

    // A key copied out of the console arrives with a newline more often than
    // not; refusing it would be arc failing at the one thing it can check.
    const save = screen.getByRole("button", { name: "Save app key" });
    expect(save).toBeEnabled();
    expect(screen.queryByTestId("app-key-hint")).toBeNull();

    await user.click(save);
    await waitFor(() =>
      expect(connectionsState().storedAppKey).toBe("abc123def456"),
    );
  });
});

describe("connecting where Dropbox will redirect back", () => {
  it("sends this browser's own address and follows the link in this tab", async () => {
    const user = userEvent.setup();
    renderStep(
      <DropboxConnectStep onConnected={() => {}} integrationKind="wahoo" />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Connect Dropbox" }),
    );

    // The redirect URI is the browser's own origin plus arc's callback route.
    // A server-derived one would be a proxy's idea of where arc is.
    await waitFor(() =>
      expect(connectionsState().authorizationRedirectUri).toBe(
        "http://localhost:3000/settings/dropbox/callback",
      ),
    );
    // Followed in *this* tab: the redirect has to come back somewhere, and a
    // popup arc cannot get the athlete out of is what this replaces.
    await waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));
    const url = new URL(navigated.mock.calls[0][0]);
    expect(url.searchParams.get("redirect_uri")).toBe(
      "http://localhost:3000/settings/dropbox/callback",
    );
    expect(url.searchParams.get("state")).toBe(
      connectionsState().authorizationState,
    );
    // Where the add flow was, parked before the tab leaves: React state does
    // not survive a navigation to dropbox.com.
    expect(takeAddFlow()).toEqual({ kind: "wahoo" });
  });

  it("offers no code field, because there is no code to paste", async () => {
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    await screen.findByRole("button", { name: "Connect Dropbox" });
    expect(screen.queryByLabelText(/Authorisation code/i)).toBeNull();
  });

  it("keeps the paste flow one click away when the redirect never arrives", async () => {
    const user = userEvent.setup();
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    // UI convention 3: the remedy for a redirect that did not happen — a
    // mistyped redirect URI in the Dropbox console, a blocked navigation — is
    // named before it is needed rather than left as a dead end.
    await user.click(await screen.findByTestId("use-paste-flow"));

    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    await waitFor(() => expect(connectionsState().connections).toHaveLength(1));
    // Started with no redirect URI, so Dropbox showed a code: the fallback is
    // the whole paste flow, not a differently-worded redirect.
    expect(connectionsState().authorizationRedirectUri).toBeNull();
    expect(navigated).not.toHaveBeenCalled();
  });
});

describe("connecting where Dropbox will not redirect back", () => {
  it("says why, and connects by paste instead", async () => {
    const user = userEvent.setup();
    // arc on the LAN over plain http: a normal way to run a self-hosted
    // application, and the one deployment Dropbox refuses to redirect to.
    atOrigin("http://192.168.1.50");
    renderStep(<DropboxConnectStep onConnected={() => {}} />);

    expect(
      await screen.findByText(/only redirects to https addresses/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Connect Dropbox" }));
    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));

    await waitFor(() => expect(connectionsState().connections).toHaveLength(1));
    expect(connectionsState().authorizationRedirectUri).toBeNull();
    expect(navigated).not.toHaveBeenCalled();
  });
});

describe("the confirmation a connect ends on", () => {
  /** Connect through the paste flow, and stop on whatever it renders next. */
  async function connectByPaste(onConnected: () => void) {
    const user = userEvent.setup();
    renderStep(<DropboxConnectStep onConnected={onConnected} />);

    await user.click(await screen.findByTestId("use-paste-flow"));
    await user.type(
      await screen.findByLabelText(/Authorisation code/i),
      DROPBOX_CODE,
    );
    await user.click(screen.getByRole("button", { name: "Finish connecting" }));
    return user;
  }

  it("names the account before the flow is allowed to move on", async () => {
    const onConnected = vi.fn();

    const user = await connectByPaste(onConnected);

    // Success is stated, not implied by a screen vanishing. Every other step
    // of this flow confirms itself; the one that leaves the application and
    // comes back used to confirm itself by disappearing.
    const confirmation = await screen.findByTestId("connect-confirmation");
    expect(confirmation).toHaveTextContent(
      "Connected as Ada Lovelace (ada@example.com)",
    );
    expect(onConnected).not.toHaveBeenCalled();

    await user.click(
      within(confirmation).getByRole("button", { name: /Choose the folder/i }),
    );
    expect(onConnected).toHaveBeenCalledTimes(1);
  });

  it("says arc has read the Dropbox it just connected", async () => {
    await connectByPaste(() => {});

    // The connection is proven, not merely stored: the server listed the
    // athlete's Dropbox with the credential before writing the row, and the
    // confirmation is where that becomes something they were told.
    expect(await screen.findByTestId("connect-confirmation")).toHaveTextContent(
      /read your Dropbox/i,
    );
  });

  it("says what is still owed when Dropbox could not answer the check", async () => {
    // A 429 or a dead socket during the connect: the authorization code is
    // spent either way, so the server stores the connection unproven rather
    // than sending the athlete back to dropbox.com — and says so.
    connectionsState().verificationNote = DROPBOX_VERIFICATION_DEFERRED;

    await connectByPaste(() => {});

    const confirmation = await screen.findByTestId("connect-confirmation");
    expect(confirmation).toHaveTextContent(
      /first time it looks for new rides/i,
    );
    // Not both sentences at once: the connection either has been read or has
    // not, and claiming the check passed beside a note saying it did not run
    // is worse than saying nothing.
    expect(confirmation).not.toHaveTextContent(/and it does/i);
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
    // Through the paste flow, because that is the one that renders the link
    // on screen instead of following it — the key in force is the same.
    await user.click(screen.getByTestId("use-paste-flow"));

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
