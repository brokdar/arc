/**
 * Reading palette tokens at runtime, for the one surface that cannot use them.
 *
 * uPlot paints to a canvas, and a canvas takes colour **strings**: it cannot
 * resolve `var(--color-zone-5)` the way a `style` attribute can. Without this
 * the stream charts would have to spell their colours as literals, which is
 * exactly what `tests/no-literal-colours.test.ts` exists to prevent and what
 * would put a second palette three pixels from the first.
 *
 * So the tokens stay in `app/globals.css` and are resolved from the document
 * at draw time. Resolution is cached per token because a redraw happens on
 * every cursor move and `getComputedStyle` is a layout read.
 */

const cache = new Map<string, string>();

/** The fallback when there is no document — server render, or a bare JSDOM. */
const TRANSPARENT = "transparent";

/**
 * The computed value of one `--color-*` token, or `transparent`.
 *
 * `transparent` rather than a guessed colour: a chart that painted a
 * hard-coded grey when the palette failed to load would hide the failure,
 * and an invisible line is a bug someone reports.
 */
export function chartToken(name: string): string {
  const cached = cache.get(name);
  if (cached !== undefined) {
    return cached;
  }
  if (typeof document === "undefined") {
    return TRANSPARENT;
  }
  const resolved =
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() ||
    TRANSPARENT;
  cache.set(name, resolved);
  return resolved;
}

/** Forget every resolved token. For tests that swap the stylesheet. */
export function clearChartTokenCache(): void {
  cache.clear();
}
