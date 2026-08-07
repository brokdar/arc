/**
 * Display formatting for the numerals the UI is made of.
 *
 * Every value produced here is meant to be rendered in the mono face — the
 * design system reserves JetBrains Mono for durations, dates, counts and
 * percentages so columns of them line up. Pure functions, no locale lookups:
 * the app is single-athlete and self-hosted, and a calendar that renders
 * `28.07` on one machine and `7/28` on another is worse than one that always
 * renders the same thing.
 */

const EM_DASH = "—";

/**
 * Seconds as `h:mm` — the calendar card's duration (`0:42`, `3:10`).
 *
 * Returns an em dash for a missing duration so a card keeps its shape when the
 * prescription has none (a strength session, an unstructured ride).
 */
export function formatDurationHm(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return EM_DASH;
  }
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours}:${String(minutes).padStart(2, "0")}`;
}

/**
 * Seconds as a stopwatch reading: `4:00`, `1:08:42`.
 *
 * Used for step durations inside a workout, where minutes:seconds is the unit
 * an athlete thinks in, unlike the h:mm above.
 */
export function formatDurationClock(
  seconds: number | null | undefined,
): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return EM_DASH;
  }
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
  return hours > 0
    ? `${hours}:${mm}:${String(secs).padStart(2, "0")}`
    : `${mm}:${String(secs).padStart(2, "0")}`;
}

/** An ISO date (`2026-07-28`) as `28.07`. */
export function formatDayMonth(isoDate: string): string {
  const [, month, day] = splitIsoDate(isoDate);
  return `${day}.${month}`;
}

/** An ISO date (`2026-07-28`) as `28.07.2026`. */
export function formatDayMonthYear(isoDate: string): string {
  const [year, month, day] = splitIsoDate(isoDate);
  return `${day}.${month}.${year}`;
}

/** A fraction (`0.75`) as a whole-number percentage (`75%`). */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** `3 sets` / `1 set` — the strength card's stand-in for a duration. */
export function formatSets(sets: number | null | undefined): string {
  if (sets === null || sets === undefined) {
    return EM_DASH;
  }
  return `${sets} ${sets === 1 ? "set" : "sets"}`;
}

function splitIsoDate(isoDate: string): [string, string, string] {
  const [year = "", month = "", day = ""] = isoDate.slice(0, 10).split("-");
  return [year, month, day];
}
