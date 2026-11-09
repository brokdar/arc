import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { FITNESS_LINES } from "@/components/fitness/fitness-chart";
import { contrastRatio, deltaE00 } from "@/tests/colour";

const CSS = readFileSync(
  join(resolve(import.meta.dirname, "../.."), "app/globals.css"),
  "utf8",
);

/** One opaque `--color-*` token out of the palette. */
function token(name: string): string {
  const found = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(CSS);
  if (!found?.[1]) {
    throw new Error(`${name} is not an opaque hex in globals.css`);
  }
  return found[1];
}

/**
 * Purple belongs to the coach and the over-target verdict (build-plan
 * invariant 7), and `globals.css` said in advance that fitness and fatigue
 * must not take it. Measured, the way `lib/purpose.test.ts` measures the
 * purpose tones, rather than remembered.
 */
describe("the fitness chart's palette", () => {
  const reserved = [
    "--color-coach",
    "--color-coach-strong",
    "--color-status-over",
  ];

  it.each(FITNESS_LINES)(
    "keeps $label clear of the purple reservation",
    (line) => {
      for (const name of reserved) {
        expect(deltaE00(token(line.token), token(name))).toBeGreaterThan(10);
      }
    },
  );

  it.each(FITNESS_LINES)("draws $label visibly on the panel", (line) => {
    // A 1.75px line has to survive on the panel it is drawn in: 3:1 is the
    // non-text contrast floor (WCAG 1.4.11).
    expect(
      contrastRatio(token(line.token), token("--color-panel")),
    ).toBeGreaterThan(3);
  });

  it("tells the three lines apart", () => {
    const [fitness, fatigue, form] = FITNESS_LINES.map((line) =>
      token(line.token),
    );
    if (!fitness || !fatigue || !form) throw new Error("three lines");
    expect(deltaE00(fitness, fatigue)).toBeGreaterThan(10);
    expect(deltaE00(fitness, form)).toBeGreaterThan(10);
    expect(deltaE00(fatigue, form)).toBeGreaterThan(10);
  });
});
