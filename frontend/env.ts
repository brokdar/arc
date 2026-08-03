import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

/**
 * Type-safe, validated environment. Misconfiguration fails the build instead
 * of surfacing at runtime. NEXT_PUBLIC_* values are baked in at build time.
 */
export const env = createEnv({
  client: {
    NEXT_PUBLIC_API_BASE_URL: z.url().default("http://localhost:8000"),
    NEXT_PUBLIC_API_PATH: z.string().default("/api/v1"),
  },
  server: {},
  runtimeEnv: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
    NEXT_PUBLIC_API_PATH: process.env.NEXT_PUBLIC_API_PATH,
  },
  emptyStringAsUndefined: true,
});
