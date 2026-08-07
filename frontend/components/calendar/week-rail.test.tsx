import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WeekRail } from "@/components/calendar/week-rail";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import type { PlanWeek } from "@/lib/plan-week";
import { planWeekFixture } from "@/tests/mocks/fixtures";

const WEEK = planWeekFixture(mondayOf(todayIsoDate()));

function rail() {
  return screen.getByRole("complementary", { name: "Week totals" });
}

describe("WeekRail", () => {
  it("never shows the planned load without the coverage it was computed from", () => {
    render(<WeekRail week={WEEK} />);

    // Three of the five sessions carry a predictable power target; a week of
    // five where two are unknown must not read as a light week.
    // Twice: the week's total and the cycling row, which reconcile because
    // the only predictable sessions this week are rides.
    expect(within(rail()).getAllByText("289")).toHaveLength(2);
    expect(within(rail()).getByText("3 of 5 sessions")).toBeInTheDocument();
  });

  it("says not assessed, never zero, when nothing in the week is predictable", () => {
    const blind: PlanWeek = {
      ...WEEK,
      planned_load: null,
      load_sessions_counted: 0,
      load_sessions_uncounted: 5,
    };
    render(<WeekRail week={blind} />);

    expect(within(rail()).queryByText("0")).not.toBeInTheDocument();
    expect(
      within(rail()).getByLabelText(
        "Not assessed: No session this week carries a predictable power target",
      ),
    ).toBeInTheDocument();
    expect(within(rail()).getByText("0 of 5 sessions")).toBeInTheDocument();
  });

  it("gives each discipline its own row, with TSS and sets in their own columns", () => {
    render(<WeekRail week={WEEK} />);

    expect(within(rail()).getByText("Cycling")).toBeInTheDocument();
    expect(within(rail()).getByText("Strength")).toBeInTheDocument();
    expect(within(rail()).getByText("3 sessions")).toBeInTheDocument();
    expect(within(rail()).getByText("2 sessions")).toBeInTheDocument();

    // 28 strength sets and 289 cycling TSS both render — and neither borrows
    // the other's column, because kilograms and TSS are different axes.
    expect(within(rail()).getByText("28")).toBeInTheDocument();
    expect(
      within(rail()).getByLabelText(
        "Not assessed: Strength volume is measured in kilograms, not TSS",
      ),
    ).toBeInTheDocument();
    expect(
      within(rail()).getByLabelText(
        "Not assessed: A ride is prescribed in time, not in sets",
      ),
    ).toBeInTheDocument();
  });

  it("renders nothing at all for the slots later work packages will fill", () => {
    render(<WeekRail week={WEEK} />);

    for (const heading of [
      "Completed",
      "Trend",
      "Fitness",
      "Fatigue",
      "Form",
      "Ramp",
    ]) {
      expect(within(rail()).queryByText(heading)).not.toBeInTheDocument();
    }
  });

  it("renders each optional figure the moment it is given one", () => {
    render(
      <WeekRail week={WEEK} completedDurationS={7200} fitness={64} ramp={7} />,
    );

    expect(within(rail()).getByText("Completed")).toBeInTheDocument();
    expect(within(rail()).getByText("2:00")).toBeInTheDocument();
    expect(within(rail()).getByText("Fitness")).toBeInTheDocument();
    expect(within(rail()).getByText("64")).toBeInTheDocument();
    // Still absent: an undefined sibling is not a zero.
    expect(within(rail()).queryByText("Fatigue")).not.toBeInTheDocument();
    expect(within(rail()).queryByText("Form")).not.toBeInTheDocument();
  });
});
