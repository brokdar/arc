import { describe, expect, it, vi } from "vitest";

import { registerServiceWorker } from "@/components/shell/sw-register";

/**
 * Registration carries the build's version in the worker URL, so a new build
 * is a new script URL and the browser installs it on the next online load.
 */

function fakeContainer({ controlled }: { controlled: boolean }) {
  const postMessage = vi.fn();
  const registration = { active: { postMessage } };
  const listeners: Array<() => void> = [];
  const container = {
    register: vi.fn(async () => registration),
    ready: Promise.resolve(registration),
    controller: controlled ? {} : null,
    addEventListener: (_type: "controllerchange", listener: () => void) => {
      listeners.push(listener);
    },
  };
  /** The worker claims the page, as its activate step does. */
  function claim() {
    container.controller = {};
    for (const listener of listeners) listener();
  }
  return { container, postMessage, claim };
}

describe("registerServiceWorker", () => {
  it("registers /sw.js at the root scope with the build version", async () => {
    const { container } = fakeContainer({ controlled: true });

    await registerServiceWorker(container, "build-42", () => []);

    expect(container.register).toHaveBeenCalledWith("/sw.js?v=build-42", {
      scope: "/",
      updateViaCache: "none",
    });
  });

  it("hands the worker this page and the build assets it loaded, not the API", async () => {
    const { container, postMessage } = fakeContainer({ controlled: true });

    await registerServiceWorker(container, "build-42", () => [
      `${location.origin}/_next/static/chunks/a.js`,
      `${location.origin}/api/v1/clock`,
    ]);

    expect(postMessage).toHaveBeenCalledWith({
      type: "cache-urls",
      urls: [location.href, `${location.origin}/_next/static/chunks/a.js`],
    });
  });

  it("waits for the worker to take over, then lists everything loaded until then", async () => {
    const { container, postMessage, claim } = fakeContainer({
      controlled: false,
    });
    const loaded = [`${location.origin}/_next/static/chunks/first.js`];

    const done = registerServiceWorker(container, "build-42", () => loaded);
    await Promise.resolve();
    await Promise.resolve();
    expect(postMessage).not.toHaveBeenCalled();
    // A chunk Next prefetched after hydration, before the worker claimed.
    loaded.push(`${location.origin}/_next/static/chunks/prefetched.js`);
    claim();
    await done;

    expect(postMessage).toHaveBeenCalledWith({
      type: "cache-urls",
      urls: [
        location.href,
        `${location.origin}/_next/static/chunks/first.js`,
        `${location.origin}/_next/static/chunks/prefetched.js`,
      ],
    });
  });

  it("sends assets that finish loading later without the worker", async () => {
    const { container, postMessage } = fakeContainer({ controlled: true });
    let send: (hrefs: readonly string[]) => void = () => {};

    await registerServiceWorker(
      container,
      "build-42",
      () => [],
      (callback) => {
        send = callback;
      },
    );
    postMessage.mockClear();
    send([
      `${location.origin}/_next/static/chunks/in-flight.js`,
      `${location.origin}/api/v1/plan/week`,
    ]);
    send([`${location.origin}/api/v1/clock`]);

    expect(postMessage.mock.calls).toEqual([
      [
        {
          type: "cache-urls",
          urls: [`${location.origin}/_next/static/chunks/in-flight.js`],
        },
      ],
    ]);
  });

  it("does not throw when registration fails", async () => {
    const container = {
      ...fakeContainer({ controlled: true }).container,
      register: vi.fn(async () => {
        throw new Error("insecure origin");
      }),
    };

    await expect(
      registerServiceWorker(container, "build-42", () => []),
    ).resolves.toBeUndefined();
  });
});
