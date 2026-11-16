import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";

/** Its own typed `http`, like `handlers-cards.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

type Preference = components["schemas"]["ExercisePreferenceRead"];
type Swap = components["schemas"]["SwapRead"];

/**
 * The *Ease back in* ladders the server computes, easiest first. Only the
 * back squat's is here, and it is the backend's own — pinned by
 * `test_exercise_preferences_api.py::test_ease_back_in_advances_only_on_the_athletes_tap`.
 * Any other slug has nothing easier, which the server answers with a 409.
 */
const LADDERS: Record<string, Swap[]> = {
  back_squat: [
    { exercise_id: "forward_lunge", exercise_name: "Forward Lunge" },
    { exercise_id: "box_squat", exercise_name: "Box Squat" },
    {
      exercise_id: "bulgarian_split_squat",
      exercise_name: "Bulgarian Split Squat",
    },
  ],
};

/** Slugs this mock's catalogue knows; anything else is a 404, as on the API. */
const KNOWN = new Set([
  "back_squat",
  "romanian_deadlift",
  "single_arm_dumbbell_row",
  "front_plank",
  "leg_press",
  "goblet_squat",
]);

/** Each exercise's stored step (null when not easing back) and flags. */
interface Stored {
  avoid: boolean;
  less_often: boolean;
  remembered_swap_id: string | null;
  step: number | null;
}

let stored = new Map<string, Stored>();

export function resetPreferences(): void {
  stored = new Map();
}

function read(slug: string): Preference {
  const row = stored.get(slug);
  const ladder = LADDERS[slug] ?? [];
  const step = row?.step ?? null;
  return {
    exercise_id: slug,
    avoid: row?.avoid ?? false,
    less_often: row?.less_often ?? false,
    remembered_swap_id: row?.remembered_swap_id ?? null,
    ease_back:
      step === null || ladder.length === 0
        ? null
        : { step, steps: ladder, current: ladder[step] },
  };
}

function notFound(slug: string) {
  return { detail: `Exercise '${slug}' is not in the catalogue` };
}

/**
 * `/exercises/{id}/preferences` and its advance, honouring the request the
 * way `app.services.exercise_preferences` does: a PATCH changes only the
 * fields it carries, `ease_back: true` starts at the easiest step (and
 * leaves a ladder under way alone), and only the advance moves the step —
 * past the last, the ladder ends.
 */
export const preferenceHandlers = [
  http.get(
    "/api/v1/exercises/{exercise_id}/preferences",
    ({ params, response }) =>
      KNOWN.has(params.exercise_id)
        ? response(200).json(read(params.exercise_id))
        : response(404).json(notFound(params.exercise_id)),
  ),
  http.patch(
    "/api/v1/exercises/{exercise_id}/preferences",
    async ({ params, request, response }) => {
      const slug = params.exercise_id;
      if (!KNOWN.has(slug)) return response(404).json(notFound(slug));
      const body = await request.json();
      const row: Stored = stored.get(slug) ?? {
        avoid: false,
        less_often: false,
        remembered_swap_id: null,
        step: null,
      };
      const next = { ...row };
      if (body.avoid !== undefined) next.avoid = body.avoid;
      if (body.less_often !== undefined) next.less_often = body.less_often;
      if (body.remembered_swap_id !== undefined) {
        next.remembered_swap_id = body.remembered_swap_id;
      }
      if (body.ease_back === false) next.step = null;
      if (body.ease_back === true && next.step === null) {
        if (!LADDERS[slug]) {
          return response(409).json({
            detail: "Nothing in the library is an easier variation.",
          });
        }
        next.step = 0;
      }
      stored.set(slug, next);
      return response(200).json(read(slug));
    },
  ),
  http.post(
    "/api/v1/exercises/{exercise_id}/preferences/ease-back/advance",
    ({ params, response }) => {
      const slug = params.exercise_id;
      if (!KNOWN.has(slug)) return response(404).json(notFound(slug));
      const row = stored.get(slug);
      const ladder = LADDERS[slug] ?? [];
      if (!row || row.step === null || ladder.length === 0) {
        return response(409).json({ detail: "You are not easing back in." });
      }
      const following = row.step + 1;
      stored.set(slug, {
        ...row,
        step: following < ladder.length ? following : null,
      });
      return response(200).json(read(slug));
    },
  ),
];
