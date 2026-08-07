/**
 * Turn whatever a failed mutation threw into sentences a person can act on.
 *
 * The API answers a rejected write with one of two 422 shapes (`ErrorDetail`'s
 * sentence, or FastAPI's list of per-field errors — the backend declares both,
 * deliberately), a 404's `{detail}`, or nothing at all when the request never
 * arrived. openapi-fetch hands the parsed body to react-query as the error, so
 * "the error" here is a body, not an `Error`.
 *
 * The point is that a save that fails says *why* on the form, next to the
 * fields, rather than failing silently and leaving the athlete to guess which
 * of forty inputs the server disliked.
 */
export function apiErrorMessages(error: unknown): string[] {
  if (!error) {
    return [];
  }
  if (typeof error === "string") {
    return [error];
  }
  if (error instanceof Error) {
    return ["Could not reach the server. Try again."];
  }
  if (typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return [detail];
    }
    if (Array.isArray(detail)) {
      return detail.map(describeFieldError);
    }
  }
  return ["The server refused the change."];
}

/**
 * Where a failed response's HTTP status is recorded on the body it answered with.
 *
 * A **symbol**, and deliberately: openapi-react-query throws the parsed error
 * *body* and drops the `Response` beside it, so a component holds `{detail:
 * "..."}` and no status at all. `lib/api/client.ts` closes that gap by tagging
 * the body as it goes past. A symbol key cannot collide with a field the API
 * might one day add, and `JSON.stringify`, `Object.keys` and object spread all
 * ignore it — so a body carrying this is still, byte for byte, the body the
 * server sent.
 */
export const HTTP_STATUS = Symbol.for("arc.api.httpStatus");

/** Whether a failure was the session guard's 401 rather than anything else. */
export function isUnauthorized(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as Record<symbol, unknown>)[HTTP_STATUS] === 401
  );
}

/**
 * Why a page could not load, told apart from *whether* it could.
 *
 * "Is the API reachable?" is the wrong question when the API answered — and a
 * 401 is an answer. The session guard's 401 is reachable while a page is open:
 * `AuthGuard` bounces a visitor whose session is already gone, but a cookie
 * that expires *under* an open page leaves the guard's cached answer saying
 * yes until it refetches, and every query in the meantime is a 401. The remedy
 * for that is logging in, not checking the network.
 */
export function loadFailureMessage(error: unknown, subject: string): string {
  return isUnauthorized(error)
    ? `Your session has expired. Log in again to see ${subject}.`
    : `Could not load ${subject}. Is the API reachable?`;
}

/** One entry of FastAPI's validation list: `{loc, msg}` → `body.name: required`. */
function describeFieldError(entry: unknown): string {
  if (typeof entry !== "object" || entry === null) {
    return "Invalid value.";
  }
  const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
  const where = Array.isArray(loc)
    ? loc
        .filter((part) => part !== "body")
        .map((part) => String(part))
        .join(".")
    : "";
  const message = typeof msg === "string" ? msg : "Invalid value.";
  return where ? `${where}: ${message}` : message;
}
