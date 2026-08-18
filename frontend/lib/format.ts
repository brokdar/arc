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

/** A date and a clock reading, both already in the zone they were asked for. */
export interface LocalStamp {
  /** `YYYY-MM-DD`, ready for `formatDayMonthYear`. */
  readonly date: string;
  /** `HH:MM`, 24-hour. */
  readonly time: string;
}

/** `UTC+02:00` / `UTC-05:30` — the offset form the backend writes. */
const FIXED_OFFSET = /^UTC([+-])(\d{2}):(\d{2})$/;

/**
 * An instant, read in one session's own timezone.
 *
 * A completed session stores a UTC start plus the zone it was ridden in, and
 * that zone is one of exactly three things (`app.domain.activity.
 * parse_timezone`): the literal `UTC`, a fixed offset the head unit implied,
 * or an IANA name for the rare file that carries one. All three are resolved
 * here — the first two by arithmetic, the third by `Intl`, which is the only
 * thing that knows where a DST boundary falls.
 *
 * `null` for a zone that resolves to none of them, so a caller renders the
 * placeholder rather than a plausible-looking wrong time. The backend refuses
 * to *store* such a value, so this is the shape of a bug, not of a session.
 *
 * The one deliberate exception to this module's no-locale rule, and it is not
 * really one: the locale is pinned and the hour cycle forced, so the output is
 * the same on every machine — `Intl` is used as a timezone database, not as a
 * formatter.
 */
export function localStamp(
  isoInstant: string,
  timezone: string,
): LocalStamp | null {
  const instant = new Date(isoInstant);
  if (Number.isNaN(instant.getTime())) {
    return null;
  }
  if (timezone === "UTC") {
    return utcStamp(instant);
  }
  const fixed = FIXED_OFFSET.exec(timezone);
  if (fixed) {
    const magnitude = Number(fixed[2]) * 60 + Number(fixed[3]);
    const minutes = fixed[1] === "-" ? -magnitude : magnitude;
    return utcStamp(new Date(instant.getTime() + minutes * 60_000));
  }
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(instant);
  } catch {
    return null;
  }
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((entry) => entry.type === type)?.value ?? "";
  const [year, month, day, hour, minute] = [
    part("year"),
    part("month"),
    part("day"),
    part("hour"),
    part("minute"),
  ];
  if (!year || !month || !day || !hour || !minute) {
    return null;
  }
  return { date: `${year}-${month}-${day}`, time: `${hour}:${minute}` };
}

/** The UTC calendar date and clock reading of a `Date`. */
function utcStamp(instant: Date): LocalStamp {
  const iso = instant.toISOString();
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

/**
 * An instant on the athlete's own clock: `07.08 14:32`.
 *
 * For a moment the athlete has to *act* on — a deadline, an expiry. The
 * counterpart of `formatUtcStamp`, which is for a moment the *server* acted
 * at: that one is left in UTC because its column is headed with the zone, and
 * a deadline shown in a zone the athlete does not live in is a deadline they
 * will miss (issue #62, finding 8).
 *
 * `timezone` is the athlete's, from `useAthleteTimezone()`. Renders an em dash
 * rather than a plausible wrong time when the instant or the zone will not
 * resolve, for the reason `localStamp` returns null.
 */
export function formatAthleteStamp(
  isoInstant: string,
  timezone: string,
): string {
  const stamp = localStamp(isoInstant, timezone);
  return stamp === null
    ? EM_DASH
    : `${formatDayMonth(stamp.date)} ${stamp.time}`;
}

/**
 * A UTC instant as the ingest log prints it: `07.08 14:32`.
 *
 * Deliberately not converted to anywhere: the log records what the *server*
 * did, and its column is headed with the zone rather than each row carrying
 * one. Sessions get `localStamp`, because a ride happened somewhere, and a
 * deadline the athlete must act on gets `formatAthleteStamp`.
 *
 * **Every caller must print the zone beside it.** A bare UTC timestamp on a
 * screen otherwise about the athlete's local day is read as local, and is
 * wrong by the offset with nothing saying so.
 */
export function formatUtcStamp(isoInstant: string): string {
  const instant = new Date(isoInstant);
  if (Number.isNaN(instant.getTime())) {
    return EM_DASH;
  }
  const { date, time } = utcStamp(instant);
  return `${formatDayMonth(date)} ${time}`;
}

/**
 * Seconds as the headline says them: `3h10`, `45min`, `30s`.
 *
 * Prose, not a clock reading — `formatDurationHm` renders `3:10` for a column
 * of durations that must line up, and this renders the same duration for the
 * middle of a sentence, where a colon reads as a time of day.
 */
export function formatDurationWords(
  seconds: number | null | undefined,
): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return EM_DASH;
  }
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) {
    return `${total}s`;
  }
  if (total < 3600) {
    return `${Math.round(total / 60)}min`;
  }
  // Round to the minute first, so 3h59m40s becomes 4h rather than 3h60.
  const minutes = Math.round(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}h` : `${hours}h${String(rest).padStart(2, "0")}`;
}

/**
 * A step's length as an interval is spoken: `4′`, `1′30″`, `40″`.
 *
 * The primes are what "5×4′" is written with on a training plan, and the
 * headline composer is the only caller — everywhere else a duration belongs
 * in a column and gets `formatDurationClock`.
 */
export function formatMinutesPrime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return EM_DASH;
  }
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes === 0) {
    return `${rest}″`;
  }
  return rest === 0 ? `${minutes}′` : `${minutes}′${rest}″`;
}

/**
 * Read a duration a person typed. The inverse of `formatDurationClock`.
 *
 * Accepts `mm:ss` and `h:mm:ss` — and a bare number as **minutes**, because
 * that is what someone typing `40` into a field labelled "Duration" means.
 * Returns `null` for anything it cannot read, which is what the builder shows
 * as a validation message rather than silently prescribing zero.
 */
export function parseDurationInput(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const parts = trimmed.split(":");
  if (parts.length > 3) {
    return null;
  }
  if (parts.length === 1) {
    const minutes = Number(trimmed);
    return Number.isFinite(minutes) && minutes >= 0
      ? Math.round(minutes * 60)
      : null;
  }
  let total = 0;
  for (const part of parts) {
    const value = Number(part.trim());
    if (part.trim() === "" || !Number.isFinite(value) || value < 0) {
      return null;
    }
    total = total * 60 + value;
  }
  return Math.round(total);
}

/**
 * Read a number a person typed, or `null`.
 *
 * Every numeric field in the builder holds a *string*, because a half-typed
 * number is a legal thing to have in a form and coercing it to 0 on every
 * keystroke makes the field impossible to clear. Parsing happens once, here,
 * when the draft is turned into a payload.
 */
export function parseNumberInput(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/**
 * An anchor value as it is written: `250`, `162.5` — never `249.99999999`.
 *
 * A tenth is the whole resolution of the quantity (nobody tests to a
 * hundredth of a watt), and the rounding is the point: an anchor's absolute
 * bounds are floats computed from a percentage, and printing one raw puts
 * binary-float noise on the screen next to a number that was measured.
 */
export function formatAnchorValue(value: number): string {
  return String(Math.round(value * 10) / 10);
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
