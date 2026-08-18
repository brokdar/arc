import "@testing-library/jest-dom/vitest";
import { Blob as NodeBlob, File as NodeFile } from "node:buffer";
import { configure } from "@testing-library/dom";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { resetMockState } from "./tests/mocks/fixtures";
import { server } from "./tests/mocks/server";

/**
 * How long a `findBy*` waits before deciding the element never arrived.
 *
 * Testing Library's default is one second, which is tuned for a developer's
 * laptop. A shared CI runner is an order of magnitude slower under contention
 * — a suite whose `environment` phase costs 50 s across 46 files has spent
 * most of that queueing — and a component that renders its first row in 90 ms
 * here has been observed taking longer than a second there. That is a false
 * failure: the element does appear, and the assertion was right.
 *
 * Raising the budget does not weaken anything. `findBy*` polls until the
 * element exists, so a query that is genuinely wrong still fails, with the
 * same message and the same DOM dump — just five seconds later instead of
 * one. `testTimeout` in `vitest.config.mts` is set well above this so the
 * Testing Library error, which names the query and prints the DOM, is the one
 * that surfaces rather than a bare test timeout.
 */
configure({ asyncUtilTimeout: 5_000 });

/**
 * Multipart uploads need the *runtime's* `File`, `Blob` and `FormData`, not
 * jsdom's.
 *
 * `fetch` here is Node's, and it serialises a `FormData` by brand-checking the
 * values inside it. jsdom supplies its own `File`/`Blob`/`FormData`, so a file
 * picked up by an `<input type="file">` and posted through `fetch` arrives at
 * the server as a nameless, **empty** part — silently, with no error anywhere.
 * That is not a mock problem: it is the one place where the jsdom environment
 * is not a faithful stand-in for a browser, and the inbox's upload control is
 * the first thing in this app to cross it.
 *
 * `FormData` is reached through a `Response`, which is Node's (jsdom has no
 * fetch), because Node exposes no module that exports the constructor.
 */
const NodeFormData = (
  await new Response(new URLSearchParams(), {
    headers: { "content-type": "application/x-www-form-urlencoded" },
  }).formData()
).constructor as typeof FormData;

Object.assign(globalThis, {
  File: NodeFile,
  Blob: NodeBlob,
  FormData: NodeFormData,
});

/**
 * uPlot reads `matchMedia` and `devicePixelRatio` when it is imported, to
 * follow a display that changes DPI. jsdom implements neither, so *importing*
 * the stream charts throws before a single assertion runs — the second place
 * (after `File`/`FormData`) where jsdom is not a faithful stand-in for a
 * browser rather than a mock that needs adjusting.
 *
 * The stub reports a display that never changes, which is the only honest
 * answer in a headless environment: the charts are asserted on their data and
 * their structure, and the pixels they paint to a canvas jsdom does not
 * implement are an e2e concern.
 */
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

/**
 * uPlot also needs a `ResizeObserver` and a 2D canvas context, and jsdom has
 * neither.
 *
 * Both are stubbed rather than polyfilled, because what they would provide is
 * not what these tests assert. A component test proves the charts are given
 * the right *data* and that the page around them renders — the pixels uPlot
 * paints are an end-to-end concern, and pulling in a native canvas
 * implementation to render them into a headless DOM nobody looks at would buy
 * a slower suite and no extra confidence.
 *
 * The context is a proxy of no-ops that answers every drawing call, so a draw
 * hook runs to completion instead of throwing on the first `clearRect` — which
 * is the difference between "the chart drew nothing" and "importing the page
 * failed".
 */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class NoopPath2D {
  addPath() {}
  arc() {}
  closePath() {}
  lineTo() {}
  moveTo() {}
  rect() {}
}

if (typeof globalThis.Path2D === "undefined") {
  globalThis.Path2D = NoopPath2D as unknown as typeof Path2D;
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver =
    NoopResizeObserver as unknown as typeof ResizeObserver;
}

if (typeof HTMLCanvasElement !== "undefined") {
  function noopContext(this: HTMLCanvasElement) {
    return new Proxy(
      { canvas: this, font: "", fillStyle: "", strokeStyle: "" },
      {
        get: (target: Record<string, unknown>, property: string) =>
          property in target
            ? target[property]
            : property === "measureText"
              ? () => ({ width: 0 })
              : () => undefined,
        set: (target: Record<string, unknown>, property: string, value) => {
          target[property] = value;
          return true;
        },
      },
    ) as unknown as CanvasRenderingContext2D;
  }

  HTMLCanvasElement.prototype.getContext =
    noopContext as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  // The ingest handlers are stateful (a confirmed record stays confirmed, an
  // uploaded hash stays known), so resetting the handlers is not enough:
  // without this, the second test in a file inherits the first one's queue.
  resetMockState();
  cleanup();
});
afterAll(() => server.close());
