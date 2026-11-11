import { type BrowserContext, expect, type Page, test } from "@playwright/test";

/**
 * The outbox in a real browser: IndexedDB, the `online` event, the service
 * worker's offline reload, and the badge's 60-second rule (AC-44).
 *
 * UI-only, against the production build. The API is faked on the context
 * (as in `offline-shell.spec.ts`), and `/health` and `/sync` additionally
 * refuse while the test's network is "down", so going offline is both what
 * the browser reports (`setOffline`) and what the server can be reached on.
 * The fake `/sync` answers like the server: every mutation it is sent is
 * applied, in order, and answered `applied`.
 *
 * No screen logs a set yet (the logging screen is the next pull request), so
 * the sets are written into the outbox's IndexedDB store exactly as
 * `lib/offline/db.ts` writes them: `{ mutation, queuedAt }` in `outbox`.
 */

const ATHLETE_ZONE = "Pacific/Kiritimati";

interface FakeServer {
  online: boolean;
  received: string[];
}

async function fakeApi(context: BrowserContext): Promise<FakeServer> {
  const server: FakeServer = { online: true, received: [] };
  await context.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );
  await context.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await context.route("**/api/v1/clock", (route) =>
    route.fulfill({
      json: {
        timezone: ATHLETE_ZONE,
        today: new Intl.DateTimeFormat("en-CA", {
          timeZone: ATHLETE_ZONE,
        }).format(new Date()),
      },
    }),
  );
  await context.route("**/health", (route) =>
    server.online
      ? route.fulfill({ json: { status: "ok" } })
      : route.abort("internetdisconnected"),
  );
  await context.route("**/api/v1/sync", async (route) => {
    if (!server.online) return route.abort("internetdisconnected");
    const body = route.request().postDataJSON() as {
      mutations: { mutation_id: string }[];
    };
    server.received.push(...body.mutations.map((m) => m.mutation_id));
    return route.fulfill({
      json: {
        schema_version: 1,
        results: body.mutations.map((m) => ({
          mutation_id: m.mutation_id,
          status: "applied",
        })),
      },
    });
  });
  return server;
}

/** Wait until the worker controls the page and Today can reload offline. */
async function waitUntilOfflineReady(page: Page) {
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          if (!navigator.serviceWorker.controller) return false;
          const shell = (await caches.keys()).find((name) =>
            name.startsWith("arc-shell-"),
          );
          return shell
            ? Boolean(await (await caches.open(shell)).match("/today"))
            : false;
        }),
      { timeout: 15_000 },
    )
    .toBe(true);
  // The badge opens the outbox database on mount.
  await expect
    .poll(() =>
      page.evaluate(async () =>
        (await indexedDB.databases()).some((db) => db.name === "arc-offline"),
      ),
    )
    .toBe(true);
}

async function goOffline(context: BrowserContext, server: FakeServer) {
  server.online = false;
  await context.setOffline(true);
}

async function goOnline(context: BrowserContext, server: FakeServer) {
  server.online = true;
  await context.setOffline(false);
}

/** A session start and three logged sets, as the logging screen queues them. */
function workout(now: number) {
  const id = (n: number) =>
    `0199a1b2-0000-7000-8000-${String(now).slice(-8)}${String(n).padStart(4, "0")}`;
  const session = id(9000);
  const at = new Date(now).toISOString();
  return [
    {
      kind: "session_start",
      mutation_id: id(1),
      client_edit_time: at,
      session_client_id: session,
      start_time: at,
    },
    ...[0, 1, 2].map((index) => ({
      kind: "set_upsert",
      mutation_id: id(10 + index),
      client_edit_time: at,
      session_client_id: session,
      set_client_id: id(100 + index),
      set: {
        exercise_id: "back_squat",
        reps: 5,
        load_kg: 100,
        set_index: index,
      },
    })),
  ];
}

async function queue(page: Page, mutations: unknown[]) {
  await page.evaluate(
    (items) =>
      new Promise<void>((resolve, reject) => {
        const open = indexedDB.open("arc-offline");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result;
          const tx = db.transaction("outbox", "readwrite");
          for (const mutation of items) {
            tx.objectStore("outbox").add({ mutation, queuedAt: Date.now() });
          }
          tx.oncomplete = () => {
            db.close();
            resolve();
          };
          tx.onerror = () => reject(tx.error);
        };
      }),
    mutations,
  );
}

/** The mutation ids waiting in IndexedDB, in queue order. */
async function stored(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      new Promise<string[]>((resolve, reject) => {
        const open = indexedDB.open("arc-offline");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result;
          const request = db
            .transaction("outbox", "readonly")
            .objectStore("outbox")
            .getAll();
          request.onsuccess = () => {
            db.close();
            resolve(
              request.result.map(
                (item: { mutation: { mutation_id: string } }) =>
                  item.mutation.mutation_id,
              ),
            );
          };
          request.onerror = () => reject(request.error);
        };
      }),
  );
}

test("sets logged offline stay queued, then drain in order when online", async ({
  page,
  context,
}) => {
  await page.clock.install();
  const server = await fakeApi(context);
  await page.goto("/today");
  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();
  await waitUntilOfflineReady(page);

  await goOffline(context, server);
  const items = workout(Date.now());
  await queue(page, items);

  // Younger than a minute: queued, and no badge — past the badge's own
  // five-second re-read and a flush that found no network.
  await page.clock.fastForward(30_000);
  expect(await stored(page)).toEqual(items.map((m) => m.mutation_id));
  await expect(page.getByTestId("outbox-badge")).toHaveCount(0);

  // Past a minute the badge says what is waiting.
  await page.clock.fastForward(31_000);
  await expect(page.getByTestId("outbox-badge")).toHaveText(
    "4 waiting to upload",
  );
  expect(server.received).toEqual([]);

  await goOnline(context, server);

  await expect(page.getByTestId("outbox-badge")).toHaveCount(0);
  expect(server.received).toEqual(items.map((m) => m.mutation_id));
  expect(await stored(page)).toEqual([]);
});

test("a reload while offline keeps the queue", async ({ page, context }) => {
  const server = await fakeApi(context);
  await page.goto("/today");
  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();
  await waitUntilOfflineReady(page);

  await goOffline(context, server);
  const items = workout(Date.now());
  await queue(page, items);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();

  expect(await stored(page)).toEqual(items.map((m) => m.mutation_id));
  await expect(page.getByTestId("outbox-badge")).toHaveCount(0);

  await goOnline(context, server);
  await expect.poll(() => stored(page)).toEqual([]);
  expect(server.received).toEqual(items.map((m) => m.mutation_id));
});
