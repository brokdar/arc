import { HttpResponse } from "msw";
import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";
import { applyBatch, syncServer } from "@/tests/mocks/sync-state";

/** Its own typed `http`, like `handlers-cards.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

/**
 * The outbox's two endpoints. `/sync` applies what it is sent (stateful, see
 * `sync-state.ts`); `/health` answers while the fake network is up and fails
 * like a dropped connection when a test takes it down.
 */
export const syncHandlers = [
  http.get("/health", ({ response }) =>
    syncServer().healthy
      ? response(200).json({ status: "ok" })
      : response.untyped(HttpResponse.error()),
  ),
  http.post("/api/v1/sync", async ({ request, response }) => {
    if (!syncServer().healthy) return response.untyped(HttpResponse.error());
    const answer = applyBatch(await request.json());
    switch (answer.status) {
      case 200:
        return response(200).json(answer.body);
      case 401:
        return response.untyped(
          HttpResponse.json(answer.body, { status: 401 }),
        );
      case 409:
        return response(409).json(answer.body);
      case 422:
        return response(422).json(answer.body);
    }
  }),
];
