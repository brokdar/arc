import { expect, test } from "@playwright/test";

/**
 * Full-stack smoke tests (@fullstack) — run against the real compose stack
 * via `just smoke`. They verify system WIRING (CORS, env baking, Docker
 * networking, migrations-on-boot), not business logic. If one of these fails
 * for a reason a cheaper layer could catch, add the lower-layer test instead
 * of growing this suite. Keep it under ~5 tests.
 */

const API = "http://localhost:8000";

test("items page talks to the real API @fullstack", async ({ page }) => {
  await page.goto("/items");

  await expect(page.getByRole("heading", { name: "Items" })).toBeVisible();
  // Any successful render (list or empty state) proves the round-trip;
  // the error state means broken wiring.
  await expect(page.getByText("Failed to load items.")).not.toBeVisible();
});

test("item created via API appears in the UI @fullstack", async ({
  page,
  request,
}) => {
  const name = `smoke-item-${Date.now()}`;
  const created = await request.post(`${API}/api/v1/items`, {
    data: { name, description: "created by the smoke test" },
  });
  expect(created.status()).toBe(201);
  const { id } = await created.json();

  try {
    await page.goto("/items");
    await expect(page.getByText(name)).toBeVisible();
  } finally {
    await request.delete(`${API}/api/v1/items/${id}`);
  }
});
