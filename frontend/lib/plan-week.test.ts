import { describe, expect, it } from "vitest";

import { findSession, moveSessionInWeek } from "@/lib/plan-week";
import { planWeekFixture, SESSION_IDS } from "@/tests/mocks/fixtures";

const START = "2026-07-27";

describe("moveSessionInWeek", () => {
  it("takes the session off its old day and puts it on the new one", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.strength, "2026-07-31");

    expect(moved.days[0]?.sessions).toHaveLength(0);
    expect(moved.days[4]?.sessions.map((s) => s.id)).toContain(
      SESSION_IDS.strength,
    );
    expect(findSession(moved, SESSION_IDS.strength)?.date).toBe("2026-07-31");
  });

  it("keeps the week's totals honest while the request is in flight", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.strength, "2026-07-31");
    expect(moved.session_count).toBe(week.session_count);
    expect(moved.planned_duration_s).toBe(week.planned_duration_s);
  });

  it("drops a session moved out of the fetched window", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.vo2, "2026-08-11");

    expect(findSession(moved, SESSION_IDS.vo2)).toBeUndefined();
    expect(moved.session_count).toBe(week.session_count - 1);
  });

  it("is a no-op for a same-day drop", () => {
    const week = planWeekFixture(START);
    expect(moveSessionInWeek(week, SESSION_IDS.vo2, "2026-07-28")).toBe(week);
  });

  it("is a no-op for an id the week does not contain", () => {
    const week = planWeekFixture(START);
    expect(moveSessionInWeek(week, "not-a-session", "2026-07-31")).toBe(week);
  });
});
