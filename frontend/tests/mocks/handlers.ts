import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import {
  anchorVersionFixture,
  EXERCISES,
  plannedSessionFixture,
  planWeekFixture,
  purposeTemplateFixture,
  SESSION_IDS,
  WORKOUT_LABELS,
  WORKOUTS,
  workoutFixture,
} from "@/tests/mocks/fixtures";

/**
 * Typed MSW handlers: paths, params, and response bodies are all inferred
 * from the generated OpenAPI types, so a backend contract change that isn't
 * reflected here becomes a type-check failure instead of silent mock drift.
 *
 * **The mutating handlers honour the request.** A fake that answers `POST` and
 * `PATCH` with a canned body cannot fail when the form drops a field, so a
 * test written against it passes whatever the component sends; the same goes
 * for a `move` that ignores the date it was given. These echo what they were
 * asked to do, which is the only version of them a test can actually be
 * wrong about.
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
      plan_state: "active",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }),
  ),
  http.patch("/api/v1/athlete", async ({ request, response }) => {
    const body = await request.json();
    return response(200).json({
      name: "Alex Rider",
      date_of_birth: "1990-06-15",
      sex: "male",
      height_cm: 181.5,
      capabilities: {},
      plan_state: body.plan_state ?? "active",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  }),

  // The week the calendar asks for, built around whatever `start` it sends.
  http.get("/api/v1/plan/week", ({ query, response }) => {
    const start = query.get("start") ?? mondayOf(todayIsoDate());
    return response(200).json(planWeekFixture(start));
  }),
  http.get(
    "/api/v1/planned-sessions/{planned_session_id}",
    ({ params, response }) =>
      response(200).json(plannedSessionFixture(params.planned_session_id)),
  ),
  // A move lands on the date it was given. Answering with the session's old
  // date would let a component that sent the wrong one still pass.
  http.post(
    "/api/v1/planned-sessions/{planned_session_id}/move",
    async ({ params, request, response }) => {
      const body = await request.json();
      return response(200).json({
        ...plannedSessionFixture(params.planned_session_id),
        date: body.date,
      });
    },
  ),
  // A copy is a *new* session: its own id, back to `planned` however the
  // original ended up, and an intent history that starts at version 1.
  http.post(
    "/api/v1/planned-sessions/{planned_session_id}/copy",
    async ({ params, request, response }) => {
      const body = await request.json();
      const source = plannedSessionFixture(params.planned_session_id);
      return response(201).json({
        ...source,
        id: SESSION_IDS.copy,
        date: body.date,
        status: "planned",
        intent_versions: 1,
        intent: { ...source.intent, version: 1 },
      });
    },
  ),
  http.delete("/api/v1/planned-sessions/{planned_session_id}", ({ response }) =>
    response(204).empty(),
  ),
  http.post("/api/v1/planned-sessions", async ({ request, response }) =>
    response(201).json(
      applyIntent(plannedSessionFixture(SESSION_IDS.vo2), await request.json()),
    ),
  ),
  http.patch(
    "/api/v1/planned-sessions/{planned_session_id}",
    async ({ params, request, response }) =>
      response(200).json(
        applyIntent(
          plannedSessionFixture(params.planned_session_id),
          await request.json(),
        ),
      ),
  ),

  // The library, the catalogue and the templates the creator draws on. The
  // list endpoint filters here the way the API does, so a search test is a
  // test of the *component's* query, not of a stubbed result.
  http.get("/api/v1/workouts", ({ query, response }) => {
    const search = query.get("q")?.toLowerCase() ?? "";
    const folder = query.get("folder");
    const tag = query.get("tag");
    const discipline = query.get("discipline");
    const items = WORKOUTS.filter(
      (workout) =>
        (search === "" ||
          workout.name.toLowerCase().includes(search) ||
          (workout.description ?? "").toLowerCase().includes(search)) &&
        (!folder || workout.folder === folder) &&
        (!tag || workout.tags.includes(tag)) &&
        (!discipline || workout.discipline === discipline),
    );
    return response(200).json({
      items,
      total: items.length,
      offset: 0,
      limit: 100,
    });
  }),
  http.get("/api/v1/workouts/{workout_id}", ({ params, response }) =>
    response(200).json(workoutFixture(params.workout_id)),
  ),
  http.post("/api/v1/workouts", ({ response }) =>
    response(201).json(workoutFixture(WORKOUTS[0]?.id ?? "")),
  ),
  http.patch("/api/v1/workouts/{workout_id}", ({ params, response }) =>
    response(200).json(workoutFixture(params.workout_id)),
  ),
  http.delete("/api/v1/workouts/{workout_id}", ({ response }) =>
    response(204).empty(),
  ),
  http.get("/api/v1/workout-labels", ({ response }) =>
    response(200).json(WORKOUT_LABELS),
  ),
  http.get("/api/v1/exercises", ({ response }) =>
    response(200).json({
      items: EXERCISES,
      total: EXERCISES.length,
      offset: 0,
      limit: 200,
    }),
  ),
  http.get("/api/v1/purposes/{purpose}", ({ params, response }) =>
    response(200).json(purposeTemplateFixture(params.purpose)),
  ),
  http.get("/api/v1/anchors/current", ({ query, response }) => {
    const anchorType = query.get("anchor_type");
    return anchorType === "ftp" || anchorType === "lthr"
      ? response(200).json(anchorVersionFixture(anchorType))
      : response(404).json({ detail: "No max_hr version in force" });
  }),
];

type PlannedSession = components["schemas"]["PlannedSessionRead"];
type PlannedSessionWrite =
  | components["schemas"]["PlannedSessionCreate"]
  | components["schemas"]["PlannedSessionUpdate"];

/**
 * Answer a write with what it actually asked for.
 *
 * The API appends a new intent version carrying the fields it was sent, so a
 * response that quietly kept the old purpose or dropped the criteria would be
 * a response no server produces — and would hide exactly the class of bug
 * these tests exist to catch (a form that posts an empty `success_criteria`).
 * Only the intent fields are echoed; the resolved steps and predictions stay
 * the fixture's, because recomputing them here would be reimplementing the
 * domain in a mock.
 */
function applyIntent(
  session: PlannedSession,
  body: PlannedSessionWrite,
): PlannedSession {
  return {
    ...session,
    date: body.date ?? session.date,
    intent: {
      ...session.intent,
      version: session.intent.version + 1,
      purpose: body.purpose ?? session.intent.purpose,
      intent_text:
        body.intent_text === undefined
          ? session.intent.intent_text
          : body.intent_text,
      coach_notes:
        body.coach_notes === undefined
          ? session.intent.coach_notes
          : body.coach_notes,
      success_criteria:
        body.success_criteria ?? session.intent.success_criteria,
      workout_id:
        body.workout_id === undefined
          ? session.intent.workout_id
          : body.workout_id,
      structure: body.structure ?? session.intent.structure,
    },
  };
}
