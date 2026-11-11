// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_strength_records_fixture.py`
// (`just strength-records-fixture`) by running `app.domain.strength_records`
// over stored sessions and projecting the result through the functions the
// endpoints use. Every best, rep max and estimate below is the domain's own.
//
// Regenerate after changing app/domain/strength_records.py or the read
// shapes, and commit the result.

import type { components } from "@/generated/api/schema";

type Schemas = components["schemas"];

/**
 * `GET /personal-bests` for a session whose Romanian deadlift (free text)
 * reached 100 kg × 8 after an earlier 100 kg × 7; its squats matched last
 * time and set no best.
 */
export const SESSION_BESTS: Schemas["SessionBestsRead"] = {
  bests: [
    {
      exercise_id: null,
      exercise_name: "Romanian deadlift",
      load_kg: 100.0,
      reps: 8,
    },
  ],
};

/** The same lift, weaker than before: no new best. */
export const SESSION_BESTS_NONE: Schemas["SessionBestsRead"] = {
  bests: [],
};

/**
 * `GET /exercises/back_squat/history` over five sessions, the third
 * don't-learn: it holds a best but no estimate.
 */
export const SQUAT_HISTORY: Schemas["ExerciseHistoryRead"] = {
  bests: [
    {
      date: "2026-09-08",
      load_kg: 110.0,
      reps: 3,
      session_id: "0199a1b2-0000-7000-8000-000000000007",
    },
    {
      date: "2026-09-15",
      load_kg: 102.5,
      reps: 5,
      session_id: "0199a1b2-0000-7000-8000-000000000014",
    },
    {
      date: "2026-09-11",
      load_kg: 100.0,
      reps: 8,
      session_id: "0199a1b2-0000-7000-8000-000000000010",
    },
  ],
  estimate: {
    current_kg: 126.4,
    points: [
      {
        date: "2026-09-01",
        estimate_kg: 117.2,
        median_kg: null,
        session_id: "0199a1b2-0000-7000-8000-000000000000",
      },
      {
        date: "2026-09-04",
        estimate_kg: 123.3,
        median_kg: null,
        session_id: "0199a1b2-0000-7000-8000-000000000003",
      },
      {
        date: "2026-09-11",
        estimate_kg: 130.0,
        median_kg: 123.3,
        session_id: "0199a1b2-0000-7000-8000-000000000010",
      },
      {
        date: "2026-09-15",
        estimate_kg: 126.4,
        median_kg: 126.4,
        session_id: "0199a1b2-0000-7000-8000-000000000014",
      },
    ],
    status: "ok",
  },
  exercise_id: "back_squat",
  exercise_name: "Back Squat",
  exposures: 5,
  rep_maxes: [
    {
      date: "2026-09-08",
      load_kg: 110.0,
      reps: 3,
      session_id: "0199a1b2-0000-7000-8000-000000000007",
      target_reps: 1,
    },
    {
      date: "2026-09-08",
      load_kg: 110.0,
      reps: 3,
      session_id: "0199a1b2-0000-7000-8000-000000000007",
      target_reps: 3,
    },
    {
      date: "2026-09-15",
      load_kg: 102.5,
      reps: 5,
      session_id: "0199a1b2-0000-7000-8000-000000000014",
      target_reps: 5,
    },
    {
      date: "2026-09-11",
      load_kg: 100.0,
      reps: 8,
      session_id: "0199a1b2-0000-7000-8000-000000000010",
      target_reps: 8,
    },
  ],
};

/** A lift never logged: empty lists and `not_enough_data`. */
export const SQUAT_HISTORY_EMPTY: Schemas["ExerciseHistoryRead"] = {
  bests: [],
  estimate: {
    current_kg: null,
    points: [],
    status: "not_enough_data",
  },
  exercise_id: "back_squat",
  exercise_name: "Back Squat",
  exposures: 0,
  rep_maxes: [],
};
