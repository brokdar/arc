import { describe, expect, it } from "vitest";

import {
  addDays,
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
  it("reads the local calendar date, not the UTC one", () => {
    // 23:30 local on the 15th is the 15th, whatever UTC thinks.
    const localLateEvening = new Date(2026, 7, 15, 23, 30);
    expect(todayIsoDate(localLateEvening)).toBe("2026-08-15");
  });
});
