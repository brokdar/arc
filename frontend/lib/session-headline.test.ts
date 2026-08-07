import { describe, expect, it } from "vitest";

import { sessionHeadline } from "@/lib/session-headline";
import type {
  EnduranceStructure,
  SteadyStep,
  StrengthStructure,
} from "@/lib/workout-profile";

function steady(
  role: "warmup" | "work" | "rest" | "cooldown",
  durationS: number,
  pct?: [number, number],
): SteadyStep {
  return {
    kind: "steady" as const,
    role,
    name: null,
    duration_s: durationS,
    distance_m: null,
    targets: pct
      ? {
          power: {
            kind: "percent_of_anchor" as const,
            anchor_type: "ftp" as const,
            pct_low: pct[0],
            pct_high: pct[1],
          },
        }
      : {},
  };
}

const LONG_RIDE: EnduranceStructure = {
  discipline: "cycling",
  steps: [
    steady("warmup", 900, [0.5, 0.6]),
    steady("work", 9600, [0.62, 0.72]),
    steady("cooldown", 900, [0.4, 0.5]),
  ],
};

const VO2: EnduranceStructure = {
  discipline: "cycling",
  steps: [
    steady("warmup", 720, [0.5, 0.6]),
    {
      kind: "repeat",
      times: 5,
      children: [
        steady("work", 240, [1.14, 1.22]),
        steady("rest", 180, [0.4, 0.5]),
      ],
    },
    steady("cooldown", 600),
  ],
};

const LOWER: StrengthStructure = {
  discipline: "strength",
  groups: [
    {
      label: null,
      items: [
        {
          exercise_id: "back_squat",
          sets: 4,
          reps: 5,
          load: { kind: "percent_e1rm", value: 0.82 },
          rir: 2,
          rest_s: 180,
          tempo: null,
          notes: null,
        },
      ],
    },
    {
      label: "Superset A",
      items: [
        {
          exercise_id: "romanian_deadlift",
          sets: 3,
          reps: 8,
          load: { kind: "kg", value: 80 },
          rir: 2,
          rest_s: 90,
          tempo: null,
          notes: null,
        },
        {
          exercise_id: "hanging_leg_raise",
          sets: 3,
          reps: 12,
          load: { kind: "bodyweight", value: null },
          rir: null,
          rest_s: 60,
          tempo: null,
          notes: null,
        },
      ],
    },
  ],
};

describe("sessionHeadline", () => {
  it("says what a long endurance ride is, the way the mockup does", () => {
    expect(
      sessionHeadline({
        purpose: "endurance",
        structure: LONG_RIDE,
        plannedDurationS: 11400,
      }),
    ).toBe("3h10 endurance ride — steady Z2");
  });

  it("describes an interval session by its intervals", () => {
    expect(
      sessionHeadline({
        purpose: "vo2max",
        structure: VO2,
        plannedDurationS: 4140,
      }),
    ).toBe("1h09 VO₂max ride — 5×4′ at Z5");
  });

  it("keeps the vocabulary's own casing mid-sentence", () => {
    expect(
      sessionHeadline({
        purpose: "sweet_spot",
        structure: LONG_RIDE,
        plannedDurationS: 3600,
      }),
    ).toMatch(/^1h sweet spot ride/);
  });

  it("counts sets and movements for a lifting session", () => {
    expect(
      sessionHeadline({
        purpose: "max_strength",
        structure: LOWER,
        totalSets: 10,
      }),
    ).toBe("10 sets of max strength — 3 movements, one superset");
  });

  it("falls back a clause at a time when the prescription is thin", () => {
    expect(
      sessionHeadline({
        purpose: "recovery",
        structure: null,
        plannedDurationS: 2700,
      }),
    ).toBe("45min recovery ride");
    expect(sessionHeadline({ purpose: "recovery", structure: null })).toBe(
      "recovery ride",
    );
    expect(sessionHeadline({ purpose: "core", structure: null })).toBe(
      "core session",
    );
  });

  it("calls a ride with no dominant band mixed, naming both ends", () => {
    const mixed: EnduranceStructure = {
      discipline: "cycling",
      steps: [
        steady("warmup", 600, [0.5, 0.55]),
        steady("work", 600, [0.85, 0.88]),
        steady("work", 600, [1.0, 1.05]),
      ],
    };

    // The warm-up's midpoint is 52.5 % FTP, which is Z1 on the backend's
    // coggan_7 ramp (Z2 opens at 55 %) — the old display ramp called it Z2.
    expect(
      sessionHeadline({
        purpose: "tempo",
        structure: mixed,
        plannedDurationS: 1800,
      }),
    ).toBe("30min tempo ride — mixed Z1–Z4");
  });
});
