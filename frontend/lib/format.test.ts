import { describe, expect, it } from "vitest";

import {
  formatDayMonth,
  formatDayMonthYear,
  formatDurationClock,
  formatDurationHm,
  formatPercent,
  formatSets,
} from "@/lib/format";

describe("formatDurationHm", () => {
  it("renders the calendar card's h:mm", () => {
    expect(formatDurationHm(2520)).toBe("0:42");
    expect(formatDurationHm(4140)).toBe("1:09");
    expect(formatDurationHm(11400)).toBe("3:10");
  });

  it("pads the minutes so a column of durations lines up", () => {
    expect(formatDurationHm(3660)).toBe("1:01");
  });

  it("keeps a card's shape when there is no duration", () => {
    expect(formatDurationHm(null)).toBe("—");
    expect(formatDurationHm(undefined)).toBe("—");
    expect(formatDurationHm(Number.NaN)).toBe("—");
  });
});

describe("formatDurationClock", () => {
  it("renders minutes:seconds below an hour", () => {
    expect(formatDurationClock(240)).toBe("4:00");
    expect(formatDurationClock(65)).toBe("1:05");
  });

  it("renders hours:mm:ss above one", () => {
    expect(formatDurationClock(4122)).toBe("1:08:42");
  });

  it("has no negative readings", () => {
    expect(formatDurationClock(-30)).toBe("0:00");
  });
});

describe("date formatting", () => {
  it("renders day-first, as the mockup does", () => {
    expect(formatDayMonth("2026-07-28")).toBe("28.07");
    expect(formatDayMonthYear("2026-08-02")).toBe("02.08.2026");
  });
});

describe("formatPercent", () => {
  it("renders a fraction as whole percent", () => {
    expect(formatPercent(0.75)).toBe("75%");
    expect(formatPercent(1.18)).toBe("118%");
  });
});

describe("formatSets", () => {
  it("agrees with itself about plurals", () => {
    expect(formatSets(1)).toBe("1 set");
    expect(formatSets(16)).toBe("16 sets");
    expect(formatSets(null)).toBe("—");
  });
});
