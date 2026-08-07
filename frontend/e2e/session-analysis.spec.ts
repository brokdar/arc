import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, type Page, test } from "@playwright/test";

/**
 * The session analysis page, walked end to end against a production build.
 *
 * UI-only, like the rest of this folder: there is no backend behind the build,
 * so the API is a small fake installed with `page.route`. What it serves is
 * **not** hand-written — it is the same generated artefact the component tests
 * use (`tests/mocks/generated-metrics.ts`, produced by running the real domain
 * over a synthetic stream), so the numbers on the page agree with the stream
 * plotted beneath them.
 *
 * The point of doing it here as well as in components is the parts jsdom
 * cannot answer for: the page renders a real canvas, the metric row survives
 * a production build, and the recompute action round-trips.
 */

const SESSION_ID = "0199a000-0000-7000-8000-000000000101";

// Playwright transpiles these specs to CommonJS, so `import.meta.url` is not
// available; the path is resolved from the config's root instead.
const GENERATED = join(process.cwd(), "tests", "mocks", "generated-metrics.ts");

/**
 * Read the two generated constants out of the fixture module.
 *
 * Playwright's config does not run the app's TypeScript path aliases, so the
 * module is parsed rather than imported: it is generated JSON with a `const`
 * in front of it, and slicing at the first `{` is enough. Parsing rather than
 * re-declaring is the point — a copy would drift from the component tests,
 * and then two suites would disagree about the same ride.
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
      // Biome formats the generated module as TypeScript, so the object
      // literal has unquoted keys and trailing commas: `Function` turns it
      // back into a value without a parser dependency. The input is a file
      // this repository generates and commits, not anything a user supplies.
      return new Function(
        `return ${source.slice(open, index + 1)}`,
      )() as Record<string, unknown>;
    }
  }
  throw new Error(`${name} not found in ${GENERATED}`);
}

const METRICS = generated("RIDE_METRICS");
const STREAMS = generated("RIDE_STREAMS");

function session(metrics: Record<string, unknown> | null) {
  const load = (metrics?.load ?? {}) as Record<string, unknown>;
  return {
    id: SESSION_ID,
    local_date: "2026-08-05",
    start_time: "2026-08-05T05:14:00Z",
    end_time: "2026-08-05T05:34:00Z",
    timezone: "Europe/Zurich",
    discipline: "cycling",
    classification_source: "sport_field",
    discipline_overridden: false,
    recording_kind: "device",
    status: "unmatched",
    duration_s: 1140,
    recording_time_s: 1140,
    rpe: null,
    notes: null,
    load: metrics === null ? null : (load.training_load ?? null),
    load_basis: metrics === null ? null : (load.load_basis ?? null),
    metrics,
    recordings: [
      {
        id: STREAMS.recording_id,
        file_hash: "e3b0c44298fc1c14".repeat(4),
        file_sport_index: 0,
        original_ext: "fit",
        sport: "cycling",
        elapsed_time_s: 1200,
        recording_time_s: 1140,
        recording_stops: STREAMS.recording_stops,
        median_time_delta_s: 1,
        moving_time_s: 1140,
        power_source_candidates: ["Quarq DZero"],
        power_source: "Quarq DZero",
        power_source_rule: "only candidate",
        hr_source_candidates: ["Wahoo TICKR"],
        hr_source: "Wahoo TICKR",
        hr_source_rule: "only candidate",
        channels: ["power", "hr", "cadence", "speed", "elevation"],
        anomaly_count: 1,
        created_at: "2026-08-05T05:35:00Z",
      },
    ],
    logged_sets: [],
    created_at: "2026-08-05T05:35:00Z",
    updated_at: "2026-08-05T05:35:00Z",
  };
}

/** Install the fake API. `computed` decides whether an artefact exists yet. */
async function mockApi(page: Page, { computed }: { computed: boolean }) {
  const state = { metrics: computed ? METRICS : null };

  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await page.route(`**/api/v1/sessions/${SESSION_ID}/streams`, (route) =>
    route.fulfill({ json: STREAMS }),
  );
  await page.route(
    `**/api/v1/sessions/${SESSION_ID}/metrics/recompute`,
    (route) => {
      // The version chain is append-only: the fake bumps rather than replaces,
      // because "recompute wrote version 2" is the assertion below.
      state.metrics = {
        ...METRICS,
        version: ((METRICS.version as number) ?? 1) + 1,
        recompute_reason: "recomputed from the session page",
      };
      return route.fulfill({ json: state.metrics });
    },
  );
  await page.route(`**/api/v1/sessions/${SESSION_ID}`, (route) =>
    route.fulfill({ json: session(state.metrics) }),
  );
}

test("the analysis page shows the numbers and what they were computed from", async ({
  page,
}) => {
  await mockApi(page, { computed: true });
  await page.goto(`/sessions/${SESSION_ID}`);

  const header = page.getByLabel("Session metrics");
  await expect(header).toBeVisible();

  const power = METRICS.power as Record<string, Record<string, number>>;
  await expect(header).toContainText(
    String(Math.round(power.normalized_power.value)),
  );
  // A5.2's counterfactual: both models were computable, so the page says what
  // the other one would have given.
  await expect(page.getByText(/Had power been unavailable/)).toBeVisible();
  // D115: the pins are on the artefact, and the page names them.
  await expect(page.getByText(/ftp 262 W/)).toBeVisible();
});

test("the streams and the intervals render together", async ({ page }) => {
  await mockApi(page, { computed: true });
  await page.goto(`/sessions/${SESSION_ID}`);

  await expect(page.getByText("Streams")).toBeVisible();
  // uPlot draws into a real canvas here, which is the half jsdom cannot do.
  await expect(page.locator("canvas").first()).toBeVisible();

  const intervals = METRICS.intervals as unknown[];
  expect(intervals.length).toBeGreaterThan(0);
  const table = page.getByRole("table").first();
  await expect(table.getByRole("row")).toHaveCount(intervals.length + 1);
});

test("a session with no artefact offers the action that computes one", async ({
  page,
}) => {
  await mockApi(page, { computed: false });
  await page.goto(`/sessions/${SESSION_ID}`);

  await expect(page.getByText(/have not been computed/)).toBeVisible();
  await page.getByRole("button", { name: "Compute metrics" }).click();

  // Appends, never overwrites — and the page says so, because "recompute"
  // reads like "overwrite" to anyone coming from another platform. One
  // assertion, not two: the artefact it just wrote makes the page swap this
  // panel for the full analysis, so the message is gone a moment later.
  await expect(page.getByRole("status")).toContainText(
    "Wrote version 2. The previous version is still readable.",
  );

  // And the page moved on to the numbers.
  await expect(page.getByLabel("Session metrics")).toBeVisible();
});
