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
  http.get("/api/v1/items", ({ response }) =>
    response(200).json({
      items: [
        {
          id: "0198c5b6-0000-7000-8000-000000000001",
          name: "First item",
          description: "from msw",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      offset: 0,
      limit: 50,
    }),
  ),
];
