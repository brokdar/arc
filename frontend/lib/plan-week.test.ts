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

  it("changes no total when the card stays inside the week", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.strength, "2026-07-31");

    expect(moved.session_count).toBe(week.session_count);
    expect(moved.planned_duration_s).toBe(week.planned_duration_s);
    expect(moved.planned_load).toBe(week.planned_load);
    expect(moved.by_discipline).toEqual(week.by_discipline);
  });

  it("drops a session moved out of the fetched window", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.vo2, "2026-08-11");

    expect(findSession(moved, SESSION_IDS.vo2)).toBeUndefined();
    expect(moved.session_count).toBe(week.session_count - 1);
  });

  /**
   * The one that used to lie. Dragging a ride out of the week took its minutes
   * with it and left its TSS, its coverage pairs and the whole `by_discipline`
   * block behind — so the rail showed a load no session in the grid
   * contributed to until the refetch landed.
   */
  it("reconciles every total when a card leaves the week", () => {
    const week = planWeekFixture(START);
    const moved = moveSessionInWeek(week, SESSION_IDS.vo2, "2026-08-11");
    const gone = findSession(week, SESSION_IDS.vo2);

    expect(gone?.planned_duration_s).toBe(3420);
    expect(moved.planned_duration_s).toBe(
      (week.planned_duration_s ?? 0) - 3420,
    );
    expect(moved.duration_sessions_counted).toBe(
      week.duration_sessions_counted - 1,
    );
    expect(moved.duration_sessions_uncounted).toBe(
      week.duration_sessions_uncounted,
    );

    expect(moved.planned_load).toBeCloseTo(
      (week.planned_load ?? 0) - (gone?.predicted_load ?? 0),
      6,
    );
    expect(moved.load_sessions_counted).toBe(week.load_sessions_counted - 1);
    expect(moved.load_sessions_uncounted).toBe(week.load_sessions_uncounted);

    // The discipline row follows the same fold over the same remaining cards.
    const cycling = moved.by_discipline.find((r) => r.discipline === "cycling");
    expect(cycling?.session_count).toBe(2);
    expect(cycling?.load_sessions_counted).toBe(1);
    expect(cycling?.load_sessions_uncounted).toBe(1);
    expect(cycling?.planned_load).toBeCloseTo(
      findSession(week, SESSION_IDS.long)?.predicted_load ?? 0,
      6,
    );
    // Untouched: the lifts are on the other side of the fold.
    expect(
      moved.by_discipline.find((r) => r.discipline === "strength"),
    ).toEqual(week.by_discipline.find((r) => r.discipline === "strength"));
  });

  it("keeps a total null rather than zeroing it when the last contributor leaves", () => {
    const week = planWeekFixture(START);
    const withoutVo2 = moveSessionInWeek(week, SESSION_IDS.vo2, "2026-08-11");
    const blind = moveSessionInWeek(withoutVo2, SESSION_IDS.long, "2026-08-11");

    // Null, never 0: a week with nothing predictable is unknown, not easy.
    expect(blind.planned_load).toBeNull();
    expect(blind.load_sessions_counted).toBe(0);
    expect(
      blind.by_discipline.find((r) => r.discipline === "cycling")?.planned_load,
    ).toBeNull();
  });

  it("drops a discipline row entirely once its last session leaves", () => {
    const week = planWeekFixture(START);
    const gone = [SESSION_IDS.strength, SESSION_IDS.missed].reduce(
      (current, id) => moveSessionInWeek(current, id, "2026-08-11"),
      week,
    );

    expect(gone.by_discipline.map((row) => row.discipline)).toEqual([
      "cycling",
    ]);
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
