import { defineConfig, devices } from "@playwright/test";

const isCI = !!process.env.CI;

/**
 * Two modes:
 * - default: UI-only e2e against `bun run start`, no backend required
 *   (tests tagged @fullstack are excluded)
 * - E2E_FULLSTACK=1: runs ONLY @fullstack tests against an already-running
 *   full stack (docker compose up) — frontend on :3000, API on :8000.
 *   Keep this suite tiny: it exists to verify wiring, not logic.
 */
const fullstack = !!process.env.E2E_FULLSTACK;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  grep: fullstack ? /@fullstack/ : undefined,
  grepInvert: fullstack ? undefined : /@fullstack/,
  // CI shards via --shard; see .github/workflows/frontend-e2e.yml.
  reporter: isCI ? [["blob"], ["github"]] : [["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: fullstack
    ? undefined
    : {
        // UI-only e2e runs against a production build. CI builds beforehand
        // and reuses it.
        command: "bun run start",
        url: "http://localhost:3000",
        reuseExistingServer: !isCI,
        timeout: 60_000,
      },
});
