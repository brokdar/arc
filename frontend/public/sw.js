/*
 * arc's service worker: the app shell, so the installed app opens without a
 * network.
 *
 * Hand-written, and it stays plain JavaScript at the origin root: a worker
 * controls only the paths under the URL it was served from, so `/sw.js` is
 * what lets it answer for `/today`, and there is no bundler between this file
 * and the browser. Displaced: Next's `experimental.useOffline`, which retries
 * failed navigations but caches nothing — it is navigation-only and
 * experimental, and a gym with no signal needs the page itself, not a retry.
 * Displaced too: a library (Serwist), which would add a build step to own
 * four routing rules.
 *
 * The rules, in the order `fetch` applies them:
 *
 * 1. Anything but a same-origin GET goes to the network untouched.
 * 2. `/api/`, `/health` and `/mcp` are network-only. The worker never answers
 *    them and never stores them: a stale plan or session answer served as if
 *    it were current is worse than an honest failure, and offline data is
 *    the outbox's and the session packet's job, not this cache's.
 * 3. Navigations are network-first. The server's page is served and a good
 *    (2xx) copy kept; when the network fails or hangs past
 *    NAVIGATION_TIMEOUT_MS, the kept copy is served, and for a route never
 *    opened online, the app's own `/offline` page instead of the browser's
 *    error. Keeping the HTML is safe because every page is a client-rendered
 *    shell — athlete data arrives from the API, which rule 2 never caches.
 *    A logging screen (`/train/<id>`) never opened online is answered with
 *    the precached `/train/shell`: the screen reads its session id from the
 *    address bar and its numbers from the stored session packet, so one copy
 *    serves every session — and the gym can start one it has never seen.
 * 4. Build assets (`/_next/static/`, content-hashed and immutable) and the
 *    icons are cache-first.
 *
 * Versioning: the page registers `/sw.js?v=<build version>`
 * (`components/shell/sw-register.tsx`), so every build is a new script URL.
 * The new worker installs on the next online load, skips waiting, claims the
 * open pages and deletes the previous build's `arc-shell-*` cache — a phone
 * never runs one build's HTML against another build's chunks for longer than
 * that one load.
 *
 * Tests: `tests/sw.test.ts` runs this file against a stand-in `self`.
 */

const VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const CACHE_PREFIX = "arc-shell-";
const CACHE = `${CACHE_PREFIX}${VERSION}`;
const OFFLINE_URL = "/offline";
const TRAIN_SHELL_URL = "/train/shell";
/**
 * Today is what the gym opens, the logging screen what it opens next; the
 * offline page answers everything unseen.
 */
const SHELL = ["/today", TRAIN_SHELL_URL, OFFLINE_URL];
const TRAIN = /^\/train\/[^/]+$/;
/** Past this, a navigation stops waiting on a network that only half works. */
const NAVIGATION_TIMEOUT_MS = 4000;
const NETWORK_ONLY = /^\/(?:api\/|health$|mcp)/;
const STATIC_ASSET = /^\/(?:_next\/static\/|icons\/)/;
/** `/_next/static/…` paths as they appear in HTML and in the flight data. */
const ASSET_IN_HTML = /\/_next\/static\/[A-Za-z0-9_\-./~%@]+/g;

/** Store a response if it is a good one; a failed write never fails a fetch. */
async function keep(cache, url, response) {
  if (!response?.ok) return;
  try {
    await cache.put(url, response);
  } catch {
    // Quota or a body that cannot be stored: serve without keeping a copy.
  }
}

async function fetchAndKeep(cache, url) {
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    await keep(cache, url, response.clone());
    return response;
  } catch {
    return undefined;
  }
}

async function precache() {
  const cache = await caches.open(CACHE);
  const assets = new Set();
  for (const url of SHELL) {
    const response = await fetchAndKeep(cache, url);
    if (response?.ok) {
      const text = await response.text();
      for (const asset of text.matchAll(ASSET_IN_HTML)) {
        assets.add(asset[0].replace(/\\+$/, ""));
      }
    }
  }
  await Promise.all([...assets].map((asset) => fetchAndKeep(cache, asset)));
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE)
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

function timeout(ms) {
  return new Promise((resolve) => setTimeout(() => resolve(undefined), ms));
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  const network = fetch(request).then(
    async (response) => {
      await keep(cache, request.url, response.clone());
      return response;
    },
    () => undefined,
  );
  const response = await Promise.race([
    network,
    timeout(NAVIGATION_TIMEOUT_MS),
  ]);
  if (response) return response;
  const train = TRAIN.test(new URL(request.url).pathname)
    ? await cache.match(TRAIN_SHELL_URL)
    : undefined;
  return (
    (await cache.match(request.url, { ignoreSearch: true })) ||
    train ||
    (await cache.match(OFFLINE_URL)) ||
    Response.error()
  );
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request.url);
  if (cached) return cached;
  const response = await fetch(request);
  await keep(cache, request.url, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (NETWORK_ONLY.test(url.pathname)) return;
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }
  if (STATIC_ASSET.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
  }
});

/** Whether a URL a page hands over is one this cache may hold. */
function cacheable(href) {
  let url;
  try {
    url = new URL(href, self.location.origin);
  } catch {
    return false;
  }
  return (
    url.origin === self.location.origin && !NETWORK_ONLY.test(url.pathname)
  );
}

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "version") {
    event.ports[0]?.postMessage({ type: "version", version: VERSION });
    return;
  }
  if (data.type === "cache-urls" && Array.isArray(data.urls)) {
    // What the page loaded before this worker controlled it — its own HTML,
    // chunks and fonts — so the first visit is already enough to open offline.
    event.waitUntil(
      (async () => {
        const cache = await caches.open(CACHE);
        await Promise.all(
          data.urls.filter(cacheable).map(async (href) => {
            // Build assets never change under one URL; refetch only pages.
            const path = new URL(href, self.location.origin).pathname;
            if (STATIC_ASSET.test(path) && (await cache.match(href))) return;
            await fetchAndKeep(cache, href);
          }),
        );
      })(),
    );
  }
});
