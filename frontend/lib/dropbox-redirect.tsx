"use client";

import { useEffect, useState } from "react";

import type { components } from "@/generated/api/schema";

type IntegrationKind = components["schemas"]["IntegrationKind"];

/**
 * The page Dropbox sends the athlete back to, and how the flow picks up again.
 *
 * Everything here is about one round trip that leaves the application: the
 * athlete clicks connect, arc hands the browser to dropbox.com, and the tab
 * comes back to a URL arc chose. Two facts have to survive that gap, and they
 * are kept in different places for different reasons — the **nonce** on the
 * server, because only the server may judge it, and **which integration was
 * being added** in `sessionStorage`, because it is the athlete's own place in
 * a wizard and no part of it is arc's to prove.
 */

/** The callback route, spelled once. The redirect URI is this on the origin. */
export const DROPBOX_CALLBACK_PATH = "/settings/dropbox/callback";

/**
 * Hosts Dropbox will redirect back to over plain `http`.
 *
 * `[::1]` carries its brackets because that is what `URL.hostname` reports in
 * a browser; the backend's copy drops them because that is what Python's
 * `urlsplit` reports. Both spellings are here so neither environment can be
 * the one that is wrong.
 */
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/**
 * Whether Dropbox will redirect back to this origin at all.
 *
 * **A mirror of `app.connectors.dropbox.redirect_eligible`, and the server is
 * the authority.** This copy exists to decide what to *offer*: which flow the
 * connect button starts, and whether the registration checklist asks the
 * athlete to register a redirect URI Dropbox would refuse. The server decides
 * what to *accept*, and refuses with a sentence — so the worst a drift between
 * the two can do is show that sentence, never connect an account arc should
 * not have connected. `frontend/lib/dropbox-redirect.test.ts` and
 * `backend/tests/unit/test_connectors_dropbox.py` walk the same table of URIs,
 * which is what makes a drift a failing test rather than a support question.
 *
 * The rule is Dropbox's: https anywhere, or http on the loopback. Everything
 * else — arc reached at `http://192.168.1.50` from the sofa, which is a normal
 * way to run a self-hosted application — connects by paste instead.
 */
export function redirectEligible(origin: string): boolean {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (url.hostname === "") {
    return false;
  }
  if (url.protocol === "https:") {
    return true;
  }
  return url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname);
}

/** The redirect URI to register with Dropbox, for a browser at this origin. */
export function redirectUriFor(origin: string): string {
  return new URL(DROPBOX_CALLBACK_PATH, origin).toString();
}

/**
 * The origin the browser is actually at, once there is a browser.
 *
 * Read in an effect rather than during render: these components are
 * prerendered on the server, where `window` does not exist, and a lazy
 * `useState` initialiser would either throw there or hydrate to a different
 * tree than it rendered. `null` until mounted, which every caller treats as
 * "not decided yet" rather than "not eligible".
 */
export function useBrowserOrigin(): string | null {
  const [origin, setOrigin] = useState<string | null>(null);
  useEffect(() => setOrigin(window.location.origin), []);
  return origin;
}

/** Where the add flow was when the browser left for dropbox.com. */
export interface AddFlowResume {
  /** The integration being added, or `null` when it was not chosen yet. */
  readonly kind: IntegrationKind | null;
}

/**
 * Where the resumption marker lives.
 *
 * `sessionStorage`, not the query string: UI convention 1 keeps transient
 * state out of the URL, and this is as transient as it gets — a half-finished
 * wizard in one tab. It is also not arc's to publish. The URL Dropbox
 * redirects to is registered by the athlete in a console page, so anything arc
 * wanted to round-trip through it would have to be registered too.
 */
const RESUME_KEY = "arc.dropbox-connect.resume";

/** `sessionStorage`, or null where there is none (prerender, locked-down UA). */
function store(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Remember which integration is being added, before leaving for Dropbox. */
export function rememberAddFlow(kind: IntegrationKind | null): void {
  store()?.setItem(RESUME_KEY, JSON.stringify({ kind }));
}

/**
 * Make sure the panel opens the add flow next time it mounts.
 *
 * Weaker than `rememberAddFlow`: it never overwrites a remembered
 * integration, it only guarantees there is *something* to resume. What the
 * callback page writes when it is about to send the athlete back to Settings
 * — landing them on a bare Settings page after they authorised an account
 * would leave them to work out that the folder step is still owed.
 */
export function keepAddFlowOpen(): void {
  if (store()?.getItem(RESUME_KEY) == null) {
    rememberAddFlow(null);
  }
}

/** Read the marker and clear it: resuming happens once, not on every mount. */
export function takeAddFlow(): AddFlowResume | null {
  const raw = store()?.getItem(RESUME_KEY) ?? null;
  if (raw === null) {
    return null;
  }
  store()?.removeItem(RESUME_KEY);
  try {
    const parsed: unknown = JSON.parse(raw);
    const kind =
      typeof parsed === "object" && parsed !== null && "kind" in parsed
        ? (parsed as { kind: unknown }).kind
        : null;
    return {
      kind: typeof kind === "string" ? (kind as IntegrationKind) : null,
    };
  } catch {
    // A marker somebody else wrote, or one from an older release. Opening the
    // flow at the catalogue is the right answer either way.
    return { kind: null };
  }
}
