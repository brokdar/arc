import type { NextConfig } from "next";

// Validate environment at build time — a bad deploy fails here, not at runtime.
import "./env";

// Type-checking uses tsgo (TypeScript native) via `bun run type-check`; the
// workspace `typescript` stays on 5.x because openapi-typescript and Next's
// compiler-API integration require the JS API that TS 7 dropped.
const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker runtime image.
  output: "standalone",
};

export default nextConfig;
