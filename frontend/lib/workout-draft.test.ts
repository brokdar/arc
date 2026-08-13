import { describe, expect, it } from "vitest";

import {
  blankRepeatBlock,
  blankSteadyStep,
  blankStrengthGroup,
  blankStrengthItem,
  blankTarget,
  type DraftRepeatBlock,
  type DraftStep,
  draftFromStructure,
  emptyDraft,
  findStep,
  insertStep,
  moveStep,
  removeStep,
  repeatDepth,
  replaceStep,
  structureFromDraft,
  validateDraft,
} from "@/lib/workout-draft";

function steady(overrides: Partial<ReturnType<typeof blankSteadyStep>> = {}) {
  return { ...blankSteadyStep(), duration: "4:00", ...overrides };
}

describe("tree edits", () => {
  it("appends a step at the top level", () => {
    const first = steady();
    const second = steady();

    expect(insertStep([first], null, second).map((s) => s.id)).toEqual([
      first.id,
      second.id,
    ]);
  });

  it("nests a step inside the repeat block it names", () => {
    const repeat = blankRepeatBlock();
    const child = steady();

    const steps = insertStep([repeat], repeat.id, child);
    const block = steps[0] as DraftRepeatBlock;

    expect(block.children.at(-1)?.id).toBe(child.id);
    expect(findStep(steps, child.id)?.id).toBe(child.id);
  });

  it("nests two deep, and reports how deep a block sits", () => {
    const outer = blankRepeatBlock();
    const inner = blankRepeatBlock();

    const steps = insertStep([outer], outer.id, inner);

    expect(repeatDepth(steps, outer.id)).toBe(1);
    expect(repeatDepth(steps, inner.id)).toBe(2);
  });

  it("removes a nested step without disturbing its siblings", () => {
    const repeat = blankRepeatBlock();
    const [work, rest] = repeat.children as DraftStep[];

    const steps = removeStep([repeat], work?.id ?? "");
    const block = steps[0] as DraftRepeatBlock;

    expect(block.children.map((child) => child.id)).toEqual([rest?.id]);
  });

  it("replaces a nested step in place", () => {
    const repeat = blankRepeatBlock();
    const work = repeat.children[0] as DraftStep;
    const edited = { ...work, name: "VO₂ block" } as DraftStep;

    const steps = replaceStep([repeat], work.id, edited);

    expect(findStep(steps, work.id)).toMatchObject({ name: "VO₂ block" });
  });

  it("reorders within a parent, and refuses to walk off either end", () => {
    const a = steady();
    const b = steady();
    const c = steady();

    expect(moveStep([a, b, c], c.id, -1).map((s) => s.id)).toEqual([
      a.id,
      c.id,
      b.id,
    ]);
    expect(moveStep([a, b, c], a.id, -1).map((s) => s.id)).toEqual([
      a.id,
      b.id,
      c.id,
    ]);
  });

  it("reorders inside a repeat without lifting the step out of it", () => {
    const repeat = blankRepeatBlock();
    const [work, rest] = repeat.children as DraftStep[];

    const steps = moveStep([repeat], rest?.id ?? "", -1);
    const block = steps[0] as DraftRepeatBlock;

    expect(block.children.map((child) => child.id)).toEqual([
      rest?.id,
      work?.id,
    ]);
    expect(steps).toHaveLength(1);
  });
});

describe("structureFromDraft", () => {
  it("sends a percentage target as fractions of the named anchor", () => {
    const step = steady({
      targets: [{ ...blankTarget("power"), low: "95", high: "105" }],
    });

    const structure = structureFromDraft({
      discipline: "cycling",
      steps: [step],
    });

    expect(structure).toEqual({
      discipline: "cycling",
      steps: [
        {
          kind: "steady",
          role: "work",
          name: null,
          duration_s: 240,
          distance_m: null,
          targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 0.95,
              pct_high: 1.05,
            },
          },
        },
      ],
    });
  });

  it("sends an absolute target in the channel's own unit", () => {
    const step = steady({
      targets: [
        { ...blankTarget("power"), mode: "absolute", low: "220", high: "245" },
        { ...blankTarget("cadence"), low: "85", high: "95" },
      ],
    });

    const structure = structureFromDraft({
      discipline: "cycling",
      steps: [step],
    });
    const targets =
      structure.discipline === "cycling"
        ? (structure.steps[0] as { targets: Record<string, unknown> }).targets
        : {};

    expect(targets.power).toEqual({
      kind: "absolute",
      unit: "W",
      low: 220,
      high: 245,
    });
    // Cadence has no anchor, so `blankTarget` starts it absolute already.
    expect(targets.cadence).toEqual({
      kind: "absolute",
      unit: "rpm",
      low: 85,
      high: 95,
    });
  });

  it("keeps drawing while a number is half typed", () => {
    const step = steady({
      duration: "",
      targets: [{ ...blankTarget("power"), low: "9", high: "" }],
    });

    const structure = structureFromDraft({
      discipline: "cycling",
      steps: [step],
    });
    const first =
      structure.discipline === "cycling"
        ? (structure.steps[0] as { duration_s: number | null; targets: object })
        : null;

    expect(first?.duration_s).toBeNull();
    expect(first?.targets).toEqual({});
  });

  it("converts a distance step from km to metres", () => {
    const step = steady({
      extent: "distance",
      distanceKm: "12.5",
      duration: "",
    });

    const structure = structureFromDraft({
      discipline: "cycling",
      steps: [step],
    });
    const first =
      structure.discipline === "cycling"
        ? (structure.steps[0] as { distance_m: number | null })
        : null;

    expect(first?.distance_m).toBe(12500);
  });

  it("sends a %e1RM load as a fraction and a bodyweight load with no value", () => {
    const draft = {
      discipline: "strength" as const,
      groups: [
        {
          ...blankStrengthGroup(),
          label: "Superset A",
          items: [
            {
              ...blankStrengthItem("back_squat"),
              sets: "4",
              reps: "5",
              loadKind: "percent_e1rm" as const,
              loadValue: "82",
              rir: "2",
            },
            {
              ...blankStrengthItem("hanging_leg_raise"),
              loadKind: "bodyweight" as const,
              loadValue: "",
            },
          ],
        },
      ],
    };

    const structure = structureFromDraft(draft);

    expect(structure).toMatchObject({
      discipline: "strength",
      groups: [
        {
          label: "Superset A",
          items: [
            {
              exercise_id: "back_squat",
              sets: 4,
              reps: 5,
              load: { kind: "percent_e1rm", value: 0.82 },
              rir: 2,
            },
            {
              exercise_id: "hanging_leg_raise",
              load: { kind: "bodyweight", value: null },
            },
          ],
        },
      ],
    });
  });
});

describe("draftFromStructure", () => {
  it("round-trips a repeat block with a percentage target", () => {
    const structure = structureFromDraft({
      discipline: "cycling",
      steps: [
        {
          ...blankRepeatBlock(),
          times: "5",
          children: [
            steady({
              duration: "4:00",
              targets: [{ ...blankTarget("power"), low: "114", high: "122" }],
            }),
          ],
        },
      ],
    });

    const draft = draftFromStructure(structure);
    const back = draft ? structureFromDraft(draft) : null;

    expect(back).toEqual(structure);
  });

  it("reads a %e1RM load back as whole percents", () => {
    const draft = draftFromStructure({
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
      ],
    });

    expect(draft?.discipline).toBe("strength");
    if (draft?.discipline === "strength") {
      expect(draft.groups[0]?.items[0]).toMatchObject({
        exerciseId: "back_squat",
        loadKind: "percent_e1rm",
        loadValue: "82",
        restS: "180",
      });
    }
  });

  it("round-trips a per-side round and a timed hold", () => {
    const structure = {
      discipline: "strength" as const,
      groups: [
        {
          label: null,
          items: [
            {
              exercise_id: "single_arm_dumbbell_row",
              sets: 3,
              reps: 11,
              duration_s: null,
              per_side: true,
              load: { kind: "kg" as const, value: 15 },
              rir: null,
              rest_s: null,
              tempo: null,
              notes: null,
            },
            {
              exercise_id: "front_plank",
              sets: 3,
              reps: null,
              duration_s: 45,
              per_side: null,
              load: { kind: "bodyweight" as const, value: null },
              rir: null,
              rest_s: null,
              tempo: null,
              notes: null,
            },
          ],
        },
      ],
    };

    const draft = draftFromStructure(structure);
    const back = draft ? structureFromDraft(draft) : null;

    expect(draft?.discipline).toBe("strength");
    if (draft?.discipline === "strength") {
      const [row, hold] = draft.groups[0]?.items ?? [];
      expect(row).toMatchObject({ mode: "reps", reps: "11", perSide: true });
      // A hold's rep box is empty rather than holding a `1` nobody typed.
      expect(hold).toMatchObject({ mode: "hold", reps: "", durationS: "45" });
    }
    expect(back).toEqual(structure);
  });
});

describe("validateDraft", () => {
  it("accepts a workout with one complete step", () => {
    expect(
      validateDraft({
        discipline: "cycling",
        steps: [
          steady({
            targets: [{ ...blankTarget("power"), low: "60", high: "70" }],
          }),
        ],
      }),
    ).toEqual([]);
  });

  it("refuses an empty workout", () => {
    expect(validateDraft(emptyDraft("cycling"))).toContain(
      "A workout needs at least one step.",
    );
  });

  it("names the step whose duration is missing", () => {
    const problems = validateDraft({
      discipline: "cycling",
      steps: [steady({ duration: "" })],
    });

    expect(problems).toContain("Step 1: give a duration, as mm:ss or minutes.");
  });

  it("refuses a percentage of an anchor the channel does not derive from", () => {
    const problems = validateDraft({
      discipline: "cycling",
      steps: [
        steady({
          targets: [
            {
              ...blankTarget("power"),
              anchorType: "lthr",
              low: "90",
              high: "95",
            },
          ],
        }),
      ],
    });

    expect(problems).toContain(
      "Step 1: power cannot be prescribed as a percentage of lthr.",
    );
  });

  it("refuses an absolute target outside the channel's plausible range", () => {
    const problems = validateDraft({
      discipline: "cycling",
      steps: [
        steady({
          targets: [
            { ...blankTarget("hr"), mode: "absolute", low: "10", high: "400" },
          ],
        }),
      ],
    });

    expect(problems).toContain("Step 1: hr must be between 25 and 230 bpm.");
  });

  it("refuses a ramp whose two ends are different kinds of target", () => {
    const problems = validateDraft({
      discipline: "cycling",
      steps: [
        {
          id: "ramp",
          kind: "ramp",
          role: "warmup",
          name: "",
          extent: "duration",
          duration: "10:00",
          distanceKm: "",
          startTargets: [{ ...blankTarget("power"), low: "50", high: "55" }],
          endTargets: [
            {
              ...blankTarget("power"),
              mode: "absolute",
              low: "200",
              high: "210",
            },
          ],
        },
      ],
    });

    expect(problems).toContain(
      "Step 1: a ramp's two ends must be the same kind of target.",
    );
  });

  it("refuses a strength line with no movement chosen", () => {
    const problems = validateDraft({
      discipline: "strength",
      groups: [blankStrengthGroup()],
    });

    expect(problems).toContain("Group 1: choose an exercise.");
  });

  it("insists a non-bodyweight load carries a value", () => {
    const problems = validateDraft({
      discipline: "strength",
      groups: [
        {
          ...blankStrengthGroup(),
          items: [{ ...blankStrengthItem("back_squat"), loadValue: "" }],
        },
      ],
    });

    expect(problems).toContain(
      "Group 1 · back_squat: a kg load needs a value.",
    );
  });
});
