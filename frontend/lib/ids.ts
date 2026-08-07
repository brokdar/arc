/**
 * The guard on an identifier that arrived from outside the application.
 *
 * A sibling of `isIsoDate` in `lib/dates.ts`, and for the same reason: a query
 * string is typed by whoever pasted the link, and a value taken from one has
 * to be checked before it is spent on a request. `/calendar?session=<garbage>`
 * must not become `GET /planned-sessions/<garbage>` — the id is a path
 * segment, so anything at all would be sent, and the answer would be a 404
 * rendered as though the session had been deleted.
 */

/**
 * The shape only: eight-four-four-four-twelve hexadecimal digits.
 *
 * Deliberately not a version check. Every id this application mints is a
 * uuid7 (`app/persistence/types.py`), but the guard's job is to reject the
 * things a URL actually carries — a word, a number, a path — not to police
 * which uuid variant a future row was written with.
 */
const UUID_SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether `value` is shaped like a uuid, and can therefore be sent as one. */
export function isUuid(value: string | null | undefined): value is string {
  return typeof value === "string" && UUID_SHAPE.test(value);
}
