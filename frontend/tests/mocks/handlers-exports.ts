import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { createOpenApiHttp } from "openapi-msw";

import type { paths } from "@/generated/api/schema";
import { ACTIVITY_IDS } from "@/tests/mocks/fixtures";

/** Its own typed `http`, like `handlers-power.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

/**
 * The gym fixture session's text export, as the real API writes it.
 *
 * Not typed in: `backend/tests/unit/test_exports_api.py` posts the same
 * session (`frontend_gym_session`) and asserts the API's answer equals this
 * file byte for byte, so the mock serves what the backend produces.
 */
const GYM_TEXT = readFileSync(
  resolve(
    import.meta.dirname,
    "../../../backend/tests/vectors/session_export.txt",
  ),
  "utf8",
);

/**
 * One-session export. Serves the text of the one session a committed export
 * exists for and refuses the rest, honestly, as unknown to the mock. The CSV
 * is not served: the page downloads it by link and never fetches it, so a
 * canned CSV here would be a payload no test reads.
 */
export const exportHandlers = [
  http.get(
    "/api/v1/sessions/{session_id}/export",
    ({ params, query, response }) => {
      const format = query.get("format") ?? "text";
      if (params.session_id === ACTIVITY_IDS.gym && format === "text") {
        return response(200).text(GYM_TEXT);
      }
      return response(404).json({
        detail: `No committed ${format} export for session ${params.session_id}`,
      });
    },
  ),
];
