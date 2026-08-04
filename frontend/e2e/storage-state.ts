/**
 * Where the fullstack `setup` project parks the logged-in session.
 *
 * It lives in its own module because `playwright.config.ts` needs the path
 * too, and importing the setup spec from the config would call `test()` at
 * config-load time (which Playwright rejects).
 *
 * Relative to the Playwright cwd (frontend/); `e2e/.auth/` is gitignored.
 */
export const STORAGE_STATE = "e2e/.auth/state.json";
