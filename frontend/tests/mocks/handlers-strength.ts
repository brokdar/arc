import type { components } from "@/generated/api/schema";
import { DEFAULT_GYM, platesPerSide } from "@/lib/strength";
import { EXERCISES } from "@/tests/mocks/fixtures";
import { packetFixture } from "@/tests/mocks/handlers-packet";

type Packet = components["schemas"]["PacketRead"];
type PacketLine = components["schemas"]["PacketLineRead"];

/** A barbell back squat replacing the first line: `sets × reps` at `kg`. */
export interface BarbellLine {
  kg: number;
  sets: number;
  reps: number;
}

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
 * - **Follow-exactly** is the plan's flag, copied onto the session. A line
 *   with a kilogram load pre-fills it as written either way (`_prefill`), so
 *   nothing else changes.
 * - **A barbell line** (`barbell`) swaps the row for a back squat with a
 *   kilogram load and `warmup: "none"`. The packet pre-fills the prescribed
 *   weight and reps; its one derived number, the plates, comes from
 *   `lib/strength` over the server's default gym — the TypeScript twin the
 *   shared vectors hold equal to `app.domain.loading` — rather than typed in.
 */
export function strengthPacket(
  today: string,
  generatedAt: Date,
  {
    mode = "simple",
    plannedRir = null,
    restS = null,
    followExactly = false,
    barbell = null,
  }: {
    mode?: "simple" | "standard";
    plannedRir?: number | null;
    restS?: number | null;
    followExactly?: boolean;
    barbell?: BarbellLine | null;
  } = {},
): Packet {
  const packet = packetFixture(today, generatedAt);
  const [session] = packet.sessions;
  const lines = session.lines.map(
    (line): PacketLine => ({
      ...line,
      prescription: {
        ...line.prescription,
        rir: line.line_ref === "0.0" ? plannedRir : line.prescription.rir,
        rest_s: restS,
      },
    }),
  );
  if (barbell) lines[0] = barbellLine(lines[0], barbell);
  return {
    ...packet,
    settings: { ...packet.settings, mode },
    effort: {
      ...packet.effort,
      scale: mode === "simple" ? "easy_good_hard" : "reps_left",
    },
    sessions: [{ ...session, follow_exactly: followExactly, lines }],
    library: barbell
      ? EXERCISES.filter((exercise) =>
          ["back_squat", "front_plank"].includes(exercise.id),
        )
      : packet.library,
  };
}

function barbellLine(line: PacketLine, { kg, sets, reps }: BarbellLine) {
  return {
    ...line,
    exercise_id: "back_squat",
    exercise_name: "Back Squat",
    prescription: {
      ...line.prescription,
      exercise_id: "back_squat",
      sets,
      reps,
      per_side: null,
      load: { kind: "kg", value: kg },
    },
    equipment: "barbell",
    prefill: { ...line.prefill, load_kg: kg, reps },
    warmups: [],
    plates_per_side: platesPerSide(kg, "barbell", DEFAULT_GYM),
  } satisfies PacketLine;
}
