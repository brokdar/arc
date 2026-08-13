import { expect, test as setup } from "@playwright/test";

import { STORAGE_STATE } from "./storage-state";

/**
 * Fullstack-only setup project: log in through the real UI once and hand the
 * session cookie to every other project via storageState. Going through the
 * form (rather than posting to /auth/login) also proves the login page itself
 * is wired to the API.
 */

setup("authenticate @fullstack", async ({ page }) => {
  await page.goto("/login");
  await page
    .getByLabel("Password")
    .fill(process.env.E2E_PASSWORD ?? "ci-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  // Landing on the calendar (the app's home) means the API accepted
  // the password and the browser is now holding a valid session cookie.
  await expect(page.getByRole("heading", { name: "Calendar" })).toBeVisible();

  await page.context().storageState({ path: STORAGE_STATE });
});
