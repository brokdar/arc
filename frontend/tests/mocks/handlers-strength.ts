import type { components } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";
import { DEFAULT_GYM, platesPerSide } from "@/lib/strength";
import { EXERCISES, SWAP_EXERCISES } from "@/tests/mocks/fixtures";
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
 * - **An avoided row** is the same plan after the athlete set `avoid` on the
 *   one-arm row: the line keeps its exercise and its swaps (avoiding a
 *   movement changes what *other* lines may be offered, not its own swaps)
 *   and says `avoided: true` (`test_exercise_preferences_api.py`).
 * - **History for a swap** is a `last_numbers` row for the barbell row — the
 *   last set the athlete logged of it, which the server copies as logged.
 *   It is already in the library, as one of the row's swaps.
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
    avoided = false,
    barbellRowHistory = null,
  }: {
    mode?: "simple" | "standard";
    plannedRir?: number | null;
    restS?: number | null;
    followExactly?: boolean;
    barbell?: BarbellLine | null;
    avoided?: boolean;
    barbellRowHistory?: { loadKg: number; reps: number } | null;
  } = {},
): Packet {
  const packet = packetFixture(today, generatedAt);
  const [session] = packet.sessions;
  const lines = session.lines.map(
    (line): PacketLine => ({
      ...line,
      avoided: avoided && line.line_ref === "0.0",
      prescription: {
        ...line.prescription,
        rir: line.line_ref === "0.0" ? plannedRir : line.prescription.rir,
        rest_s: restS,
      },
      // Follow-exactly bypasses progression on the server: no suggestion.
      // Otherwise the first-time suggestion names the target it would hold
      // the effort to, so a planned reps-left target appears in it too.
      suggestion: followExactly
        ? null
        : line.suggestion && line.line_ref === "0.0"
          ? {
              ...line.suggestion,
              explanation: {
                ...line.suggestion.explanation,
                target_reps_left: plannedRir,
              },
            }
          : line.suggestion,
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
    sessions: [{ ...session, follow_exactly: followExactly, lines }],
    // The library names every planned exercise and every swap offered.
    library: barbell
      ? libraryOf([
          "back_squat",
          ...SQUAT_SWAPS.map((swap) => swap.exercise_id),
          "front_plank",
          "dead_bug",
          "hollow_body_hold",
          "ab_wheel_rollout",
        ])
      : packet.library,
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
    library: libraryOf([
      "back_squat",
      ...SQUAT_SWAPS.map((swap) => swap.exercise_id),
    ]),
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
            swaps: SQUAT_SWAPS,
            avoided: false,
            ease_back: null,
            // Never logged, not Plain logbook: the server's first-time
            // suggestion for 5 reps, with no reps-left target to name.
            suggestion: firstTime(5, null),
          },
        ],
      },
    ],
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
    swaps: SQUAT_SWAPS,
    ease_back: null,
    // A squat never logged: the first-time suggestion for its own reps, or
    // none where the line had none (follow-exactly bypasses progression).
    suggestion: line.suggestion
      ? firstTime(reps, lowOf(line.prescription.rir))
      : null,
  } satisfies PacketLine;
}

/**
 * A back squat's swaps as the server ranks them with no avoids and no
 * remembered pick — pinned by
 * `test_the_frontend_back_squat_fixtures_swaps_are_the_servers`.
 */
const SQUAT_SWAPS: PacketLine["swaps"] = [
  { exercise_id: "box_squat", exercise_name: "Box Squat" },
  { exercise_id: "safety_bar_squat", exercise_name: "Safety Bar Squat" },
  {
    exercise_id: "bulgarian_split_squat",
    exercise_name: "Bulgarian Split Squat",
  },
];

/** The catalogue entries for ``ids``, in the server's order (by id). */
function libraryOf(ids: readonly string[]): Packet["library"] {
  return [...EXERCISES, ...SWAP_EXERCISES]
    .filter((exercise) => ids.includes(exercise.id))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** What `app.domain.progression` returns for an exercise never logged. */
function firstTime(
  reps: number,
  repsLeft: number | null,
): NonNullable<PacketLine["suggestion"]> {
  return {
    outcome: "first_time",
    load_kg: null,
    reps,
    change_kg: null,
    explanation: {
      rule: null,
      evidence: null,
      reason: "first_time",
      inputs: [],
      target_reps: reps,
      target_reps_left: repsLeft,
      computed_kg: null,
      limit: null,
    },
  };
}

/** A count's low end, as `_low` reads a reps-left target. */
function lowOf(count: number | number[] | null | undefined): number | null {
  if (count === null || count === undefined) return null;
  return Array.isArray(count) ? count[0] : count;
}
