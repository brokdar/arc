import { describe, expect, it } from "vitest";

import type {
  ActiveSession,
  LoggedSet,
  PacketLine,
  PacketSession,
} from "@/components/strength/session-model";
import { nextDraft, stepDownAfter } from "@/components/strength/step-down";
import { DEFAULT_GYM } from "@/lib/strength";
import { athleteToday } from "@/tests/mocks/fixtures";
import { strengthPacket } from "@/tests/mocks/handlers-strength";

/**
 * AC-74: a working set logged two or more reps below the plan lowers the
 * exercise's remaining sets by one loadable step. The plan is the AC's own:
 * a back squat, 4 × 8 at 100 kg, in the packet the server would send for it.
 */

function plan(
  options: { followExactly?: boolean; kg?: number } = {},
): PacketSession {
  const packet = strengthPacket(athleteToday(), new Date(), {
    barbell: { kg: options.kg ?? 100, sets: 4, reps: 8 },
    followExactly: options.followExactly ?? false,
  });
  return packet.sessions[0];
}

function fresh(planned: PacketSession): ActiveSession {
  return {
    plannedSessionId: planned.planned_session_id,
    sessionClientId: "0199c000-0000-7000-8000-00000000c001",
    startedAt: 0,
    sets: [],
    lineIndex: 0,
    rest: null,
    finished: false,
  };
}

/** Logs the line's next set the way the screen does: its draft, maybe edited. */
function log(
  session: ActiveSession,
  planned: PacketSession,
  change: { reps?: number; loadKg?: number } = {},
): ActiveSession {
  const line = planned.lines[0];
  const draft = nextDraft(session, planned, line, DEFAULT_GYM);
  const set: LoggedSet = {
    clientId: `set-${session.sets.length + 1}`,
    lineRef: line.line_ref,
    exerciseId: line.exercise_id,
    warmup: false,
    loadKg: change.loadKg ?? draft.loadKg,
    reps: change.reps ?? draft.reps,
    durationS: null,
    perSide: false,
    rir: null,
    rirCapped: false,
    effortSimple: null,
  };
  return { ...session, sets: [...session.sets, set] };
}

const squat = (planned: PacketSession): PacketLine => planned.lines[0];
const draftOf = (session: ActiveSession, planned: PacketSession) =>
  nextDraft(session, planned, squat(planned), DEFAULT_GYM);
const loads = (session: ActiveSession) => session.sets.map((s) => s.loadKg);

describe("AC-74: a missed set steps the rest down", () => {
  it("set 2 at 6 of 8 reps: sets 3 and 4 open at 97.5 kg", () => {
    const planned = plan();
    let session = log(fresh(planned), planned);
    expect(draftOf(session, planned)).toMatchObject({ loadKg: 100, reps: 8 });
    session = log(session, planned, { reps: 6 });

    expect(draftOf(session, planned)).toEqual({
      loadKg: 97.5,
      reps: 8,
      durationS: null,
    });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toEqual({ fromKg: 100, toKg: 97.5 });

    // Set 3 logged as it opened (97.5 × 8): set 4 opens at 97.5 too.
    session = log(session, planned);
    expect(session.sets[2]).toMatchObject({ loadKg: 97.5, reps: 8 });
    expect(draftOf(session, planned)).toMatchObject({ loadKg: 97.5, reps: 8 });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toBeNull();
  });

  it("set 2 at 7 of 8 reps changes nothing", () => {
    const planned = plan();
    let session = log(fresh(planned), planned);
    session = log(session, planned, { reps: 7 });

    expect(draftOf(session, planned)).toEqual({
      loadKg: 100,
      reps: 7,
      durationS: null,
    });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toBeNull();
  });

  it("never changes a set already logged", () => {
    const planned = plan();
    let session = log(fresh(planned), planned);
    session = log(session, planned, { reps: 6 });
    const before = structuredClone(session.sets);

    draftOf(session, planned);
    stepDownAfter(session, planned, squat(planned), DEFAULT_GYM);
    session = log(session, planned);

    expect(session.sets.slice(0, 2)).toEqual(before);
    expect(loads(session)).toEqual([100, 100, 97.5]);
  });

  it("on a follow-exactly line nothing changes", () => {
    const planned = plan({ followExactly: true });
    let session = log(fresh(planned), planned);
    session = log(session, planned, { reps: 6 });

    expect(draftOf(session, planned)).toMatchObject({ loadKg: 100 });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toBeNull();
  });

  it("two misses lower one step each", () => {
    const planned = plan();
    let session = log(fresh(planned), planned, { reps: 6 });
    expect(draftOf(session, planned)).toMatchObject({ loadKg: 97.5 });
    session = log(session, planned, { reps: 5 });

    expect(draftOf(session, planned)).toMatchObject({ loadKg: 95, reps: 8 });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toEqual({ fromKg: 97.5, toKg: 95 });
    expect(loads(session)).toEqual([100, 97.5]);
  });

  it("never steps below the bar", () => {
    const planned = plan({ kg: 22.5 });
    let session = log(fresh(planned), planned, { reps: 5 });
    expect(draftOf(session, planned)).toMatchObject({ loadKg: 20 });
    session = log(session, planned, { reps: 5 });

    // At the empty bar there is nothing lighter: the next set stays at 20 kg
    // and no "lighter" reason is shown, because nothing got lighter.
    expect(draftOf(session, planned)).toMatchObject({ loadKg: 20 });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toBeNull();
  });

  it("never raises a weight: a miss below the bar keeps its own weight", () => {
    const planned = plan();
    // Typed by hand, lighter than anything the bar can load.
    const session = log(fresh(planned), planned, { reps: 3, loadKg: 15 });

    expect(draftOf(session, planned)).toMatchObject({ loadKg: 15 });
    expect(
      stepDownAfter(session, planned, squat(planned), DEFAULT_GYM),
    ).toBeNull();
  });

  it("a warm-up short of the plan's reps is not a miss", () => {
    const planned = plan();
    const first = log(fresh(planned), planned);
    const session: ActiveSession = {
      ...first,
      sets: [
        ...first.sets,
        { ...first.sets[0], clientId: "w", warmup: true, loadKg: 60, reps: 3 },
      ],
    };

    expect(draftOf(session, planned)).toMatchObject({ loadKg: 100 });
  });

  it("a rep range misses against its low end", () => {
    const planned = plan();
    const ranged: PacketSession = {
      ...planned,
      lines: [
        {
          ...squat(planned),
          prescription: { ...squat(planned).prescription, reps: [6, 8] },
          prefill: { ...squat(planned).prefill, reps: 6 },
        },
        ...planned.lines.slice(1),
      ],
    };
    const atFive = log(fresh(ranged), ranged, { reps: 5 });
    expect(draftOf(atFive, ranged)).toMatchObject({ loadKg: 100 });
    const atFour = log(fresh(ranged), ranged, { reps: 4 });
    expect(draftOf(atFour, ranged)).toMatchObject({ loadKg: 97.5, reps: 6 });
  });
});
