import { type BrowserContext, expect, type Page, test } from "@playwright/test";

/**
 * The installed app opens without a network.
 *
 * UI-only, against the production build (the service worker registers in
 * production builds only). The API is faked on the *context* rather than the
 * page, because Chromium routes a worker-controlled page's requests through
 * the context. Before going offline every fake is removed, so the offline
 * load proves the app opens with no server at all — not that a fake answered.
 */

const ATHLETE_ZONE = "Pacific/Kiritimati";

async function fakeApi(context: BrowserContext) {
  // Anything not faked below answers like a missing route, so a page section
  // shows its own error state instead of the test hanging on it.
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
}

/**
 * Wait until the worker controls the page and holds what a reload of it
 * needs: the page, the offline page, and every script, stylesheet and font
 * the document itself references. (Route chunks Next prefetches for *other*
 * pages are not required — those pages are not promised offline.)
 */
async function waitUntilCached(page: Page) {
  // Polled from the test, not `waitForFunction`: that one treats the promise
  // an async predicate returns as truthy and stops waiting at once.
  await expect
    .poll(() => page.evaluate(isCached), { timeout: 15_000 })
    .toBe(true);
}

/** Runs in the page; see `waitUntilCached`. */
async function isCached(): Promise<boolean> {
  if (!navigator.serviceWorker.controller) return false;
  const shell = (await caches.keys()).find((name) =>
    name.startsWith("arc-shell-"),
  );
  if (!shell) return false;
  const cache = await caches.open(shell);
  const referenced = [
    ...[...document.querySelectorAll("script[src]")].map(
      (node) => (node as HTMLScriptElement).src,
    ),
    ...[
      ...document.querySelectorAll(
        'link[rel="stylesheet"], link[rel="preload"][as="font"]',
      ),
    ].map((node) => (node as HTMLLinkElement).href),
  ]
    .map((href) => new URL(href))
    .filter((url) => url.origin === location.origin)
    .map((url) => url.pathname);
  for (const path of [location.pathname, "/offline", ...referenced]) {
    if (!(await cache.match(path))) return false;
  }
  return true;
}

/** The version the controlling worker was registered with, or null. */
async function workerVersion(page: Page): Promise<string | null> {
  return page.evaluate(
    () =>
      new Promise<string | null>((resolve) => {
        const controller = navigator.serviceWorker.controller;
        if (!controller) {
          resolve(null);
          return;
        }
        const channel = new MessageChannel();
        channel.port1.onmessage = (event) => resolve(event.data.version);
        controller.postMessage({ type: "version" }, [channel.port2]);
        setTimeout(() => resolve(null), 5_000);
      }),
  );
}

async function visitTodayOnline(page: Page, context: BrowserContext) {
  await fakeApi(context);
  await page.goto("/today");
  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();
  await waitUntilCached(page);
}

test("Today opens offline after one online visit", async ({
  page,
  context,
}) => {
  await visitTodayOnline(page, context);
  await expect(page.getByTestId("offline-ready")).toHaveText(
    /^Ready offline · synced \d\d:\d\d$/,
  );

  await context.unrouteAll({ behavior: "ignoreErrors" });
  await context.setOffline(true);
  await page.reload();

  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();
  await expect(page.getByTestId("offline-ready")).toHaveText(
    /^Ready offline · synced \d\d:\d\d$/,
  );
});

test("a route never opened online shows the app's offline page", async ({
  page,
  context,
}) => {
  await visitTodayOnline(page, context);

  await context.unrouteAll({ behavior: "ignoreErrors" });
  await context.setOffline(true);
  await page.goto("/fitness");

  // The app's own page, served by the worker — not the browser's
  // "No internet" error, which would make `goto` throw instead.
  await expect(
    page.getByRole("heading", { name: "You are offline" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /Open Today/ })).toHaveAttribute(
    "href",
    "/today",
  );
});

test("the next online load after a deploy activates the new worker", async ({
  page,
  context,
}) => {
  await visitTodayOnline(page, context);
  const current = await workerVersion(page);
  expect(current).toBeTruthy();

  // Put the phone on an earlier build's worker: the same script under the
  // version string a previous deploy would have registered.
  await page.evaluate(() =>
    navigator.serviceWorker.register("/sw.js?v=e2e-previous-build", {
      scope: "/",
      updateViaCache: "none",
    }),
  );
  // Installing precaches the shell first, so allow it more than the default.
  await expect
    .poll(() => workerVersion(page), { timeout: 15_000 })
    .toBe("e2e-previous-build");

  // The deploy: this build's page loads online and registers its own version.
  await page.reload();

  await expect
    .poll(() => workerVersion(page), { timeout: 15_000 })
    .toBe(current);
  // … and the earlier build's shell is gone, so nothing serves its pages.
  await expect
    .poll(() => page.evaluate(() => caches.keys()), { timeout: 15_000 })
    .toEqual([`arc-shell-${current}`]);
});
