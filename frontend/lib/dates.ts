/**
 * Calendar arithmetic on ISO date strings (`YYYY-MM-DD`).
 *
 * Plan dates are *athlete-local calendar dates*, not instants — the backend
 * stores them as `date` columns and the week endpoint takes and returns them
 * as `YYYY-MM-DD`. So this module never converts to or from a timestamp:
 * everything happens in UTC-noon `Date` objects that exist only long enough to
 * add days, which keeps a DST boundary or a negative timezone offset from
 * quietly shifting a session onto the day before.
 */

import { localStamp } from "@/lib/format";

const DAY_MS = 86_400_000;

/** Weekday labels, Monday-first — the order the calendar grid renders in. */
export const WEEKDAY_LABELS = [
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
  "Sun",
] as const;

/** Parse `YYYY-MM-DD` into a UTC-noon Date. Noon, so ±12h never crosses a day. */
function parseIsoDate(isoDate: string): Date {
  const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1, 12));
}

/** Render a Date's UTC calendar date as `YYYY-MM-DD`. */
export function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Whether `value` is a `YYYY-MM-DD` string naming a real calendar day.
 *
 * The guard on anything that arrives from outside the application — a query
 * string, a pasted link — before it is handed to the arithmetic above or sent
 * to the API as a `start`. Both checks are needed: the shape rejects `next`
 * and `2026-8-1`, and the round-trip rejects `2026-02-31`, which `Date.UTC`
 * would otherwise roll over into March without complaint.
 */
export function isIsoDate(value: string | null | undefined): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  return toIsoDate(parseIsoDate(value)) === value;
}

/**
 * The athlete's *local* today, as an ISO date.
 *
 * `timeZone` is the athlete's own — `MATCHING__TIMEZONE`, served by
 * `GET /clock` and held by the `ClockProvider`. Ask for it with
 * `useAthleteToday()` rather than calling this directly; the argument exists
 * so this stays a pure function the tests can drive.
 *
 * It used to take no zone at all and read the **browser's**, while its
 * docstring said "the athlete's local today". Those are two different clocks
 * the moment the athlete travels or the laptop is set wrong, and this function
 * decided which week the calendar opened on, which day the page named "Today"
 * showed, and — through two form defaults — which `local_date` a wellness
 * reading and which `effective_date` an appended FTP were filed under
 * (issue #62, finding 3).
 *
 * Falls back to the browser's calendar date only when the zone cannot be
 * resolved at all, which the backend refuses to store: that is the shape of a
 * bug, and a page with no date at all is worse than one an hour out.
 */
export function todayIsoDate(timeZone: string, now: Date = new Date()): string {
  return localStamp(now.toISOString(), timeZone)?.date ?? browserToday(now);
}

/** The browser's own calendar date. The fallback, and never the answer. */
function browserToday(now: Date): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** `isoDate` shifted by `days` (negative shifts backwards). */
export function addDays(isoDate: string, days: number): string {
  return toIsoDate(new Date(parseIsoDate(isoDate).getTime() + days * DAY_MS));
}

/**
 * The Monday of the week `isoDate` falls in.
 *
 * Monday-first weeks are an ISO-8601 convention and the one the mockup's grid
 * uses; the API takes whatever `start` it is given literally, so picking
 * the Monday is the client's job.
 */
export function mondayOf(isoDate: string): string {
  const date = parseIsoDate(isoDate);
  // getUTCDay: 0 = Sunday. Map to 0 = Monday … 6 = Sunday.
  const offset = (date.getUTCDay() + 6) % 7;
  return addDays(isoDate, -offset);
}

/** The ISO-8601 week number of `isoDate` — the "Week 31" in the header. */
export function isoWeekNumber(isoDate: string): number {
  // Shift to the Thursday of the same week: the ISO year is the year that
  // Thursday falls in, which is what makes the turn-of-year weeks come out
  // right without special-casing them.
  const thursday = parseIsoDate(addDays(mondayOf(isoDate), 3));
  const firstOfYear = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 1, 12));
  const days = Math.round(
    (thursday.getTime() - firstOfYear.getTime()) / DAY_MS,
  );
  return Math.floor(days / 7) + 1;
}

/** The seven ISO dates of the week starting at `startIsoDate`. */
export function weekDates(startIsoDate: string): string[] {
  return Array.from({ length: 7 }, (_, index) => addDays(startIsoDate, index));
}

/** Short weekday label for an ISO date (`Mon`), Monday-first. */
export function weekdayLabel(isoDate: string): string {
  const index = (parseIsoDate(isoDate).getUTCDay() + 6) % 7;
  return WEEKDAY_LABELS[index] ?? "";
}
