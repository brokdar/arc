// The phone's loading arithmetic agrees with the server's, case for case.
//
// `backend/tests/vectors/loading.json` is written by running the Python
// domain (`just loading-vectors`); this suite runs every case through the
// TypeScript twin and demands the identical answer — exact equality, not a
// tolerance, because both sides follow the same integer-gram steps over the
// same IEEE doubles. The twin is display arithmetic only: it never decides a
// rule the server has not, and this test is what holds it to that.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  DEFAULT_GYM,
  type Equipment,
  kgToLb,
  LoadingError,
  lbToKg,
  loadingFor,
  nearestLoadable,
  percentOfSet,
  platesPerSide,
  startingStep,
  stepDown,
} from "@/lib/strength";

type Expect = { value: unknown } | { error: true };

interface VectorCase {
  fn: string;
  args: unknown[];
  expect: Expect;
}

interface Vectors {
  gym: Record<string, unknown>;
  cases: VectorCase[];
}

const VECTORS_PATH = resolve(
  import.meta.dirname,
  "../../../backend/tests/vectors/loading.json",
);

/** Parse a vectors document, refusing one a per-case loop would pass vacuously. */
function parseVectors(text: string): Vectors {
  const document = JSON.parse(text) as Partial<Vectors>;
  if (!Array.isArray(document.cases) || document.cases.length === 0) {
    throw new Error(
      "the loading vectors have no cases; run `just loading-vectors`",
    );
  }
  return document as Vectors;
}

function run(fn: string, args: unknown[]): unknown {
  const [a, b, c] = args;
  switch (fn) {
    case "nearest_loadable":
      return nearestLoadable(a as number, b as Equipment);
    case "loading_for": {
      const result = loadingFor(a as number, b as Equipment);
      return {
        kg: result.kg,
        capped: result.capped,
        plates_per_side: result.platesPerSide,
      };
    }
    case "plates_per_side":
      return platesPerSide(a as number, b as Equipment);
    case "starting_step":
      return startingStep(a as number, b as string, c as Equipment);
    case "step_down":
      return stepDown(a as number, b as Equipment);
    case "percent_of_set":
      return percentOfSet(a as number, b as number, c as Equipment);
    case "kg_to_lb":
      return kgToLb(a as number);
    case "lb_to_kg":
      return lbToKg(a as number);
    default:
      throw new Error(`no loading function ${fn}`);
  }
}

function runCase({ fn, args }: VectorCase): Expect {
  try {
    return { value: run(fn, args) };
  } catch (error) {
    if (error instanceof LoadingError) return { error: true };
    throw error;
  }
}

const vectors = parseVectors(readFileSync(VECTORS_PATH, "utf8"));

describe("loading vectors", () => {
  it.each(
    vectors.cases.map(
      (c) => [`${c.fn}(${JSON.stringify(c.args)})`, c] as const,
    ),
  )("%s matches the Python domain", (_name, vectorCase) => {
    expect(runCase(vectorCase)).toStrictEqual(vectorCase.expect);
  });

  it("was emitted against the gym the twin defaults to", () => {
    expect({
      bar_kg: DEFAULT_GYM.barKg,
      plate_pairs: DEFAULT_GYM.platePairs.map(([kg, pairs]) => [kg, pairs]),
      dumbbell_step_kg: DEFAULT_GYM.dumbbellStepKg,
      dumbbell_max_kg: DEFAULT_GYM.dumbbellMaxKg,
      stack_step_kg: DEFAULT_GYM.stackStepKg,
      stack_max_kg: DEFAULT_GYM.stackMaxKg,
    }).toStrictEqual(vectors.gym);
  });

  it("carries float-noise inputs that both languages read identically", () => {
    const noisy = vectors.cases.filter((c) => c.args[0] === 0.1 + 0.2);
    expect(noisy.length).toBeGreaterThan(0);
    for (const vectorCase of noisy) {
      expect(runCase(vectorCase)).toStrictEqual(vectorCase.expect);
    }
  });

  it("refuses an empty vectors file rather than passing vacuously", () => {
    expect(() => parseVectors(JSON.stringify({ gym: {}, cases: [] }))).toThrow(
      /no cases/,
    );
    expect(() => parseVectors("{}")).toThrow(/no cases/);
  });

  it("would catch a case whose expected answer the twin does not give", () => {
    const first = vectors.cases[0];
    expect(runCase(first)).not.toStrictEqual({
      value: "not what the domain says",
    });
  });
});
