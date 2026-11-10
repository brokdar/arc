import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The service worker, run as the browser runs it: the plain script from
 * `public/sw.js`, evaluated against a stand-in `self`, `caches` and `fetch`.
 *
 * The worker cannot be imported — it is not a module, and it must stay plain
 * JavaScript at the origin root to control `/` — so the test reads the file
 * that ships and drives the events a browser would dispatch to it. What is
 * asserted is the response the page would get and what ends up in the cache,
 * never which internal function ran.
 */

const SOURCE = readFileSync(
  resolve(import.meta.dirname, "..", "public", "sw.js"),
  "utf8",
);
const ORIGIN = "http://arc.test";

type Listener = (event: never) => void;

/** One named cache, keyed by absolute URL like the real one. */
class FakeCache {
  readonly entries = new Map<string, Response>();

  async match(request: RequestLike | string, options?: CacheQueryOptions) {
    const url = new URL(
      typeof request === "string" ? request : request.url,
      ORIGIN,
    );
    if (options?.ignoreSearch) {
      for (const [key, response] of this.entries) {
        const candidate = new URL(key);
        if (
          candidate.origin + candidate.pathname ===
          url.origin + url.pathname
        ) {
          return response.clone();
        }
      }
      return undefined;
    }
    return this.entries.get(url.href)?.clone();
  }

  async put(request: RequestLike | string, response: Response) {
    const url = new URL(
      typeof request === "string" ? request : request.url,
      ORIGIN,
    );
    this.entries.set(url.href, response);
  }

  async keys() {
    return [...this.entries.keys()].map((url) => ({ url }));
  }
}

class FakeCacheStorage {
  readonly stores = new Map<string, FakeCache>();

  async open(name: string) {
    let cache = this.stores.get(name);
    if (!cache) {
      cache = new FakeCache();
      this.stores.set(name, cache);
    }
    return cache;
  }

  async keys() {
    return [...this.stores.keys()];
  }

  async delete(name: string) {
    return this.stores.delete(name);
  }

  async has(name: string) {
    return this.stores.has(name);
  }
}

interface RequestLike {
  url: string;
  method: string;
  mode: string;
  headers: Headers;
}

function request(
  path: string,
  init: Partial<Omit<RequestLike, "url">> = {},
): RequestLike {
  return {
    url: new URL(path, ORIGIN).href,
    method: init.method ?? "GET",
    mode: init.mode ?? "cors",
    headers: init.headers ?? new Headers(),
  };
}

function html(body: string, status = 200) {
  return new Response(`<!doctype html><html><body>${body}</body></html>`, {
    status,
    headers: { "content-type": "text/html" },
  });
}

/** Boot the worker script with a given registration URL query. */
function boot(version = "build-1") {
  const listeners = new Map<string, Listener>();
  const caches = new FakeCacheStorage();
  const fetch = vi.fn<(input: RequestLike | string) => Promise<Response>>();
  const self = {
    location: new URL(`/sw.js?v=${version}`, ORIGIN),
    addEventListener: (type: string, listener: Listener) => {
      listeners.set(type, listener);
    },
    skipWaiting: vi.fn(async () => undefined),
    clients: { claim: vi.fn(async () => undefined) },
  };
  new Function("self", "caches", "fetch", SOURCE)(self, caches, fetch);

  /** Dispatch an install/activate-style event and wait for its work. */
  async function lifecycle(type: "install" | "activate") {
    let work: Promise<unknown> = Promise.resolve();
    const listener = listeners.get(type);
    if (!listener) throw new Error(`no ${type} listener`);
    listener({
      waitUntil: (promise: Promise<unknown>) => {
        work = promise;
      },
    } as never);
    await work;
  }

  /**
   * Dispatch a fetch. Resolves to the worker's response, or `undefined` when
   * the worker let the request through to the network untouched.
   */
  async function dispatch(req: RequestLike) {
    let responded: Promise<Response> | undefined;
    const listener = listeners.get("fetch");
    if (!listener) throw new Error("no fetch listener");
    listener({
      request: req,
      respondWith: (promise: Promise<Response>) => {
        responded = promise;
      },
      waitUntil: () => undefined,
    } as never);
    return responded === undefined ? undefined : await responded;
  }

  async function message(data: unknown) {
    const replies: unknown[] = [];
    let work: Promise<unknown> = Promise.resolve();
    const listener = listeners.get("message");
    if (!listener) throw new Error("no message listener");
    listener({
      data,
      ports: [{ postMessage: (reply: unknown) => replies.push(reply) }],
      waitUntil: (promise: Promise<unknown>) => {
        work = promise;
      },
    } as never);
    await work;
    return replies;
  }

  return { self, caches, fetch, lifecycle, dispatch, message };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the worker's version", () => {
  it("names its cache after the version it was registered with", async () => {
    const worker = boot("build-7");
    worker.fetch.mockImplementation(async () => html("shell"));

    await worker.lifecycle("install");

    expect(await worker.caches.keys()).toEqual(["arc-shell-build-7"]);
  });

  it("answers a version question with the registered version", async () => {
    const worker = boot("build-7");

    expect(await worker.message({ type: "version" })).toEqual([
      { type: "version", version: "build-7" },
    ]);
  });

  it("activates at once, so the next load after a deploy runs the new worker", async () => {
    const worker = boot("build-2");
    worker.fetch.mockImplementation(async () => html("shell"));

    await worker.lifecycle("install");
    await worker.lifecycle("activate");

    expect(worker.self.skipWaiting).toHaveBeenCalled();
    expect(worker.self.clients.claim).toHaveBeenCalled();
  });

  it("drops the previous build's shell on activation and keeps foreign caches", async () => {
    const worker = boot("build-2");
    await worker.caches.open("arc-shell-build-1");
    await worker.caches.open("someone-else");
    await worker.caches.open("arc-shell-build-2");

    await worker.lifecycle("activate");

    expect((await worker.caches.keys()).sort()).toEqual([
      "arc-shell-build-2",
      "someone-else",
    ]);
  });
});

describe("installing the app shell", () => {
  it("precaches Today, the offline page and the static assets they load", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(async (input) => {
      const url = new URL(
        typeof input === "string" ? input : input.url,
        ORIGIN,
      );
      if (url.pathname === "/today") {
        return html(
          '<script src="/_next/static/chunks/today-abc.js"></script>' +
            '<link rel="stylesheet" href="/_next/static/css/app-1.css">' +
            // The flight data names lazily loaded chunks inside escaped strings.
            '<script>self.__next_f.push([1,"\\"/_next/static/chunks/lazy-9.js\\""])</script>',
        );
      }
      if (url.pathname === "/offline") {
        return html(
          '<script src="/_next/static/chunks/offline-1.js"></script>',
        );
      }
      return new Response("asset", { status: 200 });
    });

    await worker.lifecycle("install");

    const cache = await worker.caches.open("arc-shell-build-1");
    expect(
      (await cache.keys()).map(({ url }) => new URL(url).pathname).sort(),
    ).toEqual([
      "/_next/static/chunks/lazy-9.js",
      "/_next/static/chunks/offline-1.js",
      "/_next/static/chunks/today-abc.js",
      "/_next/static/css/app-1.css",
      "/offline",
      "/today",
    ]);
  });

  it("still installs when one asset cannot be fetched", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(async (input) => {
      const url = new URL(
        typeof input === "string" ? input : input.url,
        ORIGIN,
      );
      if (url.pathname.startsWith("/_next/")) throw new TypeError("offline");
      return html('<script src="/_next/static/chunks/gone.js"></script>');
    });

    await worker.lifecycle("install");

    const cache = await worker.caches.open("arc-shell-build-1");
    expect(await cache.match("/today")).toBeDefined();
    expect(worker.self.skipWaiting).toHaveBeenCalled();
  });
});

describe("navigations: network first, cache when the network fails", () => {
  it("serves the network's page and keeps a copy", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(async () => html("fresh calendar"));

    const response = await worker.dispatch(
      request("/calendar?week=2026-11-09", { mode: "navigate" }),
    );

    expect(await response?.text()).toContain("fresh calendar");
    const cache = await worker.caches.open("arc-shell-build-1");
    expect(
      await (await cache.match("/calendar?week=2026-11-09"))?.text(),
    ).toContain("fresh calendar");
  });

  it("serves the cached page when the network is gone", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/today", html("cached today"));
    worker.fetch.mockRejectedValue(new TypeError("Failed to fetch"));

    const response = await worker.dispatch(
      request("/today", { mode: "navigate" }),
    );

    expect(await response?.text()).toContain("cached today");
  });

  it("matches a cached page whatever its query string", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/calendar", html("cached calendar"));
    worker.fetch.mockRejectedValue(new TypeError("Failed to fetch"));

    const response = await worker.dispatch(
      request("/calendar?week=2026-11-09", { mode: "navigate" }),
    );

    expect(await response?.text()).toContain("cached calendar");
  });

  it("serves the app's offline page for a route never opened online", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/offline", html("offline page"));
    worker.fetch.mockRejectedValue(new TypeError("Failed to fetch"));

    const response = await worker.dispatch(
      request("/fitness", { mode: "navigate" }),
    );

    expect(response?.status).toBe(200);
    expect(await response?.text()).toContain("offline page");
  });

  it("falls back to the cache when the network hangs, as a basement does", async () => {
    vi.useFakeTimers();
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/today", html("cached today"));
    worker.fetch.mockImplementation(() => new Promise<Response>(() => {}));

    const pending = worker.dispatch(request("/today", { mode: "navigate" }));
    await vi.advanceTimersByTimeAsync(10_000);
    const response = await pending;

    expect(await response?.text()).toContain("cached today");
  });

  it("does not keep an error page in place of the cached one", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/today", html("cached today"));
    worker.fetch.mockImplementation(async () => html("bad gateway", 502));

    const response = await worker.dispatch(
      request("/today", { mode: "navigate" }),
    );

    // The server answered, so its answer is what the page shows …
    expect(response?.status).toBe(502);
    // … but the copy kept for offline is still the last good one.
    expect(await (await cache.match("/today"))?.text()).toContain(
      "cached today",
    );
  });
});

describe("the API is network-only", () => {
  it("never answers an API read from the worker or stores it", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/api/v1/plan/week", new Response("stale"));

    const response = await worker.dispatch(request("/api/v1/plan/week"));

    // Left to the network untouched — not served from the cache, not fetched
    // through the worker into it.
    expect(response).toBeUndefined();
    expect(worker.fetch).not.toHaveBeenCalled();
  });

  // The read above is not a navigation, so the worker would ignore it even
  // without the network-only rule. A navigation is the request the
  // network-first handler WOULD take and cache, so only these fail when the
  // rule is removed.
  it.each(["/api/v1/plan/week", "/health", "/mcp"])(
    "leaves a navigation to %s to the network and stores nothing",
    async (path) => {
      const worker = boot();
      worker.fetch.mockImplementation(async () => new Response("{}"));

      const response = await worker.dispatch(
        request(path, { mode: "navigate" }),
      );

      expect(response).toBeUndefined();
      expect(worker.fetch).not.toHaveBeenCalled();
      const cache = await worker.caches.open("arc-shell-build-1");
      expect(await cache.keys()).toEqual([]);
    },
  );

  it("does not cache an API URL a page asks it to keep", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(async () => new Response("{}"));

    await worker.message({
      type: "cache-urls",
      urls: [`${ORIGIN}/api/v1/auth/session`, `${ORIGIN}/health`],
    });

    expect(worker.fetch).not.toHaveBeenCalled();
    const cache = await worker.caches.open("arc-shell-build-1");
    expect(await cache.keys()).toEqual([]);
  });

  it("lets writes through untouched", async () => {
    const worker = boot();

    expect(
      await worker.dispatch(
        request("/api/v1/manual-sessions", { method: "POST" }),
      ),
    ).toBeUndefined();
    expect(
      await worker.dispatch(
        request("/today", { method: "POST", mode: "navigate" }),
      ),
    ).toBeUndefined();
  });

  it("lets the MCP endpoint and other origins through untouched", async () => {
    const worker = boot();

    expect(await worker.dispatch(request("/mcp"))).toBeUndefined();
    expect(
      await worker.dispatch({
        ...request("/"),
        url: "https://fonts.example/x.woff2",
      }),
    ).toBeUndefined();
  });
});

describe("static assets: cache first", () => {
  it("does not refetch a build asset a page hands over twice", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/_next/static/chunks/a.js", new Response("cached chunk"));

    await worker.message({
      type: "cache-urls",
      urls: [`${ORIGIN}/_next/static/chunks/a.js`],
    });

    expect(worker.fetch).not.toHaveBeenCalled();
  });

  it("serves a cached build asset without the network", async () => {
    const worker = boot();
    const cache = await worker.caches.open("arc-shell-build-1");
    await cache.put("/_next/static/chunks/a.js", new Response("cached chunk"));

    const response = await worker.dispatch(
      request("/_next/static/chunks/a.js"),
    );

    expect(await response?.text()).toBe("cached chunk");
    expect(worker.fetch).not.toHaveBeenCalled();
  });

  it("fetches and keeps a build asset it has not seen", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(async () => new Response("new chunk"));

    const response = await worker.dispatch(
      request("/_next/static/chunks/b.js"),
    );

    expect(await response?.text()).toBe("new chunk");
    const cache = await worker.caches.open("arc-shell-build-1");
    expect(await (await cache.match("/_next/static/chunks/b.js"))?.text()).toBe(
      "new chunk",
    );
  });

  it("keeps what a page already loaded before the worker took over", async () => {
    const worker = boot();
    worker.fetch.mockImplementation(
      async (input) =>
        new Response(typeof input === "string" ? input : input.url),
    );

    await worker.message({
      type: "cache-urls",
      urls: [
        `${ORIGIN}/today`,
        `${ORIGIN}/_next/static/media/inter.woff2`,
        "https://elsewhere.example/_next/static/x.js",
      ],
    });

    const cache = await worker.caches.open("arc-shell-build-1");
    expect(
      (await cache.keys()).map(({ url }) => new URL(url).pathname).sort(),
    ).toEqual(["/_next/static/media/inter.woff2", "/today"]);
  });
});
