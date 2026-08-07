import "@testing-library/jest-dom/vitest";
import { Blob as NodeBlob, File as NodeFile } from "node:buffer";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { resetMockState } from "./tests/mocks/fixtures";
import { server } from "./tests/mocks/server";

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
