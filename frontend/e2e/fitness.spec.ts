import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, type Page, test } from "@playwright/test";

/**
 * The Fitness page against a production build, with the API faked.
 *
 * UI-only, like the rest of this folder. What the fake serves is the same
 * generated read the component tests use (`tests/mocks/generated-pmc.ts`,
 * produced by `just pmc-fixture` running the real domain over a stored
 * history), so the curve on the page is one the API could draw. The part only
 * a browser can answer for is that uPlot paints a real canvas in the built
 * page, and that the route is behind the same login as every other.
 */

// Playwright transpiles these specs to CommonJS, so `import.meta.url` is not
// available; the path is resolved from the config's root instead.
const GENERATED = join(process.cwd(), "tests", "mocks", "generated-pmc.ts");

/**
 * Read one generated constant out of the fixture module.
 *
 * Parsed rather than imported, for the reason `session-analysis.spec.ts`
 * gives: Playwright does not run the app's path aliases, and a re-declared
 * copy would drift from the component tests.
 */
function generated(name: string): Record<string, unknown> {
  const source = readFileSync(GENERATED, "utf8");
  const start = source.indexOf(`export const ${name}`);
  const open = source.indexOf("= {", start) + 2;
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) {
      // A file this repository generates and commits, not user input.
      return new Function(
        `return ${source.slice(open, index + 1)}`,
      )() as Record<string, unknown>;
    }
  }
  throw new Error(`${name} not found in ${GENERATED}`);
}

const TREND = generated("FITNESS_TREND");

async function mockApi(
  page: Page,
  { authenticated }: { authenticated: boolean },
) {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { authenticated } }),
  );
  // The athlete's one clock, 25 hours from the browser's (see
  // `session-analysis.spec.ts`).
  await page.route("**/api/v1/clock", (route) =>
    route.fulfill({
      json: {
        timezone: "Pacific/Kiritimati",
        today: new Intl.DateTimeFormat("en-CA", {
          timeZone: "Pacific/Kiritimati",
        }).format(new Date()),
      },
    }),
  );
  await page.route("**/api/v1/fitness**", (route) =>
    route.fulfill({ json: TREND }),
  );
}

test("the fitness page draws the chart under the display-only badge", async ({
  page,
}) => {
  await mockApi(page, { authenticated: true });
  await page.goto("/fitness");

  await expect(
    page.getByRole("heading", { name: "Fitness", level: 1 }),
  ).toBeVisible();
  await expect(page.getByRole("note", { name: "Display-only" })).toContainText(
    "arc will never change your plan because of this number",
  );
  const figure = page.getByRole("figure", {
    name: /Fitness, fatigue and form/,
  });
  await expect(figure).toBeVisible();
  // uPlot paints a real canvas here — the half jsdom cannot vouch for.
  await expect(figure.locator("canvas").first()).toBeVisible();
  await expect(
    page.getByText("Baseline maturing — day 40 of 97"),
  ).toBeVisible();
});

test("the fitness page still sends a signed-out visitor to log in", async ({
  page,
}) => {
  await mockApi(page, { authenticated: false });
  await page.goto("/fitness");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
