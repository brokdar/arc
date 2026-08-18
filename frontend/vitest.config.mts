import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    // The runner's own clock, pinned deliberately far from the athlete's.
    //
    // `Pacific/Midway` is UTC-11 all year; the fake backend serves
    // `Pacific/Kiritimati`, UTC+14 all year (`tests/mocks/fixtures.ts`).
    // Twenty-five hours apart with no DST on either side, so the browser's
    // calendar date and the athlete's are never the same day. Unpinned, the
    // runner ran in UTC and every test that keyed off "today" passed because
    // the fixture and the component shared one wrong clock — they could not
    // detect issue #62 by construction. Now they cannot miss it.
    env: { TZ: "Pacific/Midway" },
    // Comfortably above the 5 s `asyncUtilTimeout` set in `vitest.setup.ts`,
    // so a slow render surfaces as Testing Library's "unable to find" error —
    // which names the query and prints the DOM — rather than a bare timeout
    // that says only that the test ran too long.
    testTimeout: 20_000,
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      include: ["app/**", "components/**", "lib/**"],
      // components/ui is vendored shadcn code; generated/ is machine-written.
      exclude: ["components/ui/**", "**/*.test.*"],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 70,
        statements: 80,
      },
    },
  },
});
