import { describe, expect, it } from "vitest";

import {
  blankCriterion,
  CRITERION_KIND_LABELS,
  criterionKindsFor,
  describeCriterion,
  type SuccessCriterion,
} from "@/lib/criteria";

describe("describeCriterion", () => {
  it("phrases a time-in-band criterion in terms of the steps it selects", () => {
    const criterion: SuccessCriterion = {
      kind: "time_in_band",
      band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
      min_fraction: 0.75,
      selector: { kind: "role", role: "work", index: null },
    };
    expect(describeCriterion(criterion)).toBe(
      "75% of the work steps' time within 95%–105% of the prescribed power",
    );
  });

  it("names the whole session when the selector is `all`", () => {
    expect(
      describeCriterion({
        kind: "time_in_band",
        band: { channel: "hr", low: 0.9, high: 1.1, smoothing_s: 30 },
        min_fraction: 0.8,
        selector: { kind: "all", role: null, index: null },
      }),
    ).toContain("the session's time");
  });

  it("counts an index selector from one, as an athlete would", () => {
    expect(
      describeCriterion({
        kind: "time_in_band",
        band: { channel: "power", low: 0.98, high: 1.02, smoothing_s: 30 },
        min_fraction: 0.9,
        selector: { kind: "index", role: null, index: 0 },
      }),
    ).toContain("step 1's time");
  });

  it("renders a duration floor as a clock reading", () => {
    expect(
      describeCriterion({ kind: "duration_floor", min_seconds: 3600 }),
    ).toBe("Lasts at least 1:00:00");
  });

  it("renders both kinds of ceiling limit", () => {
    expect(
      describeCriterion({
        kind: "ceiling",
        channel: "power",
        limit: { kind: "percent_of_anchor", anchor_type: "ftp", pct: 1.05 },
        max_seconds_above: 360,
        smoothing_s: 0,
      }),
    ).toBe("No more than 6:00 with power above 105% of FTP");

    expect(
      describeCriterion({
        kind: "ceiling",
        channel: "hr",
        limit: { kind: "absolute", unit: "bpm", value: 178 },
        max_seconds_above: 300,
        smoothing_s: 0,
      }),
    ).toBe("No more than 5:00 with heart rate above 178 bpm");
  });

  it("renders the two strength criteria", () => {
    expect(
      describeCriterion({ kind: "sets_completed", min_fraction: 0.9 }),
    ).toBe("90% of the prescribed sets completed");
    expect(
      describeCriterion({ kind: "load_within", pct_tolerance: 0.05 }),
    ).toBe("Loads within 5% of what was prescribed");
  });
});

describe("criterionKindsFor", () => {
  it("offers each discipline only what it can be judged by", () => {
    expect(criterionKindsFor("cycling")).not.toContain("sets_completed");
    expect(criterionKindsFor("strength")).not.toContain("time_in_band");
    // A minimum duration means the same thing on a bike and in a gym.
    expect(criterionKindsFor("cycling")).toContain("duration_floor");
    expect(criterionKindsFor("strength")).toContain("duration_floor");
  });

  it("labels every kind it offers", () => {
    for (const kind of [
      ...criterionKindsFor("cycling"),
      ...criterionKindsFor("strength"),
    ]) {
      expect(CRITERION_KIND_LABELS[kind]).toBeTruthy();
    }
  });
});

describe("blankCriterion", () => {
  it("starts each kind at a rule, not at a tautology", () => {
    expect(describeCriterion(blankCriterion("time_in_band"))).toBe(
      "80% of the work steps' time within 95%\u2013105% of the prescribed power",
    );
    expect(describeCriterion(blankCriterion("sets_completed"))).toBe(
      "90% of the prescribed sets completed",
    );
    expect(blankCriterion("duration_floor")).toEqual({
      kind: "duration_floor",
      min_seconds: 3600,
    });
  });
});
