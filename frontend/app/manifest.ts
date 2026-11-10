import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { MetadataRoute } from "next";

/**
 * Prerendered at build: the manifest names nothing that changes per request,
 * and the palette file it reads below is not shipped in the runtime image.
 */
export const dynamic = "force-static";

/**
 * The installed app's splash and status-bar colour: the canvas token, read
 * from `app/globals.css` rather than written out here.
 *
 * The manifest needs a colour *string* and cannot take `var(--color-canvas)`,
 * and a literal here would be the second palette
 * `tests/no-literal-colours.test.ts` exists to prevent — so the one palette
 * file is read at build time instead.
 */
function canvasColour(): string {
  const css = readFileSync(join(process.cwd(), "app", "globals.css"), "utf8");
  const match = css.match(/--color-canvas:\s*(#[0-9a-fA-F]{6})/);
  if (!match?.[1]) {
    throw new Error("app/globals.css has no --color-canvas hex value");
  }
  return match[1];
}

/**
 * Served by Next at `/manifest.webmanifest` (the metadata file convention).
 *
 * Opens on Today, because that is the page the gym needs and the one the
 * service worker precaches; standalone, so the installed app has no browser
 * chrome. Icons live in `public/icons/` and are drawn from the sidebar's
 * wordmark tile; the 512 px one doubles as the maskable icon because the tile
 * sits inside the maskable safe zone.
 */
export default function manifest(): MetadataRoute.Manifest {
  const canvas = canvasColour();
  return {
    name: "arc",
    short_name: "arc",
    description: "Training application for one athlete.",
    id: "/",
    start_url: "/today",
    scope: "/",
    display: "standalone",
    background_color: canvas,
    theme_color: canvas,
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
