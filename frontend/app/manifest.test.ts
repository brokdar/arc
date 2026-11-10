import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";

/**
 * `app/manifest.ts` is what Next serves at `/manifest.webmanifest` — the file
 * convention, not a route of ours — so the object it returns *is* the body of
 * that response.
 */

const PUBLIC = resolve(import.meta.dirname, "..", "public");

/** Width and height from a PNG's IHDR chunk. */
function pngSize(path: string): [number, number] {
  const bytes = readFileSync(path);
  expect(bytes.subarray(1, 4).toString("ascii")).toBe("PNG");
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe("GET /manifest.webmanifest", () => {
  const body = manifest();

  it("installs as a standalone app that opens on Today", () => {
    expect(body.display).toBe("standalone");
    expect(body.start_url).toBe("/today");
    expect(body.scope).toBe("/");
    expect(body.name).toBe("arc");
  });

  it("offers a 192 px and a 512 px icon", () => {
    const sizes = (body.icons ?? []).map((icon) => icon.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
  });

  it("references only icons that exist in public/icons, at the size it claims", () => {
    expect(body.icons?.length).toBeGreaterThan(0);
    for (const icon of body.icons ?? []) {
      expect(icon.src.startsWith("/icons/")).toBe(true);
      const file = join(PUBLIC, icon.src);
      expect(existsSync(file), `${icon.src} is missing`).toBe(true);
      const [width, height] = pngSize(file);
      expect(`${width}x${height}`).toBe(icon.sizes);
      expect(icon.type).toBe("image/png");
    }
  });

  it("paints the splash and the status bar in the app's dark canvas", () => {
    const css = readFileSync(
      resolve(import.meta.dirname, "globals.css"),
      "utf8",
    );
    const canvas = css.match(/--color-canvas:\s*(#[0-9a-fA-F]{6})/)?.[1];
    expect(canvas).toBeDefined();
    expect(body.theme_color).toBe(canvas);
    expect(body.background_color).toBe(canvas);
  });
});
