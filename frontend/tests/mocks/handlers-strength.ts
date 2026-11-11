import type { components } from "@/generated/api/schema";
import { EXERCISES } from "@/tests/mocks/fixtures";
import { packetFixture } from "@/tests/mocks/handlers-packet";

type Packet = components["schemas"]["PacketRead"];

/**
 * Session packets for the logging screen, built on the captured packet in
 * `handlers-packet.ts` and changed only where the server would change them
 * for different settings or a different plan — never a derived number:
 *
 * - **Standard mode** is the same plan under `mode: "standard"`, for which
 *   the server asks the reps-left question (`_SCALE` in
 *   `backend/app/services/packet.py`). Nothing else in the packet depends on
 *   the mode.
 * - **A planned effort target** (`rir: 2` on the row) is part of the
 *   prescription, which the packet copies verbatim; pre-fills and plates do
 *   not read it. It is there so a test can prove the target is never stored
 *   as the athlete's answer.
 * - **A short rest** (`rest_s`) is likewise copied verbatim from the plan.
 */
export function strengthPacket(
  today: string,
  generatedAt: Date,
  {
    mode = "simple",
    plannedRir = null,
    restS = null,
  }: {
    mode?: "simple" | "standard";
    plannedRir?: number | null;
    restS?: number | null;
  } = {},
): Packet {
  const packet = packetFixture(today, generatedAt);
  const [session] = packet.sessions;
  return {
    ...packet,
    settings: { ...packet.settings, mode },
    effort: {
      ...packet.effort,
      scale: mode === "simple" ? "easy_good_hard" : "reps_left",
    },
    sessions: [
      {
        ...session,
        lines: session.lines.map((line) => ({
          ...line,
          prescription: {
            ...line.prescription,
            rir: line.line_ref === "0.0" ? plannedRir : line.prescription.rir,
            rest_s: restS,
          },
        })),
      },
    ],
  };
}

/**
 * A packet whose one line is an exercise never logged: a back squat
 * prescribed by effort (3 × 5 at RPE 8), so there is no weight to pre-fill.
 * This is what `_prefill` in `backend/app/services/packet.py` returns for it
 * (`test_plain_logbook_prefills_prescription_then_last_logged_then_blank`
 * pins the blank pre-fill): `source: "blank"`, every number null,
 * `starting_flow: true` — and so no warm-up ramp and no plates, which the
 * server computes only from a weight. The squat is the session's first
 * multi-joint squat, so it defaults to a main lift.
 */
export function startingFlowPacket(today: string, generatedAt: Date): Packet {
  const packet = packetFixture(today, generatedAt);
  const [session] = packet.sessions;
  return {
    ...packet,
    library: EXERCISES.filter((exercise) => exercise.id === "back_squat"),
    sessions: [
      {
        ...session,
        lines: [
          {
            line_ref: "0.0",
            group_index: 0,
            item_index: 0,
            group_label: null,
            exercise_id: "back_squat",
            exercise_name: "Back Squat",
            prescription: {
              exercise_id: "back_squat",
              sets: 3,
              reps: 5,
              duration_s: null,
              per_side: null,
              load: { kind: "rpe", value: 8 },
              rir: null,
              rest_s: null,
              tempo: null,
              notes: null,
              set_kind: null,
              main: null,
              warmup: null,
              progression: null,
            },
            main: true,
            equipment: "barbell",
            prefill: {
              source: "blank",
              load_kg: null,
              reps: null,
              duration_s: null,
              starting_flow: true,
            },
            warmups: [],
            plates_per_side: null,
          },
        ],
      },
    ],
  };
}
