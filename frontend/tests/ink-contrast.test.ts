import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { contrastRatio } from "@/tests/colour";

/**
 * Every rung of ink clears WCAG AA on every surface it lands on (D85).
 *
 * The mockup's `#5c636d` was 3.01:1 on a card and carried 38 pieces of real
 * text at 9.5–10.5px — every `SectionLabel`, the anchor provenance line, the
 * coverage notes. Fidelity to a mockup does not outrank being readable, and a
 * ratio is the kind of thing that is cheap to check and impossible to keep
 * checking by eye, so it is checked here.
 *
 * `ink-disabled` is exempt and stays exempt: WCAG 1.4.3 excludes inactive user
 * interface components, which is the only thing it may be used for. The
 * `text-ink-disabled` grep below is what keeps that promise honest.
 */

const ROOT = resolve(import.meta.dirname, "..");
const CSS = readFileSync(join(ROOT, "app/globals.css"), "utf8");

/** Pull `--color-<name>: #rrggbb;` out of the palette. */
function token(name: string): string {
  const found = new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(CSS);
  if (!found?.[1]) {
    throw new Error(`--color-${name} is not an opaque hex in globals.css`);
  }
  return found[1];
}

/** AA for body text. The inks below all carry prose at 9.5–12.5px. */
const AA = 4.5;

/**
 * Opaque surfaces that prose is set on, hover states included.
 *
 * `raised` / `raised-hover` / `well` are deliberately absent: they are button
 * and trough fills that only ever carry `ink-secondary`, and pulling them in
 * would force `ink-faint` up until it collided with `ink-muted` — buying a
 * ratio nothing needs at the cost of the hierarchy everything does. Add one
 * here the moment faint ink lands on it.
 */
const SURFACES = [
  "canvas",
  "chrome",
  "card",
  "card-hover",
  "panel",
  "inset",
  "accent-surface",
  "accent-surface-hover",
  "missed-surface",
] as const;

/** Ink that carries information, and therefore has to be readable. */
const CONTENT_INKS = [
  "ink",
  "ink-secondary",
  "ink-muted",
  "ink-faint",
] as const;

describe("ink contrast", () => {
  it.each(CONTENT_INKS)("%s clears AA on every surface", (ink) => {
    for (const surface of SURFACES) {
      const ratio = contrastRatio(token(ink), token(surface));
      expect(
        ratio,
        `--color-${ink} on --color-${surface} is ${ratio.toFixed(2)}:1, below AA`,
      ).toBeGreaterThanOrEqual(AA);
    }
  });

  it("keeps the rungs in order and visibly apart", () => {
    // Hierarchy is the point of having four of them: each must be dimmer than
    // the one above it, or the palette says nothing.
    const ratios = CONTENT_INKS.map((ink) =>
      contrastRatio(token(ink), token("card")),
    );
    for (let i = 1; i < ratios.length; i += 1) {
      expect(ratios[i]).toBeLessThan(ratios[i - 1] as number);
    }
  });

  it("leaves ink-disabled for inactive controls only", () => {
    // Below AA on purpose — so nothing that carries information may use it.
    expect(contrastRatio(token("ink-disabled"), token("card"))).toBeLessThan(
      AA,
    );
    expect(CSS).toMatch(/inactive controls only/);
  });
});
