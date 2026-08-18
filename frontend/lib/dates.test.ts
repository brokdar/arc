import { describe, expect, it } from "vitest";

import {
  addDays,
  isIsoDate,
  isoWeekNumber,
  mondayOf,
  todayIsoDate,
  weekDates,
  weekdayLabel,
} from "@/lib/dates";

describe("mondayOf", () => {
  it("keeps a Monday where it is", () => {
    expect(mondayOf("2026-07-27")).toBe("2026-07-27");
  });

  it("walks back from any other day", () => {
    expect(mondayOf("2026-07-28")).toBe("2026-07-27");
    expect(mondayOf("2026-08-01")).toBe("2026-07-27");
  });

  it("treats Sunday as the end of its week, not the start", () => {
    expect(mondayOf("2026-08-02")).toBe("2026-07-27");
  });
});

describe("addDays", () => {
  it("crosses month and year boundaries", () => {
    expect(addDays("2026-07-31", 1)).toBe("2026-08-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("crosses a spring-forward boundary without losing a day", () => {
    // 29.03.2026 is the European DST switch; a local-midnight Date would
    // land on the 28th here.
    expect(addDays("2026-03-28", 1)).toBe("2026-03-29");
    expect(addDays("2026-03-29", 1)).toBe("2026-03-30");
  });

  it("is its own inverse", () => {
    expect(addDays(addDays("2026-08-01", 7), -7)).toBe("2026-08-01");
  });
});

describe("weekDates", () => {
  it("returns seven consecutive dates", () => {
    expect(weekDates("2026-07-27")).toEqual([
      "2026-07-27",
      "2026-07-28",
      "2026-07-29",
      "2026-07-30",
      "2026-07-31",
      "2026-08-01",
      "2026-08-02",
    ]);
  });
});

describe("weekdayLabel", () => {
  it("labels Monday-first", () => {
    expect(weekdayLabel("2026-07-27")).toBe("Mon");
    expect(weekdayLabel("2026-08-02")).toBe("Sun");
  });
});

describe("isoWeekNumber", () => {
  it("numbers a mid-year week", () => {
    expect(isoWeekNumber("2026-07-27")).toBe(31);
    expect(isoWeekNumber("2026-08-01")).toBe(31);
  });

  it("puts a turn-of-year week in the year its Thursday falls in", () => {
    // 2026-01-01 is a Thursday, so that week is week 1 of 2026 and the days
    // before it belong to it too.
    expect(isoWeekNumber("2025-12-29")).toBe(1);
    expect(isoWeekNumber("2026-01-01")).toBe(1);
  });
});

describe("todayIsoDate", () => {
  // 2026-08-15 12:00 UTC. Late evening in Kiritimati (UTC+14, already the
  // 16th), still the morning of the 15th in Midway (UTC-11) — one instant,
  // two calendar days, which is the whole of what this function decides.
  const NOON_UTC = new Date("2026-08-15T12:00:00Z");

  it("reads the given zone's calendar date, not the browser's", () => {
    expect(todayIsoDate("Pacific/Kiritimati", NOON_UTC)).toBe("2026-08-16");
    expect(todayIsoDate("Pacific/Midway", NOON_UTC)).toBe("2026-08-15");
  });

  it("resolves a region name through the zone database, not as an offset", () => {
    // Europe/Berlin is +02:00 in July and +01:00 in January, and 23:30 UTC on
    // 31 December is the 1st there either way only if the offset is applied at
    // all. Read as a fixed +02:00 the winter answer would be an hour out — the
    // hour that decides the day.
    const newYearsEve = new Date("2026-12-31T23:30:00Z");
    expect(todayIsoDate("Europe/Berlin", newYearsEve)).toBe("2027-01-01");
    const midsummerNight = new Date("2026-06-30T22:30:00Z");
    expect(todayIsoDate("Europe/Berlin", midsummerNight)).toBe("2026-07-01");
    // Same clock time in winter is *not* yet the next day, because the offset
    // is an hour smaller.
    const winterNight = new Date("2026-12-30T22:30:00Z");
    expect(todayIsoDate("Europe/Berlin", winterNight)).toBe("2026-12-30");
  });

  it("takes the three forms the backend can serve", () => {
    expect(todayIsoDate("UTC", NOON_UTC)).toBe("2026-08-15");
    expect(todayIsoDate("UTC+14:00", NOON_UTC)).toBe("2026-08-16");
    expect(todayIsoDate("UTC-11:00", NOON_UTC)).toBe("2026-08-15");
  });

  it("falls back to the browser rather than rendering no date at all", () => {
    // The backend refuses to serve a zone this cannot resolve, so reaching
    // here is the shape of a bug — and a page with no date is worse than one
    // an hour out. The runner is pinned to Pacific/Midway.
    expect(todayIsoDate("Not/AZone", NOON_UTC)).toBe("2026-08-15");
  });
});

describe("isIsoDate", () => {
  it("accepts a real calendar day written the one way the API writes it", () => {
    expect(isIsoDate("2026-08-03")).toBe(true);
    expect(isIsoDate("2024-02-29")).toBe(true);
  });

  it("refuses anything a query string could otherwise smuggle through", () => {
    // Wrong shape …
    expect(isIsoDate("next-week")).toBe(false);
    expect(isIsoDate("2026-8-1")).toBe(false);
    expect(isIsoDate("2026-08-03T00:00:00Z")).toBe(false);
    expect(isIsoDate("")).toBe(false);
    expect(isIsoDate(null)).toBe(false);
    expect(isIsoDate(undefined)).toBe(false);
    // … and the right shape naming no day, which `Date.UTC` would happily
    // roll over into the following month.
    expect(isIsoDate("2026-02-31")).toBe(false);
    expect(isIsoDate("2026-13-01")).toBe(false);
    expect(isIsoDate("2025-02-29")).toBe(false);
  });
});
