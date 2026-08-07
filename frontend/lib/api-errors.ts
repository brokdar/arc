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
