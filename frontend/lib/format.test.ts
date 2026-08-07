import { describe, expect, it } from "vitest";

import {
  formatDayMonth,
  formatDayMonthYear,
  formatDurationClock,
  formatDurationHm,
  formatDurationWords,
  formatMinutesPrime,
  formatPercent,
  formatSets,
  parseDurationInput,
  parseNumberInput,
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

describe("formatDurationWords", () => {
  it("says a duration the way a sentence does", () => {
    expect(formatDurationWords(11400)).toBe("3h10");
    expect(formatDurationWords(3600)).toBe("1h");
    expect(formatDurationWords(2700)).toBe("45min");
    expect(formatDurationWords(40)).toBe("40s");
  });

  it("carries a rounded minute into the hour rather than printing 3h60", () => {
    expect(formatDurationWords(3599 + 3600)).toBe("2h");
  });
});

describe("formatMinutesPrime", () => {
  it("writes an interval with primes", () => {
    expect(formatMinutesPrime(240)).toBe("4\u2032");
    expect(formatMinutesPrime(90)).toBe("1\u203230\u2033");
    expect(formatMinutesPrime(40)).toBe("40\u2033");
  });
});

describe("parseDurationInput", () => {
  it("is the inverse of the clock reading", () => {
    expect(parseDurationInput("4:00")).toBe(240);
    expect(parseDurationInput("1:08:42")).toBe(4122);
  });

  it("reads a bare number as minutes, which is what it means in that field", () => {
    expect(parseDurationInput("40")).toBe(2400);
    expect(parseDurationInput("2.5")).toBe(150);
  });

  it("returns null rather than guessing", () => {
    expect(parseDurationInput("")).toBeNull();
    expect(parseDurationInput("soon")).toBeNull();
    expect(parseDurationInput("1:2:3:4")).toBeNull();
    expect(parseDurationInput("4:")).toBeNull();
  });
});

describe("parseNumberInput", () => {
  it("keeps an empty field distinguishable from a zero", () => {
    expect(parseNumberInput("")).toBeNull();
    expect(parseNumberInput("  ")).toBeNull();
    expect(parseNumberInput("0")).toBe(0);
    expect(parseNumberInput("82.5")).toBe(82.5);
    expect(parseNumberInput("eighty")).toBeNull();
  });
});
