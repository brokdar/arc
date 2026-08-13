/**
 * Colour arithmetic for tests that assert on the design system.
 *
 * Test-only, which is why it lives under `tests/` rather than `lib/`: nothing
 * the application renders needs to measure a colour, and a module in `lib/`
 * would invite a component to start computing tints at runtime instead of
 * naming a token.
 *
 * Two measures, both standard:
 *
 * - `contrastRatio` — WCAG 2.1 relative luminance. 4.5:1 is AA for body text,
 *   3:1 for large text and graphical objects.
 * - `deltaE00` — **CIEDE2000**, not CIE76. The distinction matters: CIE76 is
 *   plain Euclidean distance in CIE L\*a\*b\*, which badly overstates the
 *   separation of saturated colours, and a threshold set against it lets
 *   near-identical high-chroma tones through. Every ΔE figure asserted against
 *   this helper is CIEDE2000 with kL = kC = kH = 1, so a threshold quoted from
 *   somewhere that measured in CIE76 does not transfer — re-measure it here.
 */

type Rgb = readonly [number, number, number];
type Lab = readonly [number, number, number];

/** `#rrggbb` (or `#rgb`) as 0–255 channels. */
export function parseHex(hex: string): Rgb {
  const body = hex.replace("#", "");
  const full =
    body.length === 3
      ? body
          .split("")
          .map((c) => c + c)
          .join("")
      : body;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`not a hex colour: ${hex}`);
  }
  return [
    Number.parseInt(full.slice(0, 2), 16),
    Number.parseInt(full.slice(2, 4), 16),
    Number.parseInt(full.slice(4, 6), 16),
  ];
}

function linearise(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance([r, g, b]: Rgb): number {
  return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b);
}

/** WCAG 2.1 contrast ratio between two hex colours, 1:1 … 21:1. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(parseHex(a));
  const lb = relativeLuminance(parseHex(b));
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** Composite a translucent colour over an opaque one — a tint over a card. */
export function composite(fg: string, alpha: number, bg: string): string {
  const f = parseHex(fg);
  const b = parseHex(bg);
  const mix = f.map((channel, i) =>
    Math.round(channel * alpha + (b[i] as number) * (1 - alpha)),
  );
  return `#${mix.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** CIE L\*a\*b\* under D65 / 2°, from an sRGB hex. */
export function toLab(hex: string): Lab {
  const [r, g, b] = parseHex(hex).map(linearise) as unknown as Rgb;
  const x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375;
  const y = r * 0.2126729 + g * 0.7151522 + b * 0.072175;
  const z = r * 0.0193339 + g * 0.119192 + b * 0.9503041;
  const f = (t: number) =>
    t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29;
  const fx = f(x / 0.95047);
  const fy = f(y);
  const fz = f(z / 1.08883);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

const rad = (deg: number) => (deg * Math.PI) / 180;
const deg = (r: number) => ((r * 180) / Math.PI + 360) % 360;

/**
 * CIEDE2000 colour difference between two hex colours.
 *
 * Rules of thumb on this scale: ~1 is the just-noticeable difference under
 * ideal conditions, ~5 is "clearly a different colour side by side", ~10 is
 * "nobody would call these the same colour at a glance".
 */
export function deltaE00(hexA: string, hexB: string): number {
  const [l1, a1, b1] = toLab(hexA);
  const [l2, a2, b2] = toLab(hexB);
  const c1 = Math.hypot(a1, b1);
  const c2 = Math.hypot(a2, b2);
  const cBar = (c1 + c2) / 2;
  const g = 0.5 * (1 - Math.sqrt(cBar ** 7 / (cBar ** 7 + 25 ** 7)));
  const a1p = (1 + g) * a1;
  const a2p = (1 + g) * a2;
  const c1p = Math.hypot(a1p, b1);
  const c2p = Math.hypot(a2p, b2);
  const h1p = a1p === 0 && b1 === 0 ? 0 : deg(Math.atan2(b1, a1p));
  const h2p = a2p === 0 && b2 === 0 ? 0 : deg(Math.atan2(b2, a2p));

  const dLp = l2 - l1;
  const dCp = c2p - c1p;
  let dhp = 0;
  if (c1p * c2p !== 0) {
    const diff = h2p - h1p;
    dhp = Math.abs(diff) <= 180 ? diff : diff > 180 ? diff - 360 : diff + 360;
  }
  const dHp = 2 * Math.sqrt(c1p * c2p) * Math.sin(rad(dhp) / 2);

  const lBar = (l1 + l2) / 2;
  const cBarP = (c1p + c2p) / 2;
  let hBarP = h1p + h2p;
  if (c1p * c2p !== 0) {
    if (Math.abs(h1p - h2p) <= 180) {
      hBarP = (h1p + h2p) / 2;
    } else {
      hBarP = h1p + h2p < 360 ? (h1p + h2p + 360) / 2 : (h1p + h2p - 360) / 2;
    }
  }

  const t =
    1 -
    0.17 * Math.cos(rad(hBarP - 30)) +
    0.24 * Math.cos(rad(2 * hBarP)) +
    0.32 * Math.cos(rad(3 * hBarP + 6)) -
    0.2 * Math.cos(rad(4 * hBarP - 63));
  const dTheta = 30 * Math.exp(-(((hBarP - 275) / 25) ** 2));
  const rC = 2 * Math.sqrt(cBarP ** 7 / (cBarP ** 7 + 25 ** 7));
  const sL = 1 + (0.015 * (lBar - 50) ** 2) / Math.sqrt(20 + (lBar - 50) ** 2);
  const sC = 1 + 0.045 * cBarP;
  const sH = 1 + 0.015 * cBarP * t;
  const rT = -Math.sin(rad(2 * dTheta)) * rC;

  return Math.sqrt(
    (dLp / sL) ** 2 +
      (dCp / sC) ** 2 +
      (dHp / sH) ** 2 +
      rT * (dCp / sC) * (dHp / sH),
  );
}
