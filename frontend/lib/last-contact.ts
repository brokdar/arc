import { HTTP_STATUS } from "@/lib/api-errors";

/**
 * What this phone last heard from the server, kept for when it cannot ask.
 *
 * The installed app opens offline from the service worker's cache, and the
 * two gates above every signed-in page — `AuthGuard` and `ClockProvider` —
 * each wait on a request that cannot be answered without a network. Without a
 * memory of their last answers, a gym with no signal shows "Is the API
 * reachable?" instead of Today. So each gate records the server's answer when
 * it gets one and falls back to it only when the request **never reached a
 * server** (`isUnreachable`). A server that answered with an error is still
 * an error; a 401 still sends the athlete to log in.
 *
 * Browser storage, not a runtime store, because the fact is about *this
 * device*: whether it was signed in, and when it last synced. Every read and
 * write is wrapped — a private window or blocked storage answers "nothing
 * remembered", which is the pre-offline behaviour, never a crash.
 *
 * None of it is a security boundary. The API rejects every call without a
 * valid session cookie whatever this says; what it unlocks is the cached
 * shell, which holds no athlete data.
 */

const CONTACT_KEY = "arc:last-contact";
const TIMEZONE_KEY = "arc:athlete-timezone";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Storage blocked: nothing is remembered, and the gates behave as before.
  }
}

/** Record a signed-in answer from the server at `at`. */
export function rememberContact(at: Date): void {
  write(CONTACT_KEY, at.toISOString());
}

/** The server said there is no session: an offline open must not pretend. */
export function forgetContact(): void {
  write(CONTACT_KEY, null);
}

/** When this phone last heard "signed in" from the server, if ever. */
export function lastContact(): Date | null {
  const stored = read(CONTACT_KEY);
  if (stored === null) return null;
  const at = new Date(stored);
  return Number.isNaN(at.getTime()) ? null : at;
}

/** Record the athlete's zone exactly as `GET /clock` served it. */
export function rememberTimezone(timezone: string): void {
  write(TIMEZONE_KEY, timezone);
}

/** The zone `GET /clock` last served on this phone — never a guess. */
export function lastTimezone(): string | null {
  return read(TIMEZONE_KEY);
}

/**
 * Whether a failed query failed because no server answered at all.
 *
 * `lib/api/client.ts` tags every error that came with a response with its
 * HTTP status; an error without the tag is a `fetch` that rejected — no
 * network, or a server that cannot be reached from here.
 */
export function isUnreachable(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return true;
  return !(HTTP_STATUS in error);
}
