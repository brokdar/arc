import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WeekGrid } from "@/components/calendar/week-grid";
import { addDays, mondayOf } from "@/lib/dates";
import { COMPLETION_TONES } from "@/lib/scoring";
import {
  athleteToday,
  planWeekFixture,
  SESSION_IDS,
} from "@/tests/mocks/fixtures";

const START = mondayOf(athleteToday());

/**
 * The grid on its own, with no network in front of it.
 *
 * The strip is a pure function of the week payload — the API computes each
 * day's state and the component only colours it — so this is the cheapest
 * layer that can catch a strip painting the wrong state, and the calendar's
 * own suite goes on covering the page around it.
 */
function renderWeek(verdicts: Parameters<typeof planWeekFixture>[1] = {}) {
  const week = planWeekFixture(START, verdicts);
  render(
    <WeekGrid
      days={week.days}
      today={START}
      onOpen={() => {}}
      onMove={() => {}}
      onPlan={() => {}}
    />,
  );
  return week;
}

/** The 3px bar at the head of one day's column. */
function strip(date: string): HTMLElement {
  return screen.getByTestId(`day-state-${date}`);
}

describe("the week strip", () => {
  it("colours each day by the state the API computed for it", () => {
    // The seeded week: a completed lift on Monday, a VO₂ ride and a recovery
    // spin still ahead, a missed core session on Thursday.
    renderWeek();

    expect(strip(START)).toHaveAttribute("data-state", "completed");
    expect(strip(START)).toHaveStyle({
      backgroundColor: COMPLETION_TONES.completed.color,
    });
    expect(strip(addDays(START, 1))).toHaveAttribute("data-state", "planned");
    expect(strip(addDays(START, 3))).toHaveAttribute("data-state", "missed");
  });

  it("names the state as well as colouring it", () => {
    renderWeek();

    // Colour is a second channel on top of a name, never the only one: a
    // fifth of men cannot tell amber from green, and a strip they cannot read
    // is a strip that says nothing to them.
    // The day's bar names the day it belongs to; the card's dot names only
    // the state, because the card is already inside the day.
    expect(strip(START).getAttribute("aria-label")).toContain(
      "Recorded, not yet judged",
    );
    const day = screen.getByTestId(`day-${START}`);
    expect(
      within(day).getByRole("img", { name: "Recorded, not yet judged" }),
    ).toBeInTheDocument();
  });

  it("takes a verdict's colour once the athlete has declared one", () => {
    renderWeek({ [SESSION_IDS.strength]: "under" });

    expect(strip(START)).toHaveAttribute("data-state", "under");
    expect(strip(START)).toHaveStyle({
      backgroundColor: COMPLETION_TONES.under.color,
    });
    // And the card says it in words, not only in a 6px dot.
    const day = screen.getByTestId(`day-${START}`);
    expect(within(day).getByText("Under")).toBeInTheDocument();
    expect(within(day).getByRole("img", { name: "Under" })).toBeInTheDocument();
  });

  it("draws an abandoned session and an as-intended one apart", () => {
    renderWeek({ [SESSION_IDS.strength]: "abandoned" });
    expect(strip(START)).toHaveStyle({
      backgroundColor: COMPLETION_TONES.abandoned.color,
    });
    expect(COMPLETION_TONES.abandoned.color).not.toBe(
      COMPLETION_TONES["completed-as_intended"].color,
    );
  });

  it("still draws a card whose state never arrived", () => {
    // The field is new (WP-7.5), and an older payload — a cached response, a
    // hand-built fake, a client running ahead of its server — simply has no
    // `completion_state` on it. The card must fall back to its status, which
    // is exactly `completion_state(status, null)` in the domain, rather than
    // throwing and taking the whole week down with it. This is the failure
    // `e2e/plan-a-week.spec.ts` hit; it belongs here, one layer down.
    const week = planWeekFixture(START);
    const days = week.days.map((day) => ({
      ...day,
      completion_state: undefined as never,
      sessions: day.sessions.map((session) => ({
        ...session,
        completion_state: undefined as never,
      })),
    }));
    render(
      <WeekGrid
        days={days}
        today={START}
        onOpen={() => {}}
        onMove={() => {}}
        onPlan={() => {}}
      />,
    );

    const day = screen.getByTestId(`day-${START}`);
    expect(within(day).getByText("Strength — lower")).toBeInTheDocument();
    expect(
      within(day).getByRole("img", { name: "Recorded, not yet judged" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId(`day-state-${START}`)).toBeNull();
  });

  it("leaves a day with nothing planned and nothing recorded uncoloured", () => {
    renderWeek();

    // Wednesday and the weekend's first day are empty in the seeded week: no
    // state, and so no bar — an uncoloured rule rather than a grey one,
    // because grey already means `planned`.
    expect(screen.queryByTestId(`day-state-${addDays(START, 4)}`)).toBeNull();
    expect(
      within(screen.getByTestId(`day-${addDays(START, 4)}`)).getByText("Rest"),
    ).toBeInTheDocument();
  });
});
