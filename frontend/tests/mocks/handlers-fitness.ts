import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
import { athleteToday } from "@/tests/mocks/fixtures";
import { FITNESS_TREND } from "@/tests/mocks/generated-pmc";

type Trend = components["schemas"]["FitnessTrendRead"];

/**
 * Its own typed `http`, on the same base URL as `handlers.ts`, rather than
 * importing that one: `handlers.ts` spreads these in, and the import would be
 * a cycle.
 */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(`${to}T12:00:00Z`) - Date.parse(`${from}T12:00:00Z`)) /
      86_400_000,
  );
}

/**
 * A generated read moved so it ends on the athlete's today.
 *
 * Moves the calendar and none of the numbers: the series is a function of the
 * loads and their order, not of which dates they fell on, so shifting every
 * date by the same number of days is a read the API could serve for the same
 * history recorded later.
 */
export function rebased(document: Trend, today = athleteToday()): Trend {
  const shift = daysBetween(document.maturity.as_of, today);
  const move = (date: string) => addDays(date, shift);
  const moveNullable = (date: string | null) =>
    date === null ? null : move(date);
  return {
    ...document,
    points: document.points.map((point) => ({
      ...point,
      local_date: move(point.local_date),
    })),
    maturity: {
      ...document.maturity,
      as_of: move(document.maturity.as_of),
      first_session: moveNullable(document.maturity.first_session),
      ctl: {
        ...document.maturity.ctl,
        matures_on: moveNullable(document.maturity.ctl.matures_on),
      },
      atl: {
        ...document.maturity.atl,
        matures_on: moveNullable(document.maturity.atl.matures_on),
      },
    },
  };
}

/**
 * The read for a `[from, to)` window, as the endpoint answers it: the same
 * series sliced, never recomputed from the window's edge.
 *
 * `maturity` stays as of the document's last day. The page never sends `to`
 * — it reads through today — and restating the maturity rule here to answer
 * one would be a second copy of `app.domain.pmc.maturity` in a mock.
 */
export function fitnessWindow(
  document: Trend,
  from: string | null,
  to: string | null,
): Trend {
  const moved = rebased(document);
  return {
    ...moved,
    points: moved.points.filter(
      (point) =>
        (from === null || point.local_date >= from) &&
        (to === null || point.local_date < to),
    ),
  };
}

/** `GET /fitness`, serving the generated forty-day history. */
export const fitnessHandlers = [
  http.get("/api/v1/fitness", ({ query, response }) => {
    const from = query.get("from");
    const to = query.get("to");
    if (from !== null && to !== null && from > to) {
      return response(422).json({
        detail: `from (${from}) must not be after to (${to})`,
      });
    }
    return response(200).json(fitnessWindow(FITNESS_TREND, from, to));
  }),
];
