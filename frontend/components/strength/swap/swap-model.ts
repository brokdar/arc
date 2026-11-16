import type {
  ActiveSession,
  Packet,
  PacketLine,
  Stamp,
} from "@/components/strength/session-model";
import type { Mutation } from "@/lib/offline/db";

/**
 * Swapping a planned line in the gym, offline — pure, like `session-model.ts`.
 *
 * The packet carries each line's three swaps (the server's ranking,
 * `backend/app/domain/swaps.py`), the library entry of every one, and the
 * last numbers of any the athlete has logged before, so nothing here asks
 * the network or ranks anything itself.
 *
 * **A swapped line keeps its `line_ref`**, so the substitute's sets answer
 * the planned line — logged with the substitute's own `exercise_id` and the
 * planned one as `replaced_exercise_id` — and completing them completes the
 * line. **Its first weight is the substitute's own**: the weight last logged
 * for it, else the starting flow (a blank pre-fill). **Its reps are the
 * plan's** where the plan names reps, as the server pre-fills a weight from
 * history under a plan's reps (`_prefill`, follow-exactly): the substitute
 * answers the planned 3 × 10, and pre-filling the 8 it was last done for
 * would have the step-down (`step-down.ts`) call the very set it suggested a
 * miss. The planned exercise's
 * pre-fill, warm-ups and plates are dropped, because 20 kg on a one-arm row
 * says nothing about a barbell row. Pinned by `swap.test.tsx`.
 */

export type SwapScope = Extract<Mutation, { kind: "swap" }>["scope"];

/** How a library load type is rounded, for the three the phone rounds. */
const ROUNDED = {
  barbell: "barbell",
  dumbbell: "dumbbell",
  stack: "stack",
} as const;

/** `line` as the screen shows it once `exerciseId` stands in for it. */
export function swappedLine(
  line: PacketLine,
  packet: Packet,
  exerciseId: string,
): PacketLine {
  if (exerciseId === line.exercise_id) return line;
  const entry = packet.library.find((e) => e.id === exerciseId);
  const last = packet.last_numbers.find((n) => n.exercise_id === exerciseId);
  const name =
    entry?.name ??
    line.swaps.find((s) => s.exercise_id === exerciseId)?.exercise_name ??
    exerciseId;
  const perSide =
    line.prescription.per_side === true && entry?.unilateral === true;
  const loadType = entry?.load_type;
  const equipment =
    loadType && loadType in ROUNDED
      ? ROUNDED[loadType as keyof typeof ROUNDED]
      : null;
  const prefill: PacketLine["prefill"] = last
    ? {
        source: "last_logged",
        load_kg: last.load_kg,
        reps: lowOf(line.prescription.reps) ?? last.reps,
        duration_s: last.duration_s,
        starting_flow: false,
      }
    : {
        source: "blank",
        load_kg: null,
        reps: null,
        duration_s: null,
        starting_flow: true,
      };
  return {
    ...line,
    exercise_id: exerciseId,
    exercise_name: name,
    prescription: {
      ...line.prescription,
      exercise_id: exerciseId,
      per_side: perSide ? true : null,
    },
    equipment,
    prefill,
    warmups: [],
    plates_per_side: null,
    // The packet's suggestion was worked out for the planned exercise; the
    // substitute has none, so its chip names where its pre-fill came from.
    suggestion: null,
  };
}

/** Every line as the screen shows it, with today's swaps applied. */
export function linesWithSwaps(
  session: ActiveSession | null,
  lines: readonly PacketLine[],
  packet: Packet | null,
): PacketLine[] {
  const swaps = session?.swaps ?? {};
  if (!packet) return [...lines];
  return lines.map((line) => {
    const pick = swaps[line.line_ref];
    return pick ? swappedLine(line, packet, pick) : line;
  });
}

/** The planned exercise a line's sets stand in for, or null when not swapped. */
export function replacedBy(
  session: ActiveSession,
  planned: PacketLine,
): string | null {
  const pick = session.swaps?.[planned.line_ref];
  return pick && pick !== planned.exercise_id ? planned.exercise_id : null;
}

/**
 * Whether the planned exercise is avoided and today the athlete has neither
 * swapped it nor chosen to do it anyway. While true the screen shows only
 * the swap prompt: the avoided exercise's own logging controls stay hidden
 * (UAT U9: they stayed live underneath, one tap from logging the movement
 * the athlete said never to offer).
 */
export function needsSwapPrompt(
  session: ActiveSession,
  planned: PacketLine,
): boolean {
  return (
    planned.avoided &&
    planned.swaps.length > 0 &&
    session.swaps?.[planned.line_ref] === undefined &&
    !session.keptAvoided?.includes(planned.line_ref)
  );
}

/** "Do it anyway": this avoided line is done as planned today. */
export function keepAvoided(
  session: ActiveSession,
  planned: PacketLine,
): ActiveSession {
  return {
    ...session,
    keptAvoided: [...(session.keptAvoided ?? []), planned.line_ref],
  };
}

/** The session after swapping `planned` for `exerciseId`, and its outbox item. */
export function applySwap(
  session: ActiveSession,
  planned: PacketLine,
  exerciseId: string,
  scope: SwapScope,
  stamp: Stamp,
): { session: ActiveSession; mutation: Mutation } {
  // The starting flow is per line, and what it found was the planned
  // exercise's weight: the substitute finds its own (or reads its history).
  const { [planned.line_ref]: _found, ...starting } = session.starting ?? {};
  return {
    session: {
      ...session,
      swaps: { ...(session.swaps ?? {}), [planned.line_ref]: exerciseId },
      starting,
    },
    mutation: {
      kind: "swap",
      mutation_id: stamp.id(),
      client_edit_time: new Date(stamp.now).toISOString(),
      session_client_id: session.sessionClientId,
      exercise_id: planned.exercise_id,
      swap_exercise_id: exerciseId,
      scope,
    },
  };
}

/** A count's low end — the reps every set must reach. */
function lowOf(count: number | number[] | null | undefined): number | null {
  if (count === null || count === undefined) return null;
  return Array.isArray(count) ? (count[0] ?? null) : count;
}
