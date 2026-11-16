import { HttpResponse } from "msw";
import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
import { EXERCISES } from "@/tests/mocks/fixtures";
import { syncServer } from "@/tests/mocks/sync-state";
import { trainingSettings } from "@/tests/mocks/training-settings-state";

/** Its own typed `http`, like `handlers-sync.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

type Packet = components["schemas"]["PacketRead"];
type Exercise = components["schemas"]["ExerciseRead"];

const STAMP = "2026-01-01T00:00:00Z";

/** The swaps' catalogue entries, copied from `exercise_catalogue.json`. */
export const SWAP_LIBRARY: Exercise[] = [
  {
    id: "barbell_row",
    name: "Barbell Row",
    category: "pull",
    unilateral: false,
    muscles: {
      lats: 1.0,
      rhomboids: 1.0,
      traps_middle: 1.0,
      delt_posterior: 0.5,
      biceps: 0.5,
      brachialis: 0.5,
      erector_spinae: 0.5,
      teres_major: 0.5,
      forearm_flexors: 0.5,
    },
    mechanic: "multi_joint",
    pattern: "horizontal_pull",
    equipment: ["barbell"],
    load_type: "barbell",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
  {
    id: "pendlay_row",
    name: "Pendlay Row",
    category: "pull",
    unilateral: false,
    muscles: {
      lats: 1.0,
      rhomboids: 1.0,
      traps_middle: 1.0,
      delt_posterior: 0.5,
      biceps: 0.5,
      brachialis: 0.5,
      erector_spinae: 0.5,
      teres_major: 0.5,
      forearm_flexors: 0.5,
    },
    mechanic: "multi_joint",
    pattern: "horizontal_pull",
    equipment: ["barbell"],
    load_type: "barbell",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
  {
    id: "inverted_row",
    name: "Inverted Row",
    category: "pull",
    unilateral: false,
    muscles: {
      rhomboids: 1.0,
      traps_middle: 1.0,
      lats: 1.0,
      delt_posterior: 0.5,
      biceps: 0.5,
      brachialis: 0.5,
      erector_spinae: 0.5,
    },
    mechanic: "multi_joint",
    pattern: "horizontal_pull",
    equipment: ["barbell", "rack"],
    load_type: "bodyweight",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
  {
    id: "dead_bug",
    name: "Dead Bug",
    category: "core",
    unilateral: false,
    muscles: {
      transversus_abdominis: 1.0,
      rectus_abdominis: 1.0,
      obliques: 0.5,
      hip_flexors: 0.5,
    },
    mechanic: "single_joint",
    pattern: "anti_extension",
    equipment: ["bodyweight"],
    load_type: "bodyweight",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
  {
    id: "hollow_body_hold",
    name: "Hollow Body Hold",
    category: "core",
    unilateral: false,
    muscles: {
      rectus_abdominis: 1.0,
      transversus_abdominis: 1.0,
      hip_flexors: 0.5,
      obliques: 0.5,
    },
    mechanic: "single_joint",
    pattern: "anti_extension",
    equipment: ["bodyweight"],
    load_type: "bodyweight",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
  {
    id: "ab_wheel_rollout",
    name: "Ab Wheel Rollout",
    category: "core",
    unilateral: false,
    muscles: {
      rectus_abdominis: 1.0,
      transversus_abdominis: 1.0,
      lats: 0.5,
      obliques: 0.5,
      delt_anterior: 0.5,
      serratus_anterior: 0.5,
    },
    mechanic: "multi_joint",
    pattern: "anti_extension",
    equipment: ["ab_wheel"],
    load_type: "bodyweight",
    restrictions: [],
    created_at: STAMP,
    updated_at: STAMP,
  },
];

/**
 * A session packet the real API could produce — captured from
 * `GET /strength/packet` for this exact plan (a one-arm row at 20 kg with
 * `warmup: "none"`, then a 45 s plank), with only the ids and dates moved.
 *
 * The lines were chosen so the packet carries **no derived number**: a
 * dumbbell line pre-fills the prescribed weight and has no plates, its ramp
 * is switched off, and a bodyweight hold has no weight at all. Warm-ups and
 * plates are the backend's arithmetic, pinned by `test_packet_api.py`
 * against the domain; typing them here would be a fixture agreeing with
 * itself.
 */
export function packetFixture(today: string, generatedAt: Date): Packet {
  // The server sends the whole catalogue, sorted by slug; the fixture
  // carries a slice of it: the entries the tests pick from, and the swaps
  // the captured lines offer.
  const library = [
    ...EXERCISES,
    ...SWAP_LIBRARY.filter((swap) => !EXERCISES.some((e) => e.id === swap.id)),
  ].sort((a, b) => a.id.localeCompare(b.id));
  return {
    schema_version: 1,
    generated_at: generatedAt.toISOString(),
    starts_on: today,
    ends_before: addDays(today, 7),
    // What the packet was prepared under is whatever the athlete's settings
    // are at the moment it is served, as on the real endpoint — a fixed value
    // here could not fail when a settings change never reaches the phone.
    settings: {
      mode: trainingSettings().mode,
      plain_logbook: trainingSettings().plain_logbook,
      units: trainingSettings().units,
    },
    // `_SCALE` in `backend/app/services/packet.py`: Simple asks Easy/Good/Hard.
    effort: {
      scale:
        trainingSettings().mode === "simple" ? "easy_good_hard" : "reps_left",
      ask: trainingSettings().ask_frequency,
    },
    gym: {
      bar_kg: 20,
      plate_pairs: [
        { kg: 25, pairs: 4 },
        { kg: 20, pairs: 2 },
        { kg: 10, pairs: 2 },
        { kg: 5, pairs: 2 },
        { kg: 2.5, pairs: 2 },
        { kg: 1.25, pairs: 2 },
      ],
      dumbbell_step_kg: 2,
      dumbbell_max_kg: 50,
      stack_step_kg: 5,
      stack_max_kg: 150,
    },
    sessions: [
      {
        planned_session_id: "0199c000-0000-7000-8000-00000000a001",
        date: today,
        time_of_day: null,
        purpose: "max_strength",
        intent_text: null,
        key: false,
        plan_id: "0199c000-0000-7000-8000-00000000b001",
        follow_exactly: false,
        title: null,
        lines: [
          {
            line_ref: "0.0",
            group_index: 0,
            item_index: 0,
            group_label: null,
            exercise_id: "single_arm_dumbbell_row",
            exercise_name: "Single-Arm Dumbbell Row",
            prescription: {
              exercise_id: "single_arm_dumbbell_row",
              sets: 3,
              reps: 10,
              duration_s: null,
              per_side: true,
              load: { kind: "kg", value: 20 },
              rir: null,
              rest_s: null,
              tempo: null,
              notes: null,
              set_kind: null,
              main: null,
              warmup: "none",
              progression: null,
            },
            main: true,
            equipment: "dumbbell",
            prefill: {
              source: "prescription",
              load_kg: 20,
              reps: 10,
              duration_s: null,
              starting_flow: false,
            },
            warmups: [],
            plates_per_side: null,
            swaps: [
              { exercise_id: "barbell_row", exercise_name: "Barbell Row" },
              { exercise_id: "pendlay_row", exercise_name: "Pendlay Row" },
              { exercise_id: "inverted_row", exercise_name: "Inverted Row" },
            ],
            avoided: false,
            ease_back: null,
            // Never logged and not under Plain logbook: the server's
            // first-time suggestion, which keeps the prescription's pre-fill.
            suggestion: {
              outcome: "first_time",
              load_kg: null,
              reps: 10,
              change_kg: null,
              explanation: {
                rule: null,
                evidence: null,
                reason: "first_time",
                inputs: [],
                target_reps: 10,
                target_reps_left: null,
                computed_kg: null,
                limit: null,
              },
            },
          },
          {
            line_ref: "1.0",
            group_index: 1,
            item_index: 0,
            group_label: null,
            exercise_id: "front_plank",
            exercise_name: "Front Plank",
            prescription: {
              exercise_id: "front_plank",
              sets: 3,
              reps: null,
              duration_s: 45,
              per_side: null,
              load: { kind: "bodyweight", value: null },
              rir: null,
              rest_s: null,
              tempo: null,
              notes: null,
              set_kind: null,
              main: null,
              warmup: null,
              progression: null,
            },
            main: false,
            equipment: null,
            prefill: {
              source: "prescription",
              load_kg: null,
              reps: null,
              duration_s: 45,
              starting_flow: false,
            },
            warmups: [],
            plates_per_side: null,
            swaps: [
              { exercise_id: "dead_bug", exercise_name: "Dead Bug" },
              {
                exercise_id: "hollow_body_hold",
                exercise_name: "Hollow Body Hold",
              },
              {
                exercise_id: "ab_wheel_rollout",
                exercise_name: "Ab Wheel Rollout",
              },
            ],
            avoided: false,
            ease_back: null,
            // A hold has no weight to suggest.
            suggestion: null,
          },
        ],
      },
    ],
    last_numbers: [],
    library,
    notes: [],
  };
}

/**
 * `GET /strength/packet`, as of the moment it is asked — the server builds
 * the packet per request. Down with the fake network (`syncServer().healthy`),
 * like the outbox's endpoints. `today` is the date the server's clock
 * reads and `served` what it answered; a test resets both in `beforeEach`.
 */
export const packetServer = { today: "", served: [] as Packet[] };

export const packetHandlers = [
  http.get("/api/v1/strength/packet", ({ response }) => {
    if (!syncServer().healthy) return response.untyped(HttpResponse.error());
    const packet = packetFixture(
      packetServer.today || new Date().toISOString().slice(0, 10),
      new Date(),
    );
    packetServer.served.push(packet);
    return response(200).json(packet);
  }),
];
