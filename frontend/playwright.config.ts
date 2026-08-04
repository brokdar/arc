import { defineConfig, devices } from "@playwright/test";

import { STORAGE_STATE } from "./e2e/storage-state";

const isCI = !!process.env.CI;

/**
 * Two modes:
 * - default: UI-only e2e against `bun run start`, no backend required
 *   (tests tagged @fullstack are excluded)
 * - E2E_FULLSTACK=1: runs ONLY @fullstack tests against an already-running
 *   full stack (docker compose up), entered through the Caddy reverse proxy
 *   on :80 — same origin for both the UI and /api/*.
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
    baseURL: fullstack ? "http://localhost" : "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },
  // Fullstack mode logs in once in a setup project and replays the session
  // cookie into every test. UI-only mode has no API to log into, so it keeps
  // the plain single-project layout.
  projects: fullstack
    ? [
        { name: "setup", testMatch: /auth\.setup\.ts/ },
        {
          name: "chromium",
          use: { ...devices["Desktop Chrome"], storageState: STORAGE_STATE },
          dependencies: ["setup"],
        },
      ]
    : [
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
