import { describe, expect, it } from "vitest";

import type { components } from "@/generated/api/schema";
import {
  flattenSteps,
  profileBars,
  totalDurationS,
  totalSets,
  type WorkoutStep,
  zoneToneFor,
} from "@/lib/workout-profile";

type Schemas = components["schemas"];

function steady(
  role: Schemas["StepRole"],
  durationS: number,
  pct?: [number, number],
): Schemas["SteadyStepSchema"] {
  return {
    kind: "steady",
    role,
    name: null,
    duration_s: durationS,
    distance_m: null,
    targets: pct
      ? {
          power: {
            kind: "percent_of_anchor",
            anchor_type: "ftp",
            pct_low: pct[0],
            pct_high: pct[1],
          },
        }
      : {},
  };
}

describe("flattenSteps", () => {
  it("expands a repeat into one entry per rep", () => {
    const steps: WorkoutStep[] = [
      steady("warmup", 600, [0.5, 0.6]),
      {
        kind: "repeat",
        times: 3,
        children: [steady("work", 240, [1.1, 1.2]), steady("rest", 120)],
      },
    ];
    expect(flattenSteps(steps)).toHaveLength(1 + 3 * 2);
  });

  it("expands nested repeats", () => {
    const steps: WorkoutStep[] = [
      {
        kind: "repeat",
        times: 2,
        children: [
          {
            kind: "repeat",
            times: 3,
            children: [steady("work", 60, [1, 1.1])],
          },
        ],
      },
    ];
    expect(flattenSteps(steps)).toHaveLength(6);
  });
});

describe("zoneToneFor", () => {
  it("walks the ramp with intensity", () => {
    expect(zoneToneFor(0.3)).toBe("rest");
    expect(zoneToneFor(0.55)).toBe("z2");
    expect(zoneToneFor(0.8)).toBe("z3");
    expect(zoneToneFor(0.95)).toBe("z4");
    expect(zoneToneFor(1.18)).toBe("z5");
  });

  it("is monotonic across the boundaries", () => {
    const order = ["rest", "z2", "z3", "z4", "z5"];
    let previous = -1;
    for (let f = 0; f <= 1.5; f += 0.01) {
      const index = order.indexOf(zoneToneFor(f));
      expect(index).toBeGreaterThanOrEqual(previous);
      previous = index;
    }
  });
});

describe("profileBars", () => {
  const vo2: Schemas["EnduranceStructureSchema-Output"] = {
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

  it("draws one bar per flattened step", () => {
    expect(profileBars(vo2)).toHaveLength(1 + 10 + 1);
  });

  it("weights bars by duration, so a 4-minute block is wider than a 3", () => {
    const bars = profileBars(vo2);
    expect(bars[1]?.weight).toBe(240);
    expect(bars[2]?.weight).toBe(180);
  });

  it("colours the work blocks above the recoveries", () => {
    const bars = profileBars(vo2);
    expect(bars[1]?.zone).toBe("z5");
    expect(bars[2]?.zone).toBe("rest");
    expect(bars[0]?.zone).toBe("z2");
  });

  it("uses a fixed ceiling, so an easy ride reads as an easy ride", () => {
    const recovery: Schemas["EnduranceStructureSchema-Output"] = {
      discipline: "cycling",
      steps: [steady("work", 2700, [0.45, 0.5])],
    };
    const bar = profileBars(recovery)[0];
    // ~0.475 of FTP against a 1.25 ceiling — well under half height, not the
    // full-height bar a self-normalising plot would draw.
    expect(bar?.height).toBeLessThan(0.45);
  });

  it("widens the ceiling rather than clipping a sprint session", () => {
    const sprints: Schemas["EnduranceStructureSchema-Output"] = {
      discipline: "cycling",
      steps: [steady("work", 30, [2.0, 2.4])],
    };
    expect(profileBars(sprints)[0]?.height).toBeLessThanOrEqual(1);
  });

  it("falls back to the step's role when it prescribes nothing", () => {
    const bars = profileBars(vo2);
    // The cool-down has no targets at all; it still gets a visible bar.
    expect(bars.at(-1)?.height).toBeGreaterThan(0);
    expect(bars.at(-1)?.zone).toBe("rest");
  });

  it("normalises absolute targets against the workout's own hardest step", () => {
    const absolute: Schemas["EnduranceStructureSchema-Output"] = {
      discipline: "cycling",
      steps: [
        {
          ...steady("warmup", 600),
          targets: {
            power: { kind: "absolute", unit: "W", low: 120, high: 140 },
          },
        },
        {
          ...steady("work", 600),
          targets: {
            power: { kind: "absolute", unit: "W", low: 290, high: 310 },
          },
        },
      ],
    };
    const bars = profileBars(absolute);
    expect(bars[1]?.height).toBe(1);
    expect(bars[0]?.height).toBeLessThan(bars[1]?.height ?? 0);
  });

  it("draws a ramp as one bar at its midpoint", () => {
    const ramp: Schemas["EnduranceStructureSchema-Output"] = {
      discipline: "cycling",
      steps: [
        {
          kind: "ramp",
          role: "work",
          name: null,
          duration_s: 600,
          distance_m: null,
          start_targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 0.5,
              pct_high: 0.5,
            },
          },
          end_targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 1.1,
              pct_high: 1.1,
            },
          },
        },
      ],
    };
    const bars = profileBars(ramp);
    expect(bars).toHaveLength(1);
    // (0.5 + 1.1) / 2 = 0.8 → Z3.
    expect(bars[0]?.zone).toBe("z3");
  });

  it("draws nothing for a strength prescription or an empty tree", () => {
    expect(profileBars({ discipline: "strength", groups: [] })).toHaveLength(0);
    expect(profileBars({ discipline: "cycling", steps: [] })).toHaveLength(0);
    expect(profileBars(null)).toHaveLength(0);
  });
});

describe("totals", () => {
  it("sums a step tree's seconds with repeats expanded", () => {
    expect(
      totalDurationS({
        discipline: "cycling",
        steps: [
          steady("warmup", 600, [0.5, 0.6]),
          {
            kind: "repeat",
            times: 4,
            children: [steady("work", 300, [0.95, 1]), steady("rest", 150)],
          },
        ],
      }),
    ).toBe(600 + 4 * 450);
  });

  it("sums strength sets across groups", () => {
    expect(
      totalSets({
        discipline: "strength",
        groups: [
          {
            label: null,
            items: [
              {
                exercise_id: "squat",
                sets: 4,
                reps: 5,
                load: { kind: "kg", value: 100 },
                rir: 2,
                rest_s: 180,
                tempo: null,
                notes: null,
              },
            ],
          },
          {
            label: null,
            items: [
              {
                exercise_id: "row",
                sets: 3,
                reps: 8,
                load: { kind: "bodyweight", value: null },
                rir: null,
                rest_s: 60,
                tempo: null,
                notes: null,
              },
            ],
          },
        ],
      }),
    ).toBe(7);
  });

  it("has no opinion about the other discipline", () => {
    expect(totalSets({ discipline: "cycling", steps: [] })).toBeNull();
    expect(totalDurationS({ discipline: "strength", groups: [] })).toBeNull();
  });
});
