import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";

/**
 * Typed MSW handlers: paths, params, and response bodies are all inferred
 * from the generated OpenAPI types, so a backend contract change that isn't
 * reflected here becomes a type-check failure instead of silent mock drift.
 */
export const http = createOpenApiHttp<paths>({
  baseUrl: "http://localhost:8000",
});

/** Default happy-path handlers. Override per-test with server.use(...). */
export const handlers = [
  // Authenticated by default, so component tests don't each have to log in.
  http.get("/api/v1/auth/session", ({ response }) =>
    response(200).json({ authenticated: true }),
  ),
  http.post("/api/v1/auth/login", ({ response }) => response(204).empty()),
  http.get("/api/v1/athlete", ({ response }) =>
    response(200).json({
      name: "Alex Rider",
      date_of_birth: "1990-06-15",
      sex: "male",
      height_cm: 181.5,
      capabilities: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }),
  ),
];
