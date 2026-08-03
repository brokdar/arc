import { expect, test } from "@playwright/test";

test("home page renders and links to the items example", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "View items example" }),
  ).toBeVisible();
});
