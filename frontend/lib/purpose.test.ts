import { describe, expect, it } from "vitest";

import openapi from "@/generated/api/openapi.json";
import type { Purpose } from "@/lib/purpose";
import {
  disciplineOfPurpose,
  PURPOSE_TONES,
  purposeInSentence,
  purposeLabel,
  purposesFor,
  STATUS_TONES,
} from "@/lib/purpose";

/**
 * Read the vocabulary out of the committed contract rather than restating it.
 * A purpose (or a status) the backend gains and this table has not is a
 * failure here, one layer below the type-checker's own complaint.
 */
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

describe("PURPOSE_TONES", () => {
  const purposes = enumValues("Purpose");

  it("covers the whole backend purpose vocabulary", () => {
    expect(Object.keys(PURPOSE_TONES).sort()).toEqual([...purposes].sort());
  });

  it("gives every purpose a label and three colours", () => {
    for (const purpose of purposes) {
      const tone = PURPOSE_TONES[purpose as keyof typeof PURPOSE_TONES];
      expect(tone.label.length).toBeGreaterThan(0);
      expect(tone.edge).toMatch(/^#[0-9A-Fa-f]{6}$/);
      expect(tone.fg).toMatch(/^#[0-9A-Fa-f]{6}$/);
      expect(tone.tint).toMatch(/^rgb\(/);
    }
  });

  it("never labels two purposes the same", () => {
    const labels = Object.values(PURPOSE_TONES).map((tone) => tone.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("titles an untitled card by its purpose", () => {
    expect(purposeLabel("vo2max")).toBe("VO₂max");
    expect(purposeLabel("sweet_spot")).toBe("Sweet spot");
  });
});

describe("STATUS_TONES", () => {
  it("covers the whole session-status vocabulary", () => {
    expect(Object.keys(STATUS_TONES).sort()).toEqual(
      enumValues("app__domain__sessions__SessionStatus").sort(),
    );
  });
});

describe("purposeInSentence", () => {
  it("lowercases a common noun phrase and leaves a proper form alone", () => {
    expect(purposeInSentence("endurance")).toBe("endurance");
    expect(purposeInSentence("sweet_spot")).toBe("sweet spot");
    expect(purposeInSentence("max_strength")).toBe("max strength");
    expect(purposeInSentence("vo2max")).toBe("VO\u2082max");
  });
});

describe("the discipline split", () => {
  const purposes = enumValues("Purpose") as Purpose[];

  it("assigns every purpose to exactly one discipline", () => {
    const cycling = purposesFor("cycling");
    const strength = purposesFor("strength");

    expect(cycling.length + strength.length).toBe(purposes.length);
    expect(new Set([...cycling, ...strength]).size).toBe(purposes.length);
  });

  it("puts the strength family where the backend does", () => {
    expect(purposesFor("strength")).toEqual([
      "max_strength",
      "strength_endurance",
      "hypertrophy",
      "power",
      "core",
      "mobility",
      "conditioning",
    ]);
    expect(disciplineOfPurpose("vo2max")).toBe("cycling");
    expect(disciplineOfPurpose("core")).toBe("strength");
  });
});
