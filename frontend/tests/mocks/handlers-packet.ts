import { HttpResponse } from "msw";
import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
import { EXERCISES } from "@/tests/mocks/fixtures";
import { syncServer } from "@/tests/mocks/sync-state";

/** Its own typed `http`, like `handlers-sync.ts`, to avoid an import cycle. */
const http = createOpenApiHttp<paths>({ baseUrl: "http://localhost:8000" });

type Packet = components["schemas"]["PacketRead"];

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
  const library = EXERCISES.filter((exercise) =>
    ["front_plank", "single_arm_dumbbell_row"].includes(exercise.id),
  );
  return {
    schema_version: 1,
    generated_at: generatedAt.toISOString(),
    starts_on: today,
    ends_before: addDays(today, 7),
    settings: { mode: "simple", plain_logbook: false, units: "kg" },
    effort: { scale: "easy_good_hard", ask: "always" },
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
          },
        ],
      },
    ],
    last_numbers: [],
    library,
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
