import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WeekRail } from "@/components/calendar/week-rail";
import { mondayOf } from "@/lib/dates";
import type { PlanWeek } from "@/lib/plan-week";
import { athleteToday, planWeekFixture } from "@/tests/mocks/fixtures";

const WEEK = planWeekFixture(mondayOf(athleteToday()));

function rail() {
  return screen.getByRole("complementary", { name: "Week totals" });
}

/** The row for one discipline, found by the label beside its session count. */
function disciplineRow(label: string): HTMLElement {
  const heading = within(rail()).getByText(label);
  const row = heading.closest("div")?.parentElement;
  if (!row) {
    throw new Error(`no ${label} row in the rail`);
  }
  return row;
}

describe("WeekRail", () => {
  it("never shows the planned load without the coverage it was computed from", () => {
    render(<WeekRail week={WEEK} />);

    // Two of the five sessions carry a predictable power target — the VO₂
    // ride and the long one; the recovery spin is prescribed off heart rate
    // and the two lifts are on the other axis entirely. A week of five where
    // three are unknown must not read as a light week.
    // Twice: the week's total and the cycling row, which reconcile because
    // the only predictable sessions this week are rides.
    expect(within(rail()).getAllByText("213")).toHaveLength(2);
    expect(within(rail()).getByText("2 of 5 sessions")).toBeInTheDocument();
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

  /**
   * Time is a total like any other. Two lifts prescribe no minutes, so the
   * week's 4:52 is three sessions' worth of a five-session week — and it says
   * so, exactly as the load beside it does.
   */
  it("qualifies the planned time whenever a session contributed none", () => {
    render(<WeekRail week={WEEK} />);

    // Twice: the week's own total and the cycling row, which reconcile
    // because the two lifts contribute no minutes to either.
    expect(within(rail()).getAllByText("4:52")).toHaveLength(2);
    expect(within(rail()).getByText("3 of 5 sessions")).toBeInTheDocument();
  });

  it("says not assessed, never 0:00, when no session prescribes a duration", () => {
    const untimed: PlanWeek = {
      ...WEEK,
      planned_duration_s: null,
      duration_sessions_counted: 0,
      duration_sessions_uncounted: 5,
    };
    render(<WeekRail week={untimed} />);

    expect(within(rail()).queryByText("0:00")).not.toBeInTheDocument();
    expect(
      within(rail()).getByLabelText(
        "Not assessed: No session this week prescribes a duration",
      ),
    ).toBeInTheDocument();
  });

  it("gives each discipline its own row, with TSS and sets in their own columns", () => {
    render(<WeekRail week={WEEK} />);

    expect(within(rail()).getByText("Cycling")).toBeInTheDocument();
    expect(within(rail()).getByText("Strength")).toBeInTheDocument();
    expect(within(rail()).getByText("3 sessions")).toBeInTheDocument();
    expect(within(rail()).getByText("2 sessions")).toBeInTheDocument();

    // 17 strength working sets (10 + 7) and 213 cycling TSS both render — and
    // neither borrows the other's column, because kilograms and TSS are
    // different axes.
    expect(
      within(disciplineRow("Strength")).getByText("17"),
    ).toBeInTheDocument();
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
    expect(
      within(rail()).getByLabelText(
        "Not assessed: A lift is prescribed in sets and reps, not in minutes",
      ),
    ).toBeInTheDocument();
  });

  it("qualifies a discipline's own total from its own coverage pair", () => {
    render(<WeekRail week={WEEK} />);

    // Two of the three rides predicted; the recovery spin states no power
    // target. The row says so without the week's total having to.
    expect(
      within(disciplineRow("Cycling")).getByText("2 of 3 sessions"),
    ).toBeInTheDocument();
  });

  /**
   * The discriminating case. A cycling row with no TSS has *not* run into the
   * kilograms/TSS split — every one of its rides failed to predict — and
   * saying "measured in kilograms" there would be a confident falsehood about
   * the sport rather than a fact about the week.
   */
  it("explains a missing cycling TSS by its coverage, not by the kilograms axis", () => {
    const unpredictable: PlanWeek = {
      ...WEEK,
      by_discipline: WEEK.by_discipline.map((row) =>
        row.discipline === "cycling"
          ? {
              ...row,
              planned_load: null,
              load_sessions_counted: 0,
              load_sessions_uncounted: 3,
            }
          : row,
      ),
    };
    render(<WeekRail week={unpredictable} />);

    expect(
      within(disciplineRow("Cycling")).getByLabelText(
        "Not assessed: No prediction for 3 of 3 sessions",
      ),
    ).toBeInTheDocument();
    expect(
      within(disciplineRow("Cycling")).queryByLabelText(
        "Not assessed: Strength volume is measured in kilograms, not TSS",
      ),
    ).not.toBeInTheDocument();
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

  /**
   * A discipline row can exist for its **completed** side alone.
   *
   * WP-5 gave the week what was recorded, so a ride nobody planned produces a
   * row with no planned sessions in it. Copy built for the planned side
   * rendered "0 sessions" beside a ride that happened, and a coverage reason
   * of "No prediction for 0 of 0 sessions" — a sentence about nothing.
   */
  it("says nothing was planned rather than 0 sessions", () => {
    const week: PlanWeek = {
      ...WEEK,
      by_discipline: [
        {
          discipline: "cycling",
          session_count: 0,
          planned_duration_s: null,
          duration_sessions_counted: 0,
          duration_sessions_uncounted: 0,
          planned_load: null,
          load_sessions_counted: 0,
          load_sessions_uncounted: 0,
          total_sets: null,
          completed_session_count: 1,
          completed_duration_s: 4_200,
          completed_load: 71,
          completed_load_sessions_counted: 1,
          completed_load_sessions_uncounted: 0,
        },
      ],
    };

    render(<WeekRail week={week} />);

    const row = disciplineRow("Cycling");
    expect(within(row).getByText("nothing planned")).toBeInTheDocument();
    expect(within(row).queryByText("0 sessions")).not.toBeInTheDocument();
    // The row explains its own existence: something was recorded.
    expect(within(row).getByText("· 1 recorded")).toBeInTheDocument();
    // And no sentence about nothing: both planned cells say the same true
    // thing rather than "No prediction for 0 of 0 sessions".
    expect(
      within(row).getAllByRole("img", {
        name: "Not assessed: Nothing was planned for this discipline this week",
      }),
    ).toHaveLength(2);
    expect(within(row).queryByText(/0 of 0/)).not.toBeInTheDocument();
  });

  it("still counts a planned discipline the way it always did", () => {
    render(<WeekRail week={WEEK} />);

    expect(within(rail()).getByText("3 sessions")).toBeInTheDocument();
    expect(within(rail()).queryByText(/recorded/)).not.toBeInTheDocument();
  });
});
