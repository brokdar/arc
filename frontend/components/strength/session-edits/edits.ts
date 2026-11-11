import {
  type ActiveSession,
  ADDED_PREFIX,
  isPlannedLine,
  type LoggedSet,
  type Packet,
  type PacketLine,
  type PacketSession,
  patchMutation,
  type Stamp,
} from "@/components/strength/session-model";
import type { components } from "@/generated/api/schema";
import type { Mutation } from "@/lib/offline/db";

/**
 * Changing a session on the fly: reorder its exercises, add a set or an
 * exercise, move mis-logged sets to the exercise they belong to.
 *
 * Pure, like `session-model.ts`: each edit returns the next `ActiveSession`
 * and the outbox mutations that carry it to the server. Nothing here invents
 * a training number — an added set opens with the exercise's last set, an
 * added exercise with its last logged numbers from the packet or blank.
 *
 * **The stored order is the session's exercise order.** `set_index` groups
 * the sets by exercise, in the order the session's exercises now stand, and
 * keeps the logged order within an exercise. Every edit that changes that
 * order — a reorder, a move, a set logged for an exercise that stands before
 * sets already logged — rewrites the `set_index` of the sets it shifted, with
 * one patch each; a set's identity (`set_client_id`) never changes, so a
 * moved set is the same set, not a delete and a create. Displaced: keeping
 * `set_index` as pure logging order, which made a reorder invisible to the
 * server — the session detail and every positional reader would still list
 * the exercises as planned. When the set was lifted stays on the set
 * (`completed_at`); the scorer files sets by the line they answered
 * (`backend/app/services/scoring.py` `_in_prescription_order`), so a
 * reordered session scores as the same work done in order.
 *
 * **An added set is not a logged set.** "Add set" raises the exercise's set
 * count by one and opens that set with the last set's numbers, so logging it
 * is the ✓ tap like any other: a set the athlete has not lifted yet is never
 * stored as lifted. Displaced: logging the clone at once, which would need an
 * undo every time the extra set was not done after all.
 */

type Exercise = components["schemas"]["ExerciseRead"];

/** The session's exercises: its own, once changed, else the plan's. */
export function linesOf(
  session: ActiveSession,
  planned: PacketSession | null,
): PacketLine[] {
  return session.lines ?? planned?.lines ?? [];
}

/**
 * The sets in stored order: grouped by exercise in `lines` order, logged order
 * within each; a set whose exercise is no longer a line keeps its place at the
 * end.
 */
export function storedOrder(
  sets: readonly LoggedSet[],
  lines: readonly PacketLine[],
): LoggedSet[] {
  const position = new Map(lines.map((line, i) => [line.line_ref, i]));
  return sets
    .map((set, logged) => ({ set, logged }))
    .sort((a, b) => {
      const pa = position.get(a.set.lineRef) ?? lines.length;
      const pb = position.get(b.set.lineRef) ?? lines.length;
      return pa - pb || a.logged - b.logged;
    })
    .map(({ set }) => set);
}

/**
 * The patches that move every set whose index changed between `before` and
 * `after` (both in stored order), skipping `except` — sets whose own create
 * already carries their index.
 */
export function renumber(
  session: ActiveSession,
  before: readonly LoggedSet[],
  after: readonly LoggedSet[],
  stamp: Stamp,
  except: ReadonlySet<string> = new Set(),
): Mutation[] {
  const was = new Map(before.map((set, i) => [set.clientId, i]));
  return after.flatMap((set, index) =>
    except.has(set.clientId) || was.get(set.clientId) === index
      ? []
      : [patchMutation(session, set.clientId, { set_index: index }, stamp)],
  );
}

/**
 * Place newly logged sets where their exercise stands. Returns the sets in
 * stored order, each new set's index, and the patches for the sets they
 * pushed down.
 */
export function placeNew(
  session: ActiveSession,
  lines: readonly PacketLine[],
  created: readonly LoggedSet[],
  stamp: Stamp,
): {
  sets: LoggedSet[];
  indexOf: (clientId: string) => number;
  shifts: Mutation[];
} {
  const sets = storedOrder([...session.sets, ...created], lines);
  const fresh = new Set(created.map((set) => set.clientId));
  return {
    sets,
    indexOf: (clientId) => sets.findIndex((set) => set.clientId === clientId),
    shifts: renumber(session, session.sets, sets, stamp, fresh),
  };
}

export interface Edit {
  session: ActiveSession;
  mutations: Mutation[];
}

/**
 * Move the exercise at `from` to `to` and re-store the sets in the new order.
 * The screen stays on the exercise it showed.
 */
export function reorder(
  session: ActiveSession,
  lines: readonly PacketLine[],
  from: number,
  to: number,
  stamp: Stamp,
): Edit {
  const next = [...lines];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  const sets = storedOrder(session.sets, next);
  const current = lines[session.lineIndex]?.line_ref;
  return {
    session: {
      ...session,
      lines: next,
      sets,
      lineIndex: Math.max(
        0,
        next.findIndex((line) => line.line_ref === current),
      ),
    },
    mutations: renumber(session, session.sets, sets, stamp),
  };
}

/** One more set for the exercise at `index`, which the screen then shows. */
export function addSet(
  session: ActiveSession,
  lines: readonly PacketLine[],
  index: number,
): ActiveSession {
  const next = lines.map((line, i) =>
    i === index
      ? {
          ...line,
          prescription: {
            ...line.prescription,
            sets: line.prescription.sets + 1,
          },
        }
      : line,
  );
  return { ...session, lines: next, lineIndex: index, rest: null };
}

/** How the library's load type maps onto what the gym can load. */
function equipmentOf(exercise: Exercise): PacketLine["equipment"] {
  switch (exercise.load_type) {
    case "barbell":
    case "dumbbell":
    case "stack":
      return exercise.load_type;
    default:
      return null;
  }
}

/**
 * A line for an exercise added in the gym: three sets, opening with the
 * numbers last logged for it (the packet's last-numbers table) or blank —
 * the same precedence the packet uses for a line with no prescribed weight,
 * minus a plan to prescribe one.
 */
export function addedLine(
  packet: Packet,
  exercise: Exercise,
  id: string,
): PacketLine {
  const last = packet.last_numbers.find((n) => n.exercise_id === exercise.id);
  const timed = last?.duration_s !== null && last?.duration_s !== undefined;
  return {
    line_ref: `${ADDED_PREFIX}${id}`,
    group_index: -1,
    item_index: 0,
    group_label: null,
    exercise_id: exercise.id,
    exercise_name: exercise.name,
    prescription: {
      exercise_id: exercise.id,
      sets: 3,
      reps: timed ? null : (last?.reps ?? null),
      duration_s: timed ? (last?.duration_s ?? null) : null,
      per_side: last?.per_side ? true : null,
      load:
        last?.load_kg !== null && last?.load_kg !== undefined
          ? { kind: "kg", value: last.load_kg }
          : { kind: "bodyweight", value: null },
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
    equipment: equipmentOf(exercise),
    prefill: last
      ? {
          source: "last_logged",
          load_kg: last.load_kg,
          reps: last.reps,
          duration_s: last.duration_s,
          starting_flow: false,
        }
      : {
          source: "blank",
          load_kg: null,
          reps: null,
          duration_s: null,
          starting_flow: true,
        },
    warmups: [],
    plates_per_side: null,
  };
}

/** Add `line` after the exercise the screen shows, and show it. */
export function addExercise(
  session: ActiveSession,
  lines: readonly PacketLine[],
  line: PacketLine,
): ActiveSession {
  const at = lines.length === 0 ? 0 : session.lineIndex + 1;
  const next = [...lines.slice(0, at), line, ...lines.slice(at)];
  return { ...session, lines: next, lineIndex: at, rest: null };
}

/**
 * Move logged sets to the exercise at `target`: they keep their client ids
 * and every number, and take the target's exercise and planned line. They go
 * after the target's own sets, in their logged order.
 */
export function moveSets(
  session: ActiveSession,
  lines: readonly PacketLine[],
  clientIds: readonly string[],
  target: number,
  stamp: Stamp,
): Edit {
  const line = lines[target];
  const moving = new Set(clientIds);
  const kept = session.sets.filter((set) => !moving.has(set.clientId));
  const moved = session.sets
    .filter((set) => moving.has(set.clientId))
    .map((set) => ({
      ...set,
      lineRef: line.line_ref,
      exerciseId: line.exercise_id,
    }));
  const sets = storedOrder([...kept, ...moved], lines);
  const patches = moved.map((set) =>
    patchMutation(
      session,
      set.clientId,
      {
        exercise_id: line.exercise_id,
        // Null, not omitted: a set moved onto an added exercise no longer
        // answers the planned line it was logged against.
        planned_line_ref: isPlannedLine(line.line_ref) ? line.line_ref : null,
        set_index: sets.findIndex((s) => s.clientId === set.clientId),
      },
      stamp,
    ),
  );
  return {
    session: { ...session, sets },
    mutations: [
      ...patches,
      ...renumber(session, session.sets, sets, stamp, moving),
    ],
  };
}
