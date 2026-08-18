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

/**
 * The port the UI-only suite serves its own production build on.
 *
 * Overridable because the port is **not** per-worktree, and this repository
 * expects several checkouts at once (see the Worktrees section of
 * `CLAUDE.md`). A `bun run start` left running in another tree owns :3000,
 * and reusing it silently pointed the whole suite at *that* build.
 */
const port = process.env.E2E_PORT ?? "3000";

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
    baseURL: fullstack ? "http://localhost" : `http://localhost:${port}`,
    // The *browser's* zone, pinned deliberately away from the athlete's.
    //
    // `Pacific/Midway` is UTC-11 all year. The UI-only fake API serves the
    // athlete's zone from `/clock` and every spec's fixtures are built on it,
    // so a component that computed "today" from the browser instead would
    // disagree on every run. Unpinned, the browser ran in the runner's zone
    // and the two silently agreed (issue #62). In fullstack mode the real
    // backend serves `MATCHING__TIMEZONE`, and the same pin keeps the browser
    // from being the thing that happens to match it.
    timezoneId: "Pacific/Midway",
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
        command: `bun run start --port ${port}`,
        url: `http://localhost:${port}`,
        // Never reuse. A server already on this port is somebody else's — a
        // dev server, or `bun run start` in another worktree — and attaching
        // to it tests a build that is not the one just made. That failure is
        // invisible: the suite runs, and its result describes other code.
        // Playwright's "port is already used" is the honest answer, and
        // `E2E_PORT` is the way past it.
        reuseExistingServer: false,
        timeout: 60_000,
      },
});
