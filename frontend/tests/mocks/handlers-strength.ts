import type { components } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
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
 * - **An avoided row** is the same plan after the athlete set `avoid` on the
 *   one-arm row: the line keeps its exercise and its swaps (avoiding a
 *   movement changes what *other* lines may be offered, not its own swaps)
 *   and says `avoided: true` (`test_exercise_preferences_api.py`).
 * - **History for a swap** is a `last_numbers` row for the barbell row — the
 *   last set the athlete logged of it, which the server copies as logged.
 *   It is already in the library, as one of the row's swaps.
 */
export function strengthPacket(
  today: string,
  generatedAt: Date,
  {
    mode = "simple",
    plannedRir = null,
    restS = null,
    avoided = false,
    barbellRowHistory = null,
  }: {
    mode?: "simple" | "standard";
    plannedRir?: number | null;
    restS?: number | null;
    avoided?: boolean;
    barbellRowHistory?: { loadKg: number; reps: number } | null;
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
    last_numbers: barbellRowHistory
      ? [
          {
            exercise_id: "barbell_row",
            exercise_name: "Barbell Row",
            load_kg: barbellRowHistory.loadKg,
            reps: barbellRowHistory.reps,
            duration_s: null,
            per_side: false,
            logged_on: addDays(today, -3),
            increase_blocked: false,
          },
        ]
      : packet.last_numbers,
    sessions: [
      {
        ...session,
        lines: session.lines.map((line) => ({
          ...line,
          avoided: avoided && line.line_ref === "0.0",
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
