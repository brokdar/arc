import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";
import { ingestState } from "@/tests/mocks/fixtures";
import {
  NO_POWER_CURVE,
  NO_RECORDING_CURVE,
  RIDE_POWER_CURVE,
} from "@/tests/mocks/generated-power-curve";

/**
 * Its own typed `http`, on the same base URL as `handlers.ts`, rather than
 * importing that one: `handlers.ts` spreads these in, and the import would be
 * a cycle.
 */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

/**
 * The power-curve read, answered the way `PowerCurveService.read` decides.
 *
 * It honours the session it is asked about: an unknown id is the service's
 * 404, a session typed in by hand and a recording without a power channel are
 * its two `not_assessed` answers, and any other recording gets the curve of
 * the stream the streams handler serves — so a page showing both agrees.
 */
export const powerHandlers = [
  http.get(
    "/api/v1/sessions/{session_id}/power-curve",
    ({ params, response }) => {
      const session = ingestState().sessions.find(
        (row) => row.id === params.session_id,
      );
      if (!session) {
        return response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
      }
      if (session.recordings.length === 0) {
        return response(200).json(NO_RECORDING_CURVE);
      }
      if (
        !session.recordings.some((recording) =>
          recording.channels.includes("power"),
        )
      ) {
        return response(200).json(NO_POWER_CURVE);
      }
      return response(200).json(RIDE_POWER_CURVE);
    },
  ),
];
