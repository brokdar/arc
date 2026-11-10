import { type BrowserContext, expect, test } from "@playwright/test";

/**
 * The 768 px switch between the sidebar and the bottom bar, measured with the
 * real stylesheet — the half of AC-14 jsdom cannot see
 * (`components/shell/bottom-nav.test.tsx` holds the rest).
 */

async function fakeApi(context: BrowserContext) {
  await context.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );
  await context.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await context.route("**/api/v1/clock", (route) =>
    route.fulfill({ json: { timezone: "UTC", today: "2026-11-10" } }),
  );
}

test.describe("at 375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the bottom bar shows four tabs with Today marked, and no sidebar", async ({
    page,
    context,
  }) => {
    await fakeApi(context);
    await page.goto("/today");

    const bar = page.getByRole("navigation", { name: "Sections (phone)" });
    await expect(bar).toBeVisible();
    await expect(bar.getByRole("link")).toHaveText([
      "Today",
      "Calendar",
      "Sessions",
      "Settings",
    ]);
    await expect(bar.getByRole("link", { name: "Today" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(
      page.getByRole("navigation", { name: "Sections", exact: true }),
    ).toBeHidden();

    for (const link of await bar.getByRole("link").all()) {
      const box = await link.boundingBox();
      expect(box?.height).toBeGreaterThanOrEqual(44);
      expect(box?.width).toBeGreaterThanOrEqual(44);
    }
  });
});

test.describe("at 1280 px", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("the sidebar shows and the bottom bar does not", async ({
    page,
    context,
  }) => {
    await fakeApi(context);
    await page.goto("/today");

    await expect(
      page.getByRole("navigation", { name: "Sections", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Sections (phone)" }),
    ).toBeHidden();
  });
});
