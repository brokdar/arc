import type { components } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
import { packetFixture } from "@/tests/mocks/handlers-packet";

type Packet = components["schemas"]["PacketRead"];
type PacketLine = components["schemas"]["PacketLineRead"];
type Exercise = components["schemas"]["ExerciseRead"];

/**
 * A third line, captured from `GET /strength/packet` for the same plan with
 * `dumbbell_curl` 3 × 12 at 10 kg, `warmup: "none"` appended: like the row,
 * a dumbbell line pre-filling its prescribed weight, no plates, no ramp.
 */
const CURL_LINE: PacketLine = {
  line_ref: "2.0",
  group_index: 2,
  item_index: 0,
  group_label: null,
  exercise_id: "dumbbell_curl",
  exercise_name: "Dumbbell Curl",
  prescription: {
    exercise_id: "dumbbell_curl",
    sets: 3,
    reps: 12,
    duration_s: null,
    per_side: null,
    load: { kind: "kg", value: 10 },
    rir: null,
    rest_s: null,
    tempo: null,
    notes: null,
    set_kind: null,
    main: null,
    warmup: "none",
    progression: null,
  },
  main: false,
  equipment: "dumbbell",
  prefill: {
    source: "prescription",
    load_kg: 10,
    reps: 12,
    duration_s: null,
    starting_flow: false,
  },
  warmups: [],
  plates_per_side: null,
};

/** The catalogue entry the server sends for it, captured with the line. */
export const CURL: Exercise = {
  id: "dumbbell_curl",
  name: "Dumbbell Curl",
  category: "pull",
  unilateral: false,
  muscles: { biceps: 1.0, brachialis: 0.5, brachioradialis: 0.5 },
  mechanic: "single_joint",
  pattern: "elbow_flexion",
  equipment: ["dumbbell"],
  load_type: "dumbbell",
  restrictions: [],
  created_at: "2026-11-11T11:28:13Z",
  updated_at: "2026-11-11T11:28:13Z",
};

/** The planned session id of the copy three days on (`thursday`). */
export const THURSDAY_ID = "0199c000-0000-7000-8000-00000000a004";

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
    curl = false,
    thursday = false,
    notes = [],
  }: {
    mode?: "simple" | "standard";
    plannedRir?: number | null;
    restS?: number | null;
    /** Append the captured curl line (and its library entry). */
    curl?: boolean;
    /** Add the same plan three days on, as a second planned session. */
    thursday?: boolean;
    /** Notes the server holds, as `PUT /exercises/{id}/note` stored them. */
    notes?: Packet["notes"];
  } = {},
): Packet {
  const packet = packetFixture(today, generatedAt);
  const [base] = packet.sessions;
  const session = curl ? { ...base, lines: [...base.lines, CURL_LINE] } : base;
  const shaped = {
    ...session,
    lines: session.lines.map((line) => ({
      ...line,
      prescription: {
        ...line.prescription,
        rir: line.line_ref === "0.0" ? plannedRir : line.prescription.rir,
        rest_s: restS,
      },
    })),
  };
  return {
    ...packet,
    library: curl ? [...packet.library, CURL] : packet.library,
    notes,
    settings: { ...packet.settings, mode },
    effort: {
      ...packet.effort,
      scale: mode === "simple" ? "easy_good_hard" : "reps_left",
    },
    sessions: thursday
      ? [
          shaped,
          {
            ...shaped,
            planned_session_id: THURSDAY_ID,
            date: addDays(today, 3),
          },
        ]
      : [shaped],
  };
}
