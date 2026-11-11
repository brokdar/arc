// GENERATED FILE — do not hand-edit.
//
// Produced by `backend/scripts/emit_progression_fixture.py`
// (`just progression-fixture`) by running `app.domain.progression.suggest`
// over stored sessions and projecting the result through `SuggestionRead`, the
// model the packet serves. Every weight and computed value is the domain's own.
//
// Regenerate after changing app/domain/progression.py or the suggestion read
// shape, and commit the result.

import type { components } from "@/generated/api/schema";

type Suggestion = components["schemas"]["SuggestionRead"];

/**
 * Target 6 reps with 2 left; the last two sessions were 100 kg × 6 with 3
 * left → 102.5 kg (computed 102.6).
 */
export const EASIER_TWICE: Suggestion = {
  change_kg: 2.5,
  explanation: {
    computed_kg: 102.63157894736842,
    evidence: "practice",
    inputs: [
      {
        date: "2026-09-04",
        effort_simple: null,
        load_kg: 100.0,
        reps: 6,
        reps_left: 3.0,
        reps_left_capped: false,
        session_id: "0199a1c3-0000-7000-8000-000000000001",
        trimmed: false,
      },
      {
        date: "2026-09-07",
        effort_simple: null,
        load_kg: 100.0,
        reps: 6,
        reps_left: 3.0,
        reps_left_capped: false,
        session_id: "0199a1c3-0000-7000-8000-000000000002",
        trimmed: false,
      },
    ],
    limit: null,
    reason: "easier_twice",
    rule: "progression.weight_from_effort",
    target_reps: 6,
    target_reps_left: 2.0,
  },
  load_kg: 102.5,
  outcome: "up",
  reps: 6,
};

/**
 * Target 8 reps with 2 left; 100 kg × 5 with 0 left → 90 kg (computed 87.5,
 * held by the 10 % limit).
 */
export const CAPPED_DOWN: Suggestion = {
  change_kg: -10.0,
  explanation: {
    computed_kg: 87.50000000000001,
    evidence: "practice",
    inputs: [
      {
        date: "2026-09-04",
        effort_simple: null,
        load_kg: 100.0,
        reps: 5,
        reps_left: 0.0,
        reps_left_capped: false,
        session_id: "0199a1c3-0000-7000-8000-000000000001",
        trimmed: false,
      },
    ],
    limit: "progression.cap.down",
    reason: "harder_once",
    rule: "progression.weight_from_effort",
    target_reps: 8,
    target_reps_left: 2.0,
  },
  load_kg: 90.0,
  outcome: "down",
  reps: 8,
};

/** An exercise never logged: no weight, no rule. */
export const FIRST_TIME: Suggestion = {
  change_kg: null,
  explanation: {
    computed_kg: null,
    evidence: null,
    inputs: [],
    limit: null,
    reason: "first_time",
    rule: null,
    target_reps: 8,
    target_reps_left: 2.0,
  },
  load_kg: null,
  outcome: "first_time",
  reps: 8,
};
