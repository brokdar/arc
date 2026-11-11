import {
  type ActiveSession,
  type Draft,
  draftFor,
  type LoggedSet,
  type PacketLine,
  type PacketSession,
  workingSetsOf,
} from "@/components/strength/session-model";
import { type Gym, stepDown } from "@/lib/strength";

/**
 * The step-down after a missed set: when a working set comes in two or more
 * reps short of the plan, the exercise's remaining sets open one loadable
 * step lighter (`lib/strength.stepDown`), with the reason on the row.
 *
 * **Why the phone decides this one.** The session packet is otherwise the
 * only source of training numbers (`backend/app/services/packet.py`: "the
 * phone renders, it does not decide"). This rule cannot be prepared ahead:
 * it answers a set the server has not seen yet, in a gym with no signal, and
 * it has to land before the next set starts. It touches only what the next
 * set *opens with* — a suggestion the athlete can still change with the
 * steppers — never a logged set, and nothing here reaches the server except
 * the sets the athlete then logs. Displaced: shipping a precomputed
 * step-down weight per line in the packet, which would still leave "was that
 * a miss?" to the phone and add a number to keep in step.
 *
 * **Derived, not stored.** The step-down is read off the logged sets every
 * time, so undoing the missed set, or editing its reps up before the next
 * set is logged, takes the step-down back with it. Once a lighter set is
 * logged, the next one opens at that set's weight as every set does
 * (`draftFor`), which is what keeps "the remaining sets" lighter without a
 * flag: the step is taken from the last set's weight, so a second miss
 * lowers one step further and a run of good sets never climbs back up.
 *
 * What counts as a miss:
 * - a working set (a warm-up is light on purpose);
 * - counted in reps, against the prescription's reps — a range's low end,
 *   the number every set must reach, as the packet pre-fills it. A timed
 *   hold, or a line whose prescription names no reps, cannot miss;
 * - on a line with loadable equipment and a logged weight: without the
 *   gym's steps for the equipment there is no "one step lighter";
 * - never on a follow-exactly plan (a physio's programme), whose numbers are
 *   the prescription as written (`app.domain.plans`).
 *
 * Weights never rise mid-session: a step that would not land below the
 * missed set's own weight (the empty bar, or a weight typed below anything
 * the gym can load) is no step at all, and no reason is shown.
 */

/** How many reps short of the plan a working set must be to step down. */
export const MISS_REPS = 2;

export interface StepDown {
  /** The missed set's weight. */
  fromKg: number;
  /** What the remaining sets open with. */
  toKg: number;
}

/** The reps every set of `line` must reach, or null when it names none. */
function plannedReps(line: PacketLine): number | null {
  const { reps } = line.prescription;
  if (reps === null || reps === undefined) return null;
  return Array.isArray(reps) ? reps[0] : reps;
}

function missed(set: LoggedSet, line: PacketLine): boolean {
  const target = plannedReps(line);
  return (
    !set.warmup &&
    target !== null &&
    set.reps !== null &&
    set.durationS === null &&
    set.reps <= target - MISS_REPS
  );
}

/** The step-down the line's last working set calls for, if any. */
export function stepDownAfter(
  session: ActiveSession,
  planned: PacketSession,
  line: PacketLine,
  gym: Gym,
): StepDown | null {
  if (planned.follow_exactly || !line.equipment) return null;
  const done = workingSetsOf(session, line.line_ref);
  const last = done[done.length - 1];
  if (!last || last.loadKg === null || !missed(last, line)) return null;
  const toKg = stepDown(last.loadKg, line.equipment, gym);
  return toKg < last.loadKg ? { fromKg: last.loadKg, toKg } : null;
}

/**
 * The numbers the line's next set opens with: `draftFor`, stepped down after
 * a miss. A stepped-down set opens at the planned reps again, not the missed
 * count — the weight came off so the plan can be met, and opening at the
 * miss would make the one-tap ✓ log another one.
 */
export function nextDraft(
  session: ActiveSession,
  planned: PacketSession,
  line: PacketLine,
  gym: Gym,
): Draft {
  const draft = draftFor(session, line);
  const step = stepDownAfter(session, planned, line, gym);
  if (!step) return draft;
  return {
    ...draft,
    loadKg: step.toKg,
    reps: plannedReps(line) ?? draft.reps,
  };
}
