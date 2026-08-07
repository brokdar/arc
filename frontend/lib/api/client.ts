import createFetchClient from "openapi-fetch";
import createClient from "openapi-react-query";

import { env } from "@/env";
import type { paths } from "@/generated/api/schema";
import { HTTP_STATUS } from "@/lib/api-errors";

/** The verbs openapi-fetch exposes, which are the ones worth wrapping. */
const METHODS = [
  "GET",
  "PUT",
  "POST",
  "DELETE",
  "OPTIONS",
  "HEAD",
  "PATCH",
  "TRACE",
] as const;

type OneCall = (
  ...args: never[]
) => Promise<{ error?: unknown; response: Response }>;

/**
 * Tag every failed response's body with the status it came with.
 *
 * openapi-fetch hands back `{data, error, response}`; openapi-react-query
 * throws `error` and drops `response`, so by the time a component sees a
 * failure the status is gone and "is the API reachable?" is the only thing
 * left to say — including for a 401, where the true answer is "log in again"
 * (`loadFailureMessage`). This is the one place both halves are still in hand.
 *
 * The tag is a symbol property (`HTTP_STATUS`), so the body is not altered in
 * any way a spread, a `JSON.stringify` or a schema check can see: it is still
 * exactly the payload the server sent, with one invisible annotation on it.
 * Middleware could not do this — `onResponse` runs before the body is parsed,
 * so tagging there would mean rewriting the JSON the server actually sent.
 */
function recordingStatus<T extends object>(client: T): T {
  const source = client as unknown as Record<string, unknown>;
  const wrapped: Record<string, unknown> = { ...source };
  for (const method of METHODS) {
    const call = source[method];
    if (typeof call !== "function") {
      continue;
    }
    wrapped[method] = async (...args: never[]) => {
      const result = await (call as OneCall).apply(client, args);
      if (typeof result.error === "object" && result.error !== null) {
        Object.defineProperty(result.error, HTTP_STATUS, {
          value: result.response.status,
        });
      }
      return result;
    };
  }
  return wrapped as T;
}

/** Plain fetch client — use in server components and route handlers. */
export const apiClient = recordingStatus(
  createFetchClient<paths>({
    baseUrl: env.NEXT_PUBLIC_API_BASE_URL,
    // The API authenticates with a session cookie. Same-origin (behind Caddy)
    // this is redundant; cross-origin (bare-metal `bun dev` on :3000 talking to
    // :8000) it is what makes the browser send and store the cookie at all.
    credentials: "include",
    // Resolve fetch at call time, not module-import time, so interceptors
    // installed later (MSW in tests) are honored.
    fetch: (request) => globalThis.fetch(request),
  }),
);

/** TanStack Query bindings — use in client components ($api.useQuery, ...). */
export const $api = createClient(apiClient);
