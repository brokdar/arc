import { expect, test } from "@playwright/test";

/**
 * UI-only smoke tests: a production build with NO backend behind it. Assert
 * only on what is deterministic without an API — chiefly that the shells
 * render and the app degrades instead of crashing.
 */

test("the login page renders its form", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("a guarded page degrades gracefully with no API", async ({ page }) => {
  await page.goto("/");

  // With the API absent the guard either waits on the session query or gives
  // up on it — both are acceptable; a blank page or a crash is not.
  await expect(
    page
      .getByText("Loading…")
      .or(page.getByText("Could not verify your session.")),
  ).toBeVisible();
});

/**
 * Behind Caddy, the service worker is served from the origin root and never
 * from an HTTP cache — the proxy's half of the offline shell, which only the
 * real stack can show (Next sets the same headers; `caddy/Caddyfile` repeats
 * them after the proxy answers). Tagged @fullstack, so it runs only in
 * `just smoke`, against http://localhost through Caddy.
 */
test("the service worker is served from / uncached behind Caddy @fullstack", async ({
  request,
}) => {
  const response = await request.get("/sw.js");

  expect(response.status()).toBe(200);
  expect(new URL(response.url()).pathname).toBe("/sw.js");
  expect(response.headers()["cache-control"]).toBe("no-cache");
  expect(response.headers()["service-worker-allowed"]).toBe("/");
  expect(response.headers()["content-type"]).toMatch(/javascript/);
  expect(await response.text()).toContain('addEventListener("fetch"');
});
