import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";
import {
  SESSION_BESTS,
  SESSION_BESTS_NONE,
  SQUAT_HISTORY,
} from "@/tests/mocks/generated-strength-records";

/** Its own typed `http`, like `handlers-cards.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

/** The phone id of the session whose finish set a new best. */
export const BEST_SESSION_CLIENT_ID = "0199c000-0000-7000-8000-00000000b001";
/** The phone id of the session that set none. */
export const NO_BEST_SESSION_CLIENT_ID = "0199c000-0000-7000-8000-00000000b002";

/**
 * Strength records, answered from the generated fixtures
 * (`backend/scripts/emit_strength_records_fixture.py`) and honouring the
 * request: the bests belong to the session id asked for, an id the server
 * never saw is a 404 as on the real API, and a history belongs to the slug
 * asked for — only `back_squat` has one here.
 */
export const strengthRecordHandlers = [
  http.get("/api/v1/personal-bests", ({ query, response }) => {
    const id = query.get("session_client_id");
    if (id === BEST_SESSION_CLIENT_ID) return response(200).json(SESSION_BESTS);
    if (id === NO_BEST_SESSION_CLIENT_ID) {
      return response(200).json(SESSION_BESTS_NONE);
    }
    return response(404).json({ detail: `No session with client id ${id}` });
  }),
  http.get("/api/v1/exercises/{exercise_id}/history", ({ params, response }) =>
    params.exercise_id === SQUAT_HISTORY.exercise_id
      ? response(200).json(SQUAT_HISTORY)
      : response(404).json({
          detail: `Exercise '${params.exercise_id}' is not in the catalogue`,
        }),
  ),
];
