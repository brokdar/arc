import { expect, test } from "@playwright/test";

/**
 * Full-stack smoke tests (@fullstack) — run against the real compose stack
 * via `just smoke`. They verify system WIRING (CORS, env baking, Docker
 * networking, migrations-on-boot), not business logic. If one of these fails
 * for a reason a cheaper layer could catch, add the lower-layer test instead
 * of growing this suite. Keep it under ~5 tests.
 *
 * URLs are relative: both the UI and the API are reached through the Caddy
 * reverse proxy at the config's baseURL (http://localhost), which is exactly
 * the same-origin path the browser takes.
 *
 * Every test here starts authenticated: the `setup` project (e2e/auth.setup.ts)
 * logs in once and its storageState is applied to both the browser context
 * and the `request` fixture, so API calls carry the session cookie too.
 */

test("the app shell renders behind the session cookie @fullstack", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "arc" })).toBeVisible();
});

test("the athlete profile round-trips through the proxy @fullstack", async ({
  request,
}) => {
  // Also proves migrations ran on boot: the profile is bootstrapped into a
  // table that only exists if `alembic upgrade head` succeeded.
  const created = await request.get("/api/v1/athlete");
  expect(created.status()).toBe(200);

  const name = `smoke-${Date.now()}`;
  const updated = await request.patch("/api/v1/athlete", { data: { name } });
  expect(updated.status()).toBe(200);
  expect((await updated.json()).name).toBe(name);
});

test("anchors and zones answer through the proxy @fullstack", async ({
  request,
}) => {
  // WARNING: this append is PERMANENT — anchor history is append-only by
  // design, so there is no cleanup. Fine against CI's throwaway compose
  // stack; but `E2E_PASSWORD=... just smoke` against a stack sharing a real
  // athlete's database volume will leave this 250 W estimated FTP in the
  // history and may change which version is "current".
  const appended = await request.post("/api/v1/anchors", {
    data: { anchor_type: "ftp", value: 250, provenance: "estimated" },
  });
  expect(appended.status()).toBe(201);

  const zones = await request.get("/api/v1/zones?anchor_type=ftp");
  expect(zones.status()).toBe(200);
  expect((await zones.json()).zones).toHaveLength(7);
});
