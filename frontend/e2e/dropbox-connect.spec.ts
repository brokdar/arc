import { expect, type Page, test } from "@playwright/test";

/**
 * AC-26: the athlete comes back from Dropbox instead of copying a code.
 *
 * This is the one flow in arc that leaves the application and returns, so it
 * is the one flow a component test cannot describe: the assertion is about a
 * real navigation to dropbox.com, a real redirect back to a route arc serves,
 * and a page that has to resolve into something the athlete can act on no
 * matter which of the four ways it was opened.
 *
 * UI-only, like the rest of this folder: there is no backend behind the
 * production build, so the API is a small stateful fake installed with
 * `page.route`, and dropbox.com is a route that answers the redirect Dropbox
 * would. The fake holds the two facts the flow turns on — a `state` is minted
 * per authorization and only that value completes it, and a completed
 * connection is one the folder step can then be reached through.
 */

const CONNECTION_ID = "0199b000-0000-7000-8000-0000000000c1";
const WAHOO_PATH = "/apps/wahoofitness";
const WAHOO_PATH_DISPLAY = "/Apps/WahooFitness";

/** The fake Dropbox the picker browses, keyed by the path arc stores. */
const DROPBOX: Record<
  string,
  {
    path_display: string;
    items: { path_lower: string; path_display: string; name: string }[];
    file_count: number;
    supported_file_count: number;
  }
> = {
  "": {
    path_display: "",
    items: [{ path_lower: "/apps", path_display: "/Apps", name: "Apps" }],
    file_count: 1,
    supported_file_count: 0,
  },
  "/apps": {
    path_display: "/Apps",
    items: [
      {
        path_lower: WAHOO_PATH,
        path_display: WAHOO_PATH_DISPLAY,
        name: "WahooFitness",
      },
    ],
    file_count: 0,
    supported_file_count: 0,
  },
  [WAHOO_PATH]: {
    path_display: WAHOO_PATH_DISPLAY,
    items: [],
    file_count: 4,
    supported_file_count: 3,
  },
};
const TIMEZONE = "Pacific/Kiritimati";
const ISO_TODAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIMEZONE,
}).format(new Date());

interface FakeState {
  /** The nonce minted by the last authorize call, if it asked for a redirect. */
  state: string | null;
  /** The redirect URI the last authorize call was given. */
  redirectUri: string | null;
  connected: boolean;
}

/** Install the fake API and the fake Dropbox. Returns the state to assert on. */
async function mockApi(page: Page): Promise<FakeState> {
  const state: FakeState = {
    state: null,
    redirectUri: null,
    connected: false,
  };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  const connection = () => ({
    id: CONNECTION_ID,
    provider: "dropbox",
    status: "connected",
    account_label: "Ada Lovelace (ada@example.com)",
    scopes: ["account_info.read", "files.content.read", "files.metadata.read"],
    last_error: null,
    created_at: "2026-08-16T09:30:00Z",
    updated_at: "2026-08-16T09:30:00Z",
    feeds: [],
  });

  const catalogue = () => ({
    items: [
      {
        kind: "local_drop",
        display_name: "Local drop",
        data_kinds: ["recordings"],
        addable: false,
        transports: [
          { kind: "local_folder", storage: null, default_path: null },
        ],
      },
      {
        kind: "wahoo",
        display_name: "Wahoo",
        data_kinds: ["recordings"],
        addable: true,
        transports: [
          {
            kind: "cloud_folder",
            storage: "dropbox",
            default_path: WAHOO_PATH,
          },
        ],
      },
    ],
    storage: [
      {
        provider: "dropbox",
        app_configured: true,
        connection_id: state.connected ? CONNECTION_ID : null,
        account_label: state.connected
          ? "Ada Lovelace (ada@example.com)"
          : null,
        status: state.connected ? "connected" : null,
      },
    ],
  });

  const localDrop = () => ({
    id: "local_drop",
    kind: "local_drop",
    display_name: "Local drop",
    data_kinds: ["recordings"],
    transport: "local_folder",
    storage: null,
    removable: false,
    prompt: null,
    local: { inbox_path: "/data/inbox", scan_interval_seconds: 300 },
    folders: [],
  });

  await page.route("https://www.dropbox.com/oauth2/authorize*", (route) => {
    // Dropbox's half of the round trip: it hands the browser straight back to
    // the redirect URI it was given, with the code and the nonce arc minted.
    const asked = new URL(route.request().url());
    const back = asked.searchParams.get("redirect_uri");
    const nonce = asked.searchParams.get("state");
    return route.fulfill({
      status: 302,
      headers: {
        location: `${back}?code=the-code-dropbox-issued&state=${nonce}`,
      },
    });
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path.endsWith("/auth/session")) {
      return route.fulfill(json({ authenticated: true }));
    }
    if (path.endsWith("/clock")) {
      return route.fulfill(json({ timezone: TIMEZONE, today: ISO_TODAY }));
    }
    if (path.endsWith("/athlete")) {
      return route.fulfill(
        json({
          name: "Alex Rider",
          date_of_birth: null,
          sex: "male",
          height_cm: null,
          capabilities: {},
          plan_state: "active",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      );
    }
    if (path.endsWith("/connections/dropbox/setup")) {
      return route.fulfill(json({ app_key_set: true, source: "stored" }));
    }
    if (path.endsWith("/connections/dropbox/authorize") && method === "POST") {
      const body = (request.postDataJSON() ?? {}) as { redirect_uri?: string };
      state.redirectUri = body.redirect_uri ?? null;
      state.state =
        body.redirect_uri === undefined ? null : "a-nonce-arc-minted-here";
      const query = new URLSearchParams({
        client_id: "an-app-key",
        response_type: "code",
        token_access_type: "offline",
        code_challenge: "fake-challenge",
        code_challenge_method: "S256",
        scope: "account_info.read files.content.read files.metadata.read",
      });
      if (body.redirect_uri !== undefined) {
        query.set("redirect_uri", body.redirect_uri);
        query.set("state", state.state as string);
      }
      return route.fulfill(
        json({
          authorize_url: `https://www.dropbox.com/oauth2/authorize?${query}`,
          expires_at: "2026-08-16T09:45:00Z",
        }),
      );
    }
    if (path.endsWith("/connections/dropbox/complete") && method === "POST") {
      const body = request.postDataJSON() as { code: string; state?: string };
      if ((body.state ?? null) !== state.state) {
        return route.fulfill(
          json(
            {
              detail:
                "arc could not verify that connection came back from the " +
                "link it sent you, so it has been stopped. Start the " +
                "connection again.",
            },
            422,
          ),
        );
      }
      state.connected = true;
      state.state = null;
      // `verification_note` is null: the server proved the credential by
      // listing the athlete's Dropbox before it stored anything, which is the
      // ordinary answer and the one the confirmation is written for.
      return route.fulfill(
        json({ ...connection(), verification_note: null }, 201),
      );
    }
    if (path.endsWith("/connections")) {
      return route.fulfill(
        json({ items: state.connected ? [connection()] : [] }),
      );
    }
    if (path.endsWith("/integration-catalogue")) {
      return route.fulfill(json(catalogue()));
    }
    if (path.endsWith("/discover")) {
      return route.fulfill(json({ proposals: [], access_type_suspect: null }));
    }
    if (path.endsWith("/folders")) {
      // The picker's data, in the shape the real endpoint answers with: the
      // athlete's own capitalisation for everything on screen, `path_lower`
      // for the identity a feed row stores, and the current folder's contents
      // as two counts it can actually back.
      const asked = new URL(request.url()).searchParams.get("path") ?? "";
      const here = DROPBOX[asked.toLowerCase()];
      return here === undefined
        ? route.fulfill(
            json({ detail: `Dropbox has no folder at ${asked}` }, 404),
          )
        : route.fulfill(json(here));
    }
    if (path.endsWith("/integrations/local-drop/settings")) {
      return route.fulfill(
        json({ scan_interval_seconds: 300, source: "environment" }),
      );
    }
    if (path.endsWith("/integrations")) {
      return route.fulfill(json({ items: [localDrop()] }));
    }
    if (path.endsWith("/anchors/current")) {
      // A fresh instance has no anchor in force, and the API says so with a
      // 404 rather than an empty object — a body of the wrong shape would
      // crash the panel above this one and take the page with it.
      return route.fulfill(json({ detail: "No FTP anchor is in force" }, 404));
    }
    if (path.endsWith("/anchors")) {
      return route.fulfill(json({ items: [], total: 0, offset: 0, limit: 50 }));
    }
    if (path.endsWith("/zones")) {
      return route.fulfill(
        json({ detail: "No anchor to derive zones from" }, 404),
      );
    }
    if (path.endsWith("/proposals")) {
      return route.fulfill(json({ items: [], total: 0, offset: 0, limit: 50 }));
    }
    return route.fulfill(json({ detail: "unmocked" }, 404));
  });

  return state;
}

test.describe("connecting Dropbox by redirect", () => {
  test("brings the athlete back and resumes at folder selection", async ({
    page,
  }) => {
    const state = await mockApi(page);

    await page.goto("/settings");
    await page.getByRole("button", { name: "Add an integration" }).click();
    await page.getByRole("button", { name: "Wahoo" }).click();
    await page.getByRole("button", { name: "Connect Dropbox" }).click();

    // Out to Dropbox and back through arc's own callback, with no code ever
    // shown to the athlete. Awaited before anything else: the tab starts on
    // `/settings` and ends there, so waiting for that URL alone would pass
    // without a round trip having happened at all.
    await page.waitForURL("**/settings/dropbox/callback?*");
    // The connect states its own success before anything else renders. This
    // is the whole point of coming back to a page rather than replacing
    // straight through to Settings: the athlete is told which account is
    // connected and that arc has read it.
    await expect(page.getByTestId("connect-confirmation")).toContainText(
      "Connected as Ada Lovelace (ada@example.com)",
    );
    await page.getByRole("button", { name: /Choose the folder/i }).click();
    // Resumed where it left off: the account is connected, so the only thing
    // still owed is which folder.
    const picker = page.getByTestId("folder-step");
    await expect(picker).toContainText(/Which folder holds your Wahoo files/i);

    // And the picker the athlete lands on speaks their Dropbox: the shortcut
    // takes them to the folder, the breadcrumb names it as Dropbox spells it,
    // and there is one action to take, about the folder they are standing in.
    await picker.getByRole("button", { name: "Go to Wahoo's folder" }).click();
    await expect(
      picker.getByRole("navigation", { name: "Folder path" }),
    ).toContainText("WahooFitness");
    await expect(picker).toContainText("4 files here, 3 arc can read.");
    await expect(picker.getByRole("button", { name: /watch/i })).toHaveCount(1);
    expect(await picker.textContent()).not.toContain(WAHOO_PATH);

    expect(new URL(page.url()).pathname).toBe("/settings");
    // The browser said where it was, and arc asked Dropbox to come back there.
    expect(state.redirectUri).toBe(
      `${new URL(page.url()).origin}/settings/dropbox/callback`,
    );
    expect(state.connected).toBe(true);
  });

  test("renders Dropbox's refusal and offers the flow again", async ({
    page,
  }) => {
    await mockApi(page);

    await page.goto("/settings/dropbox/callback?error=access_denied");

    // Not a blank page and not a spinner that never resolves: the athlete
    // said no, and the page says so and offers the way back in.
    await expect(
      page.getByTestId("dropbox-callback").getByRole("alert"),
    ).toContainText(/did not connect/i);
    await page.getByRole("button", { name: /Try connecting again/i }).click();

    await page.waitForURL("**/settings");
    await expect(page.getByTestId("add-integration-flow")).toBeVisible();
  });

  test("explains a callback opened with neither a code nor an error", async ({
    page,
  }) => {
    await mockApi(page);

    await page.goto("/settings/dropbox/callback");

    await expect(
      page.getByText(/Dropbox did not send anything back/i),
    ).toBeVisible();
    await page.getByRole("link", { name: /Back to Settings/i }).click();

    await page.waitForURL("**/settings");
    await expect(page.getByTestId("integrations-panel")).toBeVisible();
  });

  test("renders the server's refusal when completing fails", async ({
    page,
  }) => {
    const state = await mockApi(page);

    // A code that came back with a nonce arc never minted — the tab was
    // reopened out of history, or the link came from somewhere else.
    state.state = "the-nonce-arc-actually-minted";
    await page.goto(
      "/settings/dropbox/callback?code=the-code-dropbox-issued&state=a-different-nonce",
    );

    await expect(
      page.getByTestId("dropbox-callback").getByRole("alert"),
    ).toContainText(/could not verify that connection came back/i);
    expect(state.connected).toBe(false);
  });
});
