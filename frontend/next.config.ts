import type { NextConfig } from "next";

// Validate environment at build time — a bad deploy fails here, not at runtime.
import "./env";

/**
 * One version per build, shared by every process the build spawns.
 *
 * The service worker is registered as `/sw.js?v=<version>`
 * (`components/shell/sw-register.tsx`); a new version is a new script URL, so
 * the next online load after a deploy installs the new worker and drops the
 * old build's cache (`public/sw.js`). Written back into `process.env` so the
 * workers `next build` forks inherit this value instead of each taking their
 * own timestamp, and inlined into the client as `NEXT_PUBLIC_SW_VERSION`. A
 * caller may pin it (a reproducible image) by setting the variable itself.
 */
process.env.NEXT_PUBLIC_SW_VERSION ||= Date.now().toString(36);

// Type-checking uses tsgo (TypeScript native) via `bun run type-check`; the
// workspace `typescript` stays on 5.x because openapi-typescript and Next's
// compiler-API integration require the JS API that TS 7 dropped.
const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker runtime image.
  output: "standalone",
  env: {
    NEXT_PUBLIC_SW_VERSION: process.env.NEXT_PUBLIC_SW_VERSION,
  },
  async headers() {
    return [
      {
        // The worker must never be served from an HTTP cache, or a deploy
        // would not reach a phone until that cache expired. Caddy repeats
        // both headers in front of this (`caddy/Caddyfile`), so a proxy
        // change cannot silently drop them.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache" },
          { key: "Service-Worker-Allowed", value: "/" },
          {
            key: "Content-Type",
            value: "application/javascript; charset=utf-8",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
