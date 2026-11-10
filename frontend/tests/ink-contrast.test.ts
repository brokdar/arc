import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { $api } from "@/lib/api/client";
import { I18nProvider } from "@/lib/i18n/provider";
import { useDocumentContrast } from "@/lib/training-settings";
import { contrastRatio } from "@/tests/colour";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import { patchTrainingSettings } from "@/tests/mocks/training-settings-state";

/**
 * Every rung of ink clears WCAG AA on every surface it lands on — in both
 * token sets: the default one, and the high-contrast one a training setting
 * switches to for a gym lit by glare or by almost nothing.
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

/** The selector the high-contrast set hangs off; `<html>` carries it. */
const HIGH_SELECTOR = ':root[data-contrast="high"]';

/** The body of the `{ … }` block opened by the first match of `opener`. */
function block(css: string, opener: RegExp): string {
  const found = opener.exec(css);
  if (!found) throw new Error(`no block matching ${opener} in globals.css`);
  const start = found.index + found[0].length;
  let depth = 1;
  for (let i = start; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") depth -= 1;
    if (depth === 0) return css.slice(start, i);
  }
  throw new Error(`unclosed block after ${opener}`);
}

/**
 * The colour tokens a block states as values, `--color-<name>` → value.
 *
 * Aliases (`var(--color-…)`) are left out: they follow whatever they point
 * at, in whichever set is active, so they carry no decision of their own.
 */
function literalColours(body: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const [, name, value] of body.matchAll(
    /--color-([\w-]+):\s*([^;]+);/g,
  )) {
    if (name && value && !value.trim().startsWith("var(")) {
      out.set(name, value.trim());
    }
  }
  return out;
}

function defaultSet(css: string): Map<string, string> {
  return literalColours(block(css, /@theme\s*\{/));
}

function highSet(css: string): Map<string, string> {
  return literalColours(block(css, /:root\[data-contrast="high"\]\s*\{/));
}

/** Tokens stated in one set and not the other, each named with its set. */
function parityGaps(css: string): string[] {
  const base = defaultSet(css);
  const high = highSet(css);
  return [
    ...[...base.keys()]
      .filter((name) => !high.has(name))
      .map((name) => `--color-${name} only in the default set`),
    ...[...high.keys()]
      .filter((name) => !base.has(name))
      .map((name) => `--color-${name} only in the high-contrast set`),
  ];
}

const SETS = {
  default: defaultSet(CSS),
  "high-contrast": highSet(CSS),
} as const;
type SetName = keyof typeof SETS;
const SET_NAMES = Object.keys(SETS) as SetName[];

/** One token of one set, which must be an opaque hex to be measured. */
function token(set: SetName, name: string): string {
  const value = SETS[set].get(name);
  if (!value || !/^#[0-9a-fA-F]{6}$/.test(value)) {
    throw new Error(`--color-${name} is not an opaque hex in the ${set} set`);
  }
  return value;
}

/** AA for body text. The inks below all carry prose at 9.5–12.5px. */
const AA = 4.5;

/**
 * AAA for body text, and the floor for the numbers the athlete reads from
 * arm's length in the gym — the load, the reps, the timer.
 */
const AAA = 7;

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

/**
 * The plan's `--ink-strong`: the top rung, `--color-ink`, which every core
 * number is set in (it is what `html`/`body` default to and what a figure
 * renders in unless it is deliberately quieter). "On `--surface`" is every
 * surface above, not one of them — a load printed on a hovered card is still
 * a load. A separate `ink-strong` token was not added: it would be a second
 * name for the same rung, and the next figure would have to choose between
 * two names for one meaning.
 */
const INK_STRONG = "ink";

/** Text set on a fill of its own, outside the ink × surface grid. */
const OTHER_TEXT_PAIRS = [
  ["accent-ink", "accent"],
  ["accent-quiet", "accent-surface"],
  ["accent-quiet", "accent-surface-hover"],
  ["coach-ink", "coach-surface"],
] as const;

describe.each(SET_NAMES)("ink contrast, %s set", (set) => {
  it(`ink-strong (--color-${INK_STRONG}) clears ${AAA}:1 on every surface`, () => {
    for (const surface of SURFACES) {
      const ratio = contrastRatio(token(set, INK_STRONG), token(set, surface));
      expect(
        ratio,
        `--color-${INK_STRONG} on --color-${surface} is ${ratio.toFixed(2)}:1, below ${AAA}:1`,
      ).toBeGreaterThanOrEqual(AAA);
    }
  });

  it.each(CONTENT_INKS)("%s clears AA on every surface", (ink) => {
    for (const surface of SURFACES) {
      const ratio = contrastRatio(token(set, ink), token(set, surface));
      expect(
        ratio,
        `--color-${ink} on --color-${surface} is ${ratio.toFixed(2)}:1, below AA`,
      ).toBeGreaterThanOrEqual(AA);
    }
  });

  it.each(OTHER_TEXT_PAIRS)("%s on %s clears AA", (ink, fill) => {
    const ratio = contrastRatio(token(set, ink), token(set, fill));
    expect(
      ratio,
      `--color-${ink} on --color-${fill} is ${ratio.toFixed(2)}:1, below AA`,
    ).toBeGreaterThanOrEqual(AA);
  });

  it("keeps the rungs in order and visibly apart", () => {
    // Hierarchy is the point of having four of them: each must be dimmer than
    // the one above it, or the palette says nothing.
    const ratios = CONTENT_INKS.map((ink) =>
      contrastRatio(token(set, ink), token(set, "card")),
    );
    for (let i = 1; i < ratios.length; i += 1) {
      expect(ratios[i]).toBeLessThan(ratios[i - 1] as number);
    }
  });

  it("leaves ink-disabled for inactive controls only", () => {
    // Below AA on purpose — so nothing that carries information may use it.
    expect(
      contrastRatio(token(set, "ink-disabled"), token(set, "card")),
    ).toBeLessThan(AA);
    expect(CSS).toMatch(/inactive controls only/);
  });
});

describe("the high-contrast set", () => {
  it("states every colour token the default set states, and no other", () => {
    expect(SETS["high-contrast"].size).toBeGreaterThan(20);
    expect(parityGaps(CSS)).toEqual([]);
  });

  it("fails the parity check for a token added only to the default set", () => {
    const grown = CSS.replace(
      /@theme\s*\{/,
      (opener) => `${opener}\n  --color-new-thing: #123456;`,
    );
    expect(parityGaps(grown)).toEqual([
      "--color-new-thing only in the default set",
    ]);
  });

  it("fails the parity check for a token added only to the high-contrast set", () => {
    const grown = CSS.replace(
      HIGH_SELECTOR,
      `${HIGH_SELECTOR} {\n  --color-stray: #123456;\n}\n${HIGH_SELECTOR}`,
    );
    // The first block is the one read, so the stray is all it holds.
    expect(parityGaps(grown)).toContain(
      "--color-stray only in the high-contrast set",
    );
  });

  it("stands down under forced-colors: active, leaving system colours in control", () => {
    // The set is declared once, and only inside `forced-colors: none`: with
    // Windows High Contrast (or any forced palette) on, the browser's own
    // system colours win and no token of ours is re-pointed over them.
    expect(CSS.split(HIGH_SELECTOR)).toHaveLength(2);
    const guarded = block(CSS, /@media\s*\(forced-colors:\s*none\)\s*\{/);
    expect(guarded).toContain(HIGH_SELECTOR);
    expect(literalColours(guarded).size).toBe(SETS["high-contrast"].size);
    // …and nothing redefines a colour token for a forced palette.
    expect(CSS).not.toMatch(/forced-colors:\s*active/);
  });
});

describe("high_contrast on <html>", () => {
  const CONTRAST_KEY = "arc:high-contrast";

  function wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return createElement(QueryClientProvider, { client }, children);
  }

  function offline() {
    server.use(
      http.untyped.get("http://localhost:8000/api/v1/settings/training", () =>
        Response.error(),
      ),
    );
  }

  beforeEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.contrast;
  });

  afterEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.contrast;
  });

  it('with high_contrast: true, <html> carries data-contrast="high"', async () => {
    patchTrainingSettings({ high_contrast: true });
    renderHook(() => useDocumentContrast(), { wrapper });
    await waitFor(() =>
      expect(document.documentElement.getAttribute("data-contrast")).toBe(
        "high",
      ),
    );
  });

  it("the signed-in tree applies it: I18nProvider in app/(app)/layout.tsx", async () => {
    patchTrainingSettings({ high_contrast: true });
    renderHook(() => null, {
      wrapper: ({ children }) =>
        wrapper({ children: createElement(I18nProvider, null, children) }),
    });
    await waitFor(() =>
      expect(document.documentElement.getAttribute("data-contrast")).toBe(
        "high",
      ),
    );
  });

  it("with high_contrast: false, <html> carries no data-contrast", async () => {
    document.documentElement.dataset.contrast = "high";
    patchTrainingSettings({ high_contrast: false });
    renderHook(() => useDocumentContrast(), { wrapper });
    await waitFor(() =>
      expect(document.documentElement.hasAttribute("data-contrast")).toBe(
        false,
      ),
    );
  });

  it("offline, it keeps the contrast the server last served on this phone", async () => {
    patchTrainingSettings({ high_contrast: true });
    const first = renderHook(() => useDocumentContrast(), { wrapper });
    await waitFor(() =>
      expect(window.localStorage.getItem(CONTRAST_KEY)).toBe("true"),
    );
    first.unmount();
    delete document.documentElement.dataset.contrast;

    offline();
    const second = renderHook(
      () => {
        useDocumentContrast();
        return $api.useQuery("get", "/api/v1/settings/training");
      },
      { wrapper },
    );
    expect(document.documentElement.getAttribute("data-contrast")).toBe("high");
    // Let the failed fetch land inside the test: left in flight, the network
    // error rejects after cleanup has unmounted the hook and Vitest reports it
    // as an unhandled rejection. Once it lands, the remembered value must
    // still be in force — the failure must not clear it.
    await waitFor(() => expect(second.result.current.isError).toBe(true));
    expect(document.documentElement.getAttribute("data-contrast")).toBe("high");
  });

  it("removes the attribute when unmounted, so the login page is default", async () => {
    patchTrainingSettings({ high_contrast: true });
    const hook = renderHook(() => useDocumentContrast(), { wrapper });
    await waitFor(() =>
      expect(document.documentElement.dataset.contrast).toBe("high"),
    );
    hook.unmount();
    expect(document.documentElement.hasAttribute("data-contrast")).toBe(false);
  });
});
