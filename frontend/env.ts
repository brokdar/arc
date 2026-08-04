import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

/**
 * Type-safe, validated environment. Misconfiguration fails the build instead
 * of surfacing at runtime. NEXT_PUBLIC_* values are baked in at build time.
 *
 * `emptyStringAsUndefined` is deliberately off: an empty
 * NEXT_PUBLIC_API_BASE_URL is *meaningful* — it means "same origin", i.e. the
 * browser calls /api/... on whatever host served the page and the Caddy
 * reverse proxy forwards it to the API (that is how the Docker stack builds
 * the frontend). With the option on, t3-env strips empty values before
 * validation and the schema default would silently resurrect
 * http://localhost:8000. A variable where empty really does mean "unset" has
 * to map empty to undefined itself, in `runtimeEnv` below.
 */
export const env = createEnv({
  client: {
    // "" = same origin; an absolute URL is for running without the proxy
    // (bare-metal `bun dev` against a local API), which is also the default.
    NEXT_PUBLIC_API_BASE_URL: z
      .union([z.literal(""), z.url()])
      .default("http://localhost:8000"),
  },
  server: {},
  runtimeEnv: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  },
  emptyStringAsUndefined: false,
});
