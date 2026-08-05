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
