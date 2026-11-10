"use client";

import { useEffect } from "react";

import { env } from "@/env";

/** The slice of `ServiceWorkerContainer` registration needs. */
interface Container {
  register(
    url: string,
    options: RegistrationOptions,
  ): Promise<{ active?: { postMessage(message: unknown): void } | null }>;
  readonly ready: Promise<{
    active?: { postMessage(message: unknown): void } | null;
  }>;
  readonly controller: unknown;
  addEventListener(type: "controllerchange", listener: () => void): void;
}

/** Resolve once a worker controls this page (at once if one already does). */
function controlled(container: Container): Promise<void> {
  if (container.controller) return Promise.resolve();
  return new Promise((resolve) => {
    container.addEventListener("controllerchange", () => resolve());
  });
}

/** Paths the worker may keep; the API is network-only (`public/sw.js`). */
const ASSET = /^\/_next\/static\//;

function sameOriginAssets(hrefs: readonly string[]): string[] {
  return hrefs.filter((href) => {
    const url = new URL(href, location.href);
    return url.origin === location.origin && ASSET.test(url.pathname);
  });
}

/**
 * Register `/sw.js` for this build and hand it what this page already loaded.
 *
 * The version in the URL is the build's (`next.config.ts`), so a deploy is a
 * new script URL and the browser installs the new worker on the next online
 * load. `updateViaCache: "none"` keeps the HTTP cache out of that check, on
 * top of the `no-cache` header.
 *
 * The first page a phone opens loaded its chunks and fonts *before* any
 * worker existed to see them, so once the worker controls the page it is sent
 * this page's URL and every asset loaded so far (`loaded`, read only then).
 * Requests already in flight when the worker took over never pass through
 * it either — Next prefetches route chunks right after hydration — so
 * `onLaterLoad` reports each asset that finishes loading afterwards *without*
 * the worker, and those are sent too. One online visit is then enough to open
 * offline. A browser that refuses the worker (an insecure origin, a private
 * window) costs nothing but the offline shell.
 */
export async function registerServiceWorker(
  container: Container,
  version: string,
  loaded: () => readonly string[],
  onLaterLoad: (send: (hrefs: readonly string[]) => void) => void = () => {},
): Promise<void> {
  try {
    await container.register(`/sw.js?v=${encodeURIComponent(version)}`, {
      scope: "/",
      updateViaCache: "none",
    });
    const registration = await container.ready;
    await controlled(container);
    registration.active?.postMessage({
      type: "cache-urls",
      urls: [location.href, ...sameOriginAssets(loaded())],
    });
    onLaterLoad((hrefs) => {
      const urls = sameOriginAssets(hrefs);
      if (urls.length > 0) {
        registration.active?.postMessage({ type: "cache-urls", urls });
      }
    });
  } catch {
    // No worker: the app still works online, it just cannot open offline.
  }
}

/**
 * Mounted once in the root layout. Production builds only: in `next dev` a
 * worker would cache chunks the dev server rewrites on every save.
 */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    let observer: PerformanceObserver | undefined;
    void registerServiceWorker(
      navigator.serviceWorker,
      env.NEXT_PUBLIC_SW_VERSION,
      () => performance.getEntriesByType("resource").map((entry) => entry.name),
      (send) => {
        observer = new PerformanceObserver((list) => {
          send(
            list
              .getEntries()
              // `workerStart` is 0 for a request the worker never saw.
              .filter(
                (entry) =>
                  (entry as PerformanceResourceTiming).workerStart === 0,
              )
              .map((entry) => entry.name),
          );
        });
        observer.observe({ type: "resource" });
      },
    );
    return () => observer?.disconnect();
  }, []);
  return null;
}
