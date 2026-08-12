import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import openapi from "@/generated/api/openapi.json";
import {
  AXIS_LABELS,
  AXIS_QUESTIONS,
  COMPLETION_TONES,
  type CompletionState,
  CRITERION_LABELS,
  MAX_REASONS,
  REASON_LABELS,
  REASON_ORDER,
  reasonsProblem,
  resolveAxis,
  VERDICT_HINTS,
  VERDICT_LABELS,
  VERDICT_ORDER,
} from "@/lib/scoring";
import { contrastRatio, deltaE00, parseHex } from "@/tests/colour";

/** The vocabulary as the committed contract states it, never as restated here. */
function enumValues(schemaName: string): string[] {
  const schemas = (
    openapi as unknown as {
      components: { schemas: Record<string, { enum?: string[] }> };
    }
  ).components.schemas;
  const values = schemas[schemaName]?.enum;
  if (!values) {
    throw new Error(
      `${schemaName} is not an enum in the committed OpenAPI schema`,
    );
  }
  return values;
}

describe("the WP-7 vocabularies", () => {
  it("labels every completion state", () => {
    expect(Object.keys(COMPLETION_TONES).sort()).toEqual(
      enumValues("CompletionState").sort(),
    );
  });

  it("labels and explains every verdict, and offers all of them", () => {
    const verdicts = enumValues("Verdict");
    expect(Object.keys(VERDICT_LABELS).sort()).toEqual([...verdicts].sort());
    expect(Object.keys(VERDICT_HINTS).sort()).toEqual([...verdicts].sort());
    expect([...VERDICT_ORDER].sort()).toEqual([...verdicts].sort());
  });

  it("offers the whole controlled reason list, once each", () => {
    const reasons = enumValues("Reason");
    expect([...REASON_ORDER].sort()).toEqual([...reasons].sort());
    expect(new Set(REASON_ORDER).size).toBe(REASON_ORDER.length);
    expect(Object.keys(REASON_LABELS).sort()).toEqual([...reasons].sort());
  });

  it("names every axis and every criterion kind", () => {
    expect(Object.keys(AXIS_LABELS).sort()).toEqual(
      enumValues("ScoringAxis").sort(),
    );
    expect(Object.keys(AXIS_QUESTIONS).sort()).toEqual(
      enumValues("ScoringAxis").sort(),
    );
    expect(Object.keys(CRITERION_LABELS).sort()).toEqual(
      enumValues("CriterionKind").sort(),
    );
  });
});

/**
 * The strip's palette, measured rather than remembered.
 *
 * Purple belongs to the coach and to the over-target verdict and to nothing
 * else (build-plan invariant 7) — so `over` is the only state allowed to
 * wear it, and every other tone has to stay ΔE00 10 clear. The states also
 * have to be told apart *from each other*: a strip whose `missed` and
 * `abandoned` read as the same red would be a strip that says nothing.
 */
const CARD = "#131519";
const RESERVED = {
  "--color-coach": "#B49BFF",
  "--color-coach-strong": "#C7B6FF",
  "--color-status-over": "#A78BFA",
} as const;
const MIN_RESERVED_DISTANCE = 10;
const MIN_STATE_DISTANCE = 8;

/** The palette itself, read out of the one file allowed to name a colour. */
function paletteHex(token: string): string {
  const css = readGlobals();
  const found = new RegExp(`${token}:\\s*(#[0-9a-fA-F]{3,8})`).exec(css);
  if (!found) {
    throw new Error(`${token} is not defined in app/globals.css`);
  }
  return found[1];
}

let globals: string | null = null;
function readGlobals(): string {
  if (globals === null) {
    // Read lazily and once: the palette is a file, and a test that re-read it
    // per case would be measuring the filesystem.
    globals = readFileSync(
      join(resolve(import.meta.dirname, ".."), "app/globals.css"),
      "utf8",
    );
  }
  return globals;
}

/** Every state's tone, resolved from `var(--token)` to the hex behind it. */
function toneHexes(): Map<CompletionState, string> {
  const resolved = new Map<CompletionState, string>();
  for (const [state, tone] of Object.entries(COMPLETION_TONES)) {
    const token = /var\((--[a-z-]+)\)/.exec(tone.color)?.[1];
    if (!token) {
      throw new Error(`${state} names a colour instead of a token`);
    }
    resolved.set(state as CompletionState, paletteHex(token));
  }
  return resolved;
}

describe("the completion-state palette", () => {
  const hexes = toneHexes();

  it("takes every colour from a token in globals.css", () => {
    for (const hex of hexes.values()) {
      expect(() => parseHex(hex)).not.toThrow();
    }
  });

  it.each([...hexes])("draws %s legibly on a card", (_state, hex) => {
    // 3:1 is WCAG AA for a graphical object, which a 6px dot and a 3px bar
    // both are.
    expect(contrastRatio(hex, CARD)).toBeGreaterThanOrEqual(3);
  });

  it.each([...hexes].filter(([state]) => state !== "over"))(
    "keeps %s clear of the reserved purples",
    (state, hex) => {
      for (const [token, reserved] of Object.entries(RESERVED)) {
        const distance = deltaE00(hex, reserved);
        expect(
          distance,
          `${state} ${hex} is ΔE00 ${distance.toFixed(2)} from ${token} (${reserved}) — purple is reserved for interpretive content`,
        ).toBeGreaterThan(MIN_RESERVED_DISTANCE);
      }
    },
  );

  it("tells the states apart from each other", () => {
    const entries = [...hexes];
    for (let left = 0; left < entries.length; left += 1) {
      for (let right = left + 1; right < entries.length; right += 1) {
        const [leftState, leftHex] = entries[left];
        const [rightState, rightHex] = entries[right];
        if (leftHex === rightHex) {
          // The one deliberate sharing: `displaced` and `different_session`
          // are the same statement from two writers, so they are one colour.
          // Any other pair sharing a hex is an accident.
          expect(new Set([leftState, rightState])).toEqual(
            new Set(["displaced", "different_session"]),
          );
          continue;
        }
        const distance = deltaE00(leftHex, rightHex);
        expect(
          distance,
          `${leftState} and ${rightState} are ΔE00 ${distance.toFixed(2)} apart`,
        ).toBeGreaterThan(MIN_STATE_DISTANCE);
      }
    }
  });
});

describe("resolveAxis", () => {
  it("narrows a scored axis to its number and its explanation", () => {
    const resolved = resolveAxis({
      axis: "completion",
      value: 0.75,
      explanation: {
        formula: "completed / prescribed",
        inputs: {},
        assumptions: [],
        citation: null,
      },
      not_assessed: null,
      criteria: [],
    });
    expect(resolved).toEqual({
      kind: "value",
      value: 0.75,
      explanation: expect.objectContaining({
        formula: "completed / prescribed",
      }),
    });
  });

  it("reads a refused axis as absent with its reason, never as zero", () => {
    expect(
      resolveAxis({
        axis: "adherence",
        value: null,
        explanation: null,
        not_assessed: "no power was recorded",
        criteria: [],
      }),
    ).toEqual({ kind: "absent", reason: "no power was recorded" });
  });

  it("reads an axis that claims neither as absent", () => {
    expect(
      resolveAxis({
        axis: "pacing",
        value: null,
        explanation: null,
        not_assessed: null,
        criteria: [],
      }).kind,
    ).toBe("absent");
  });
});

describe("the reason rule", () => {
  it("lets an as-intended declaration carry nothing", () => {
    expect(reasonsProblem("as_intended", [])).toBeNull();
  });

  it("demands at least one reason for anything else", () => {
    expect(reasonsProblem("under", [])).toMatch(/at least one reason/);
    expect(reasonsProblem("under", ["time"])).toBeNull();
  });

  it("refuses a fourth reason", () => {
    expect(
      reasonsProblem("under", ["time", "weather", "heat", "traffic"]),
    ).toMatch(new RegExp(`at most ${MAX_REASONS}`));
  });

  it("refuses the same reason twice", () => {
    expect(reasonsProblem("under", ["time", "time"])).toMatch(/once/);
  });
});
