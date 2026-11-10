import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The palette lives in two files and nowhere else, and this test is what
 * makes that enforceable.
 *
 * `app/globals.css` holds the tokens and `lib/purpose.ts` holds the eighteen
 * purpose tones — a runtime lookup Tailwind cannot see, so it has to be data.
 * Every other file in `components/` and `app/` refers to a colour by name.
 * That is what makes a re-skin an edit in one place, and it is what stops the
 * next `#171C24` from being invented three pixels away from an existing token.
 *
 * A literal here is a lint failure rather than a review comment because the
 * seven literals that the palette rule already claimed did not exist were
 * every one of them added after it was written, one arbitrary-value class at
 * a time.
 */

const ROOT = resolve(import.meta.dirname, "..");

/** Directories whose colours must be tokens. */
const SCANNED = ["components", "app"];

/**
 * The one file allowed to name a colour, plus anything generated.
 *
 * An addition needs its reason written here beside it, not just an entry: an
 * allowlist that grows silently is the thing this test exists to prevent, and
 * the next person has to be able to tell a considered exception from a
 * shortcut someone took to make the suite pass.
 */
const ALLOWED = new Set(["app/globals.css"]);

/** `#abc`, `#abcd`, `#aabbcc`, `#aabbccdd`, `rgb(…)`, `rgba(…)`, `hsl(…)`. */
const COLOUR_LITERAL =
  /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![0-9a-zA-Z])|\b(?:rgba?|hsla?)\(/;

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(join(ROOT, dir), { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...sourceFiles(path));
    } else if (/\.(tsx?|css)$/.test(entry.name)) {
      out.push(path);
    }
  }
  return out;
}

describe("colour literals outside the palette", () => {
  const files = SCANNED.flatMap(sourceFiles).filter(
    (path) => !ALLOWED.has(path.split("\\").join("/")),
  );

  it("finds files to check at all", () => {
    // A broken path would make this suite pass by scanning nothing.
    expect(files.length).toBeGreaterThan(20);
  });

  it.each(files)("%s names its colours", (path) => {
    const offenders = readFileSync(join(ROOT, path), "utf8")
      .split("\n")
      .map((line, index) => [index + 1, line] as const)
      .filter(([, line]) => COLOUR_LITERAL.test(line))
      .map(([number, line]) => `${path}:${number}  ${line.trim()}`);

    expect(
      offenders,
      `use a token from app/globals.css instead of a colour literal:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });
});

/**
 * A colour token defined anywhere else — `style={{ "--color-ink": … }}`, a
 * `[data-contrast]` rule in a component's CSS — is a third token set beside
 * the two in globals.css, and one the contrast test never measures.
 */
const TOKEN_DEFINITION =
  /--color-[\w-]+["']?\s*:\s*["']?(?:#|rgba?\(|hsla?\(|oklch\(|color-mix\(|var\()/;

describe("colour tokens defined outside the palette", () => {
  const files = SCANNED.flatMap(sourceFiles).filter(
    (path) => !ALLOWED.has(path.split("\\").join("/")),
  );

  it.each(files)("%s defines no colour token", (path) => {
    const offenders = readFileSync(join(ROOT, path), "utf8")
      .split("\n")
      .map((line, index) => [index + 1, line] as const)
      .filter(([, line]) => TOKEN_DEFINITION.test(line))
      .map(([number, line]) => `${path}:${number}  ${line.trim()}`);

    expect(
      offenders,
      `define colour tokens in app/globals.css, in both sets:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });
});

describe("the palette itself", () => {
  it("keeps every token in globals.css, both sets of them", () => {
    const css = readFileSync(join(ROOT, "app/globals.css"), "utf8");
    // A spot check that the file this test exempts is the file it thinks it
    // is — if the palette moved, the exemption above is protecting nothing.
    expect(css).toMatch(/--color-card:/);
    // The high-contrast set is the second place a literal may appear, and it
    // is in the same file: a contrast set kept anywhere else would be exempt
    // from neither this test nor the ink-contrast one, and checked by both.
    expect(css).toMatch(/:root\[data-contrast="high"\]\s*\{[^}]*--color-card:/);
    expect(relative(ROOT, join(ROOT, "app/globals.css"))).toBe(
      join("app", "globals.css"),
    );
  });
});
