import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";
import { actOnCard, showCards } from "@/tests/mocks/cards-state";
import { ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";

/** Its own typed `http`, like `handlers-power.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

/**
 * Suggestion cards, stateful: the list stamps what it shows, and an answer
 * echoes the state the request asked for — so a component that posted the
 * wrong action renders the wrong result instead of passing.
 */
export const cardHandlers = [
  http.get("/api/v1/cards", ({ response }) => response(200).json(showCards())),
  http.post(
    "/api/v1/cards/{card_id}/act",
    async ({ params, request, response }) => {
      const { action } = await request.json();
      const result = actOnCard(params.card_id, action, ATHLETE_TIMEZONE);
      return result.status === 200
        ? response(200).json(result.card)
        : response(result.status).json({ detail: result.detail });
    },
  ),
];
