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
import { composite, contrastRatio, deltaE00 } from "@/tests/colour";

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

/**
 * Purple belongs to the coach and to the over-target verdict, and to nothing
 * else (build-plan invariant 7).
 *
 * `max_strength` was once byte-identical to both of them, which is exactly the
 * failure a table of eighteen hand-picked hexes invites: nobody diffs a colour
 * against a token in a different file. So the reservation is measured rather
 * than remembered. ΔE00 ≥ 10 is roughly "no one would call these the same
 * colour at a glance" and is met by every tone in the table with margin — the
 * closest is `recovery`, a desaturated slate, at 11.9.
 */
const RESERVED_FOR_INTERPRETATION = {
  "--color-coach": "#B49BFF",
  "--color-coach-strong": "#C7B6FF",
  "--color-status-over": "#A78BFA",
} as const;

/** Below this, two colours read as the same colour on a 10px badge. */
const MIN_RESERVED_DISTANCE = 10;

describe("the purple reservation", () => {
  const tones = Object.entries(PURPOSE_TONES);

  it.each(tones)(
    "keeps %s clear of the coach and over-target tones",
    (purpose, tone) => {
      // Both the badge text and the card's left edge — the tint is the edge at
      // 14 % alpha, so guarding the edge guards all three.
      for (const swatch of [tone.edge, tone.fg]) {
        for (const [token, reserved] of Object.entries(
          RESERVED_FOR_INTERPRETATION,
        )) {
          const distance = deltaE00(swatch, reserved);
          expect(
            distance,
            `${purpose} ${swatch} is ΔE00 ${distance.toFixed(2)} from ${token} (${reserved}) — purple is reserved for interpretive content`,
          ).toBeGreaterThan(MIN_RESERVED_DISTANCE);
        }
      }
    },
  );

  it("never re-uses a reserved hex outright", () => {
    const reserved = new Set(
      Object.values(RESERVED_FOR_INTERPRETATION).map((hex) =>
        hex.toLowerCase(),
      ),
    );
    for (const [, tone] of tones) {
      expect(reserved.has(tone.edge.toLowerCase())).toBe(false);
      expect(reserved.has(tone.fg.toLowerCase())).toBe(false);
    }
  });
});

describe("badge legibility", () => {
  const CARD = "#131519"; // --color-card, the surface a badge sits on

  it.each(Object.entries(PURPOSE_TONES))(
    "renders %s legibly on a card at badge size",
    (_purpose, tone) => {
      // The badge is `fg` text over `tint` (the edge at 14 %) over the card.
      const alpha = Number(/\/\s*([\d.]+)\s*\)/.exec(tone.tint)?.[1] ?? "0.14");
      const badge = composite(tone.edge, alpha, CARD);
      expect(contrastRatio(tone.fg, badge)).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(tone.fg, CARD)).toBeGreaterThanOrEqual(3);
    },
  );
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
