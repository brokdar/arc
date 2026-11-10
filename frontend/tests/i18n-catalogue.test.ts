import { describe, expect, it } from "vitest";
import {
  bannedTermProblems,
  emptyValueProblems,
  fleschKincaidGrade,
  parityProblems,
  placeholderProblems,
  readingLevelProblems,
  wienerSachtextformel,
} from "@/lib/i18n/check";
import de from "@/lib/i18n/de.json";
import en from "@/lib/i18n/en.json";

/**
 * The catalogue gate: every string a person reads in arc comes from
 * `lib/i18n/{en,de}.json`, and this file is what keeps the two in step and
 * the copy free of jargon and long sentences before it ships.
 *
 * Each check is exercised twice: once on a small catalogue built to break it,
 * so the test proves the check can fail for the reason it names, and once on
 * the committed catalogues, which must pass.
 */

const COMMITTED = { "en.json": en, "de.json": de };

describe("key parity (AC-18)", () => {
  it("the committed catalogues hold the same keys", () => {
    expect(parityProblems(COMMITTED)).toEqual([]);
  });

  it("a key added to en.json only fails, naming the key", () => {
    const problems = parityProblems({
      "en.json": { ...en, today: { ...en.today, extraLine: "One more line." } },
      "de.json": de,
    });
    expect(problems).toEqual(['de.json has no key "today.extraLine"']);
  });

  it("a key added to de.json only fails, naming the key", () => {
    const problems = parityProblems({
      "en.json": en,
      "de.json": { ...de, nav: { ...de.nav, extra: "Noch einer" } },
    });
    expect(problems).toEqual(['en.json has no key "nav.extra"']);
  });

  it("a group in one language that is a string in the other is a mismatch", () => {
    const problems = parityProblems({
      "en.json": { a: { b: "Text" } },
      "de.json": { a: "Text" },
    });
    expect(problems).toEqual([
      'de.json has no key "a.b"',
      'en.json has no key "a"',
    ]);
  });

  it("an empty string value fails, naming the key", () => {
    expect(emptyValueProblems("de.json", { today: { title: "" } })).toEqual([
      'de.json: "today.title" is empty',
    ]);
    expect(emptyValueProblems("en.json", { today: { title: "   " } })).toEqual([
      'en.json: "today.title" is empty',
    ]);
  });

  it("the committed catalogues have no empty strings", () => {
    expect(emptyValueProblems("en.json", en)).toEqual([]);
    expect(emptyValueProblems("de.json", de)).toEqual([]);
  });

  it("interpolation placeholders must match between languages", () => {
    const problems = placeholderProblems({
      "en.json": { nav: { waiting: "{label}: {count} waiting on you" } },
      "de.json": { nav: { waiting: "{label}: {anzahl} warten auf dich" } },
    });
    expect(problems).toEqual([
      '"nav.waiting": en.json has {count, label}, de.json has {anzahl, label}',
    ]);
  });

  it("the same placeholders in a different order are fine", () => {
    expect(
      placeholderProblems({
        "en.json": { a: "{weight} for {reps}" },
        "de.json": { a: "{reps} mal {weight}" },
      }),
    ).toEqual([]);
  });

  it("the committed catalogues agree on every placeholder", () => {
    expect(placeholderProblems(COMMITTED)).toEqual([]);
  });
});

describe("banned terms (AC-19)", () => {
  it.each([
    ["RIR", "Two RIR left"],
    ["e1RM", "Your e1RM went up"],
    ["EWMA", "Load from the EWMA"],
    ["Epley", "Worked out with Epley"],
    ["MEV", "Above MEV this week"],
    ["MAV", "Near your MAV"],
    ["MRV", "Past MRV now"],
    ["CR10", "Rate it on CR10"],
    ["ACWR", "ACWR is high"],
  ])("%s under any key fails", (term, text) => {
    expect(
      bannedTermProblems("en.json", { deep: { nested: { line: text } } }),
    ).toEqual([`en.json: "deep.nested.line" uses "${term}"`]);
  });

  it("matches regardless of case", () => {
    expect(bannedTermProblems("de.json", { a: "Noch 2 rir übrig" })).toEqual([
      'de.json: "a" uses "RIR"',
    ]);
  });

  it("flags a term only as a whole word", () => {
    expect(
      bannedTermProblems("en.json", {
        a: "FIRIR",
        b: "e1RMs",
        c: "Smevar and Havana",
      }),
    ).toEqual([]);
  });

  it("the committed catalogues use no banned term", () => {
    expect(bannedTermProblems("en.json", en)).toEqual([]);
    expect(bannedTermProblems("de.json", de)).toEqual([]);
  });
});

describe("reading level (AC-19)", () => {
  // Grade 9.3 by the formula: 15 words in one sentence.
  const GRADE_NINE =
    "Finish the warm-up exercises before you attempt the heaviest working set of the session.";

  it("scores English with Flesch–Kincaid", () => {
    expect(fleschKincaidGrade(GRADE_NINE)).toBeGreaterThanOrEqual(9);
    expect(fleschKincaidGrade(GRADE_NINE)).toBeLessThan(10);
    expect(fleschKincaidGrade("Lift the bar. Put it down.")).toBeLessThan(2);
  });

  it("a cards. string at grade 9 fails", () => {
    expect(
      readingLevelProblems("en", { cards: { next: { why: GRADE_NINE } } }),
    ).toEqual([
      expect.stringMatching(
        /^en: "cards\.next\.why" reads at grade 9\.\d > 6$/,
      ),
    ]);
  });

  it("an onboarding. string is held to grade 6 too", () => {
    expect(
      readingLevelProblems("en", { onboarding: { intro: GRADE_NINE } }),
    ).toHaveLength(1);
  });

  it("the same string elsewhere passes up to grade 8 only", () => {
    expect(readingLevelProblems("en", { today: { hint: GRADE_NINE } })).toEqual(
      [expect.stringMatching(/^en: "today\.hint" reads at grade 9\.\d > 8$/)],
    );
    // Grade 6.7: allowed outside cards. and onboarding., refused inside.
    const gradeSeven =
      "Finish the warm-up sets before you attempt the heaviest working set of the day.";
    expect(fleschKincaidGrade(gradeSeven)).toBeGreaterThan(6);
    expect(fleschKincaidGrade(gradeSeven)).toBeLessThanOrEqual(8);
    expect(readingLevelProblems("en", { today: { hint: gradeSeven } })).toEqual(
      [],
    );
    expect(
      readingLevelProblems("en", { cards: { hint: gradeSeven } }),
    ).toHaveLength(1);
  });

  it("scores German with the Wiener Sachtextformel", () => {
    const hard =
      "Die Belastungssteuerung berücksichtigt ausschließlich dokumentierte Trainingseinheiten unterschiedlicher Intensitätsbereiche.";
    expect(wienerSachtextformel(hard)).toBeGreaterThan(8);
    expect(readingLevelProblems("de", { today: { hint: hard } })).toEqual([
      expect.stringMatching(/^de: "today\.hint" reads at grade \d+\.\d > 8$/),
    ]);
    expect(wienerSachtextformel("Heb die Hantel. Leg sie ab.")).toBeLessThan(4);
  });

  it("does not score a short label, which has no sentence to measure", () => {
    expect(
      readingLevelProblems("en", { cards: { label: "Success criteria" } }),
    ).toEqual([]);
  });

  it("the committed catalogues pass", () => {
    expect(readingLevelProblems("en", en)).toEqual([]);
    expect(readingLevelProblems("de", de)).toEqual([]);
  });
});
