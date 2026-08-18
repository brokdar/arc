import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WellnessView } from "@/components/wellness/wellness-view";
import { AthleteClock } from "@/lib/clock";
import { addDays, weekdayLabel } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";
import {
  ATHLETE_TIMEZONE,
  athleteToday,
  seedWellnessDay,
  wellnessDay,
} from "@/tests/mocks/fixtures";
import { WELLNESS_TREND } from "@/tests/mocks/generated-wellness-trend";

/**
 * `next/navigation`, reading jsdom's own address bar — the same mock
 * `calendar-week.test.tsx` uses, and for the same reason: the page's selected
 * day *is* `?date=`, so a test that asserted a spy's call list would be
 * asserting the component's intention rather than where the athlete ends up.
 */
vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const MOVED = "arc-test:url-moved";

  for (const method of ["pushState", "replaceState"] as const) {
    const original = window.history[method].bind(window.history);
    window.history[method] = ((...args: Parameters<History["pushState"]>) => {
      original(...args);
      window.dispatchEvent(new Event(MOVED));
    }) as History["pushState"];
  }

  const subscribe = (onMoved: () => void) => {
    window.addEventListener(MOVED, onMoved);
    window.addEventListener("popstate", onMoved);
    return () => {
      window.removeEventListener(MOVED, onMoved);
      window.removeEventListener("popstate", onMoved);
    };
  };
  const useAddressBar = () =>
    useSyncExternalStore(
      subscribe,
      () => `${window.location.pathname}${window.location.search}`,
      () => "/wellness",
    );

  return {
    usePathname: () => useAddressBar().split("?")[0] ?? "/",
    useSearchParams: () =>
      new URLSearchParams(useAddressBar().split("?")[1] ?? ""),
    useRouter: () => ({
      push: (href: string) => window.history.pushState(null, "", href),
    }),
  };
});

const today = athleteToday();

function renderWellness() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <WellnessView />
      </AthleteClock>
    </QueryClientProvider>,
  );
}

/**
 * The capture form, as a scope.
 *
 * The page carries a trajectory panel per metric beside the form, and both
 * name the metric — "Resting HR" is a text input in one and a chart's
 * accessible name in the other. A bare `findByLabelText` cannot tell them
 * apart, and it should not have to: a test about what the athlete types
 * belongs inside the form.
 */
async function form() {
  return within(await screen.findByRole("form", { name: "Record this day" }));
}

/** The row of the history table for one date, found by the day it names. */
async function rowFor(date: string) {
  const table = await screen.findByRole("table");
  const cell = await within(table).findByRole("button", {
    name: `${weekdayLabel(date)} ${formatDayMonth(date)}`,
  });
  const row = cell.closest("tr");
  if (!row) {
    throw new Error(`no row for ${date}`);
  }
  return within(row);
}

describe("WellnessView", () => {
  beforeEach(() => {
    // Every test starts at the bare `/wellness`, which means today. Without
    // this the test after the one that navigates to a past day would render
    // that day's window and see none of its own seeds.
    window.history.replaceState(null, "", "/wellness");
  });

  it("records a morning through one form and reads it back", async () => {
    const user = userEvent.setup();
    renderWellness();

    const fields = await form();
    await user.type(fields.getByLabelText(/Resting HR/), "46");
    await user.type(fields.getByLabelText(/Slept/), "7.5");
    await user.selectOptions(fields.getByLabelText(/Fatigue/), "3");
    await user.click(screen.getByRole("button", { name: "Save the day" }));

    await screen.findByRole("status");
    // Asserted against the mock's own store rather than the rendered numbers:
    // what matters is that the *write* carried the fields, and a re-render
    // reading its own optimistic state would prove nothing about the payload.
    const stored = wellnessDay(today);
    expect(stored?.resting_hr_bpm).toBe(46);
    expect(stored?.sleep_duration_s).toBe(27_000);
    expect(stored?.fatigue).toBe(3);
  });

  it("offers the scale's own anchor descriptors, not bare numerals", async () => {
    renderWellness();

    const fatigue = await screen.findByLabelText(/Fatigue/);

    // The words come from `/wellness/inputs`. A hard-coded label in the
    // component would be exactly the private copy that endpoint exists to
    // prevent, and this is the assertion that notices one.
    expect(
      await within(fatigue).findByRole("option", { name: "3 — Three" }),
    ).toBeInTheDocument();
    expect(
      within(fatigue).getByRole("option", { name: "Not recorded" }),
    ).toBeInTheDocument();
  });

  it("clears a value when its box is emptied", async () => {
    seedWellnessDay(today, { resting_hr_bpm: 46, fatigue: 3 });
    const user = userEvent.setup();
    renderWellness();

    const restingHr = await screen.findByDisplayValue("46");
    await user.clear(restingHr);
    await user.click(screen.getByRole("button", { name: "Save the day" }));

    await screen.findByRole("status");
    expect(wellnessDay(today)?.resting_hr_bpm).toBeNull();
    // The rest of the day survives: an emptied box retracts one value, not
    // the morning.
    expect(wellnessDay(today)?.fatigue).toBe(3);
  });

  it("renders a day nobody answered as an absence, never as a zero", async () => {
    seedWellnessDay(addDays(today, -1), { resting_hr_bpm: 46 });
    renderWellness();

    const row = await rowFor(addDays(today, -3));

    // The placeholder component, not the string: a dash somebody typed and a
    // declared absence are different things, and only one of them says why.
    const placeholders = row.getAllByRole("img", {
      name: /^Not assessed:/,
    });
    expect(placeholders.length).toBeGreaterThan(0);
    expect(row.queryByText("0")).not.toBeInTheDocument();
  });

  it("marks a day whose confounder voided its markers, beside the numbers", async () => {
    seedWellnessDay(today, {
      resting_hr_bpm: 43,
      confounders: ["alcohol"],
    });
    renderWellness();

    const row = await rowFor(today);

    expect(row.getByText(/not actionable: Alcohol/)).toBeInTheDocument();
    // The number is still there. What is withheld is its standing as evidence,
    // never the measurement.
    expect(row.getByText("43")).toBeInTheDocument();
  });

  it("marks a day entered long after the one it describes as recalled", async () => {
    seedWellnessDay(addDays(today, -20), { fatigue: 4 });
    renderWellness();

    const row = await rowFor(addDays(today, -20));

    expect(row.getByText("recalled")).toBeInTheDocument();
  });

  it("edits a past day in place, and says so in the URL", async () => {
    const past = addDays(today, -5);
    seedWellnessDay(past, { resting_hr_bpm: 51 });
    const user = userEvent.setup();
    renderWellness();

    const row = await rowFor(past);
    await user.click(
      row.getByRole("button", {
        name: `${weekdayLabel(past)} ${formatDayMonth(past)}`,
      }),
    );

    await waitFor(() => {
      expect(window.location.search).toBe(`?date=${past}`);
    });
    // A link someone could send: the form is now showing that day's numbers.
    expect(await screen.findByDisplayValue("51")).toBeInTheDocument();
  });

  it("asks what an HRV number is rather than stamping it rmssd", async () => {
    // The case the discriminator exists for: this athlete's watch reports
    // SDNN, so a form that defaulted to RMSSD would put every hand-typed
    // reading into a series it does not belong to — invisibly, because both
    // are plausible millisecond figures.
    const user = userEvent.setup();
    renderWellness();

    const fields = await form();
    await user.type(fields.getByLabelText(/^HRV/), "58");
    await user.selectOptions(fields.getByLabelText(/HRV statistic/), "sdnn");
    await user.selectOptions(
      screen.getByLabelText(/How it was taken/),
      "waking_spot",
    );
    await user.click(screen.getByRole("button", { name: "Save the day" }));

    await screen.findByRole("status");
    expect(wellnessDay(today)?.hrv_metric).toBe("sdnn");
    expect(wellnessDay(today)?.hrv_context).toBe("waking_spot");
  });

  it("refuses an HRV number that does not say what it is", async () => {
    const user = userEvent.setup();
    renderWellness();

    await user.type((await form()).getByLabelText(/^HRV/), "58");
    await user.click(screen.getByRole("button", { name: "Save the day" }));

    // Said beside the field rather than after a round trip.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /which statistic it is and how it was taken/,
    );
    expect(wellnessDay(today)).toBeNull();
  });

  it("carries the last reading's descriptors over to the next one", async () => {
    // Answered once, not every morning: an athlete reads the same number off
    // the same app each day.
    seedWellnessDay(addDays(today, -1), {
      hrv_ms: 60,
      hrv_metric: "sdnn",
      hrv_context: "sleeping",
    });
    const user = userEvent.setup();
    renderWellness();

    await user.type((await form()).getByLabelText(/^HRV/), "57");
    await user.click(screen.getByRole("button", { name: "Save the day" }));

    await screen.findByRole("status");
    expect(wellnessDay(today)?.hrv_metric).toBe("sdnn");
  });

  it("counts the days recorded against the days in the range", async () => {
    seedWellnessDay(today, { fatigue: 3 });
    seedWellnessDay(addDays(today, -1), { fatigue: 4 });
    renderWellness();

    // The gaps are the point: a table that only listed the answered days
    // would read as an unbroken record.
    expect(
      await screen.findByText("2 of 28 days recorded"),
    ).toBeInTheDocument();
  });
});

/**
 * The generated fixture's own sentence for a metric that cannot yet speak.
 *
 * Read out of the payload rather than typed here: the whole point of serving
 * `reason` is that the maturity rule lives in one place, and a copy in a test
 * would be the second one.
 */
function abstentionReason(metric: string): string {
  const baseline = WELLNESS_TREND.metrics[metric]?.baseline;
  if (baseline === undefined || baseline.kind !== "abstention") {
    throw new Error(`${metric} is not an abstention in the fixture`);
  }
  return baseline.reason;
}

/**
 * The trajectory half of the page (AC-59).
 *
 * The fixture behind these is `tests/mocks/generated-wellness-trend.ts`, whose
 * every baseline, band and maturity verdict was produced by running the real
 * domain and the real API projection over a synthetic sixty-day athlete. The
 * assertions below are therefore about the *page*: what it does with a
 * baseline that cannot speak yet.
 */
describe("WellnessView trajectories", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/wellness");
  });

  it("charts a metric and names its normal band", async () => {
    renderWellness();

    // Resting HR is the mature case in the fixture: sixty mornings, a band the
    // domain computed, and a line to draw.
    const panel = (await screen.findByRole("region", {
      name: /Resting HR/,
    })) as HTMLElement;
    expect(within(panel).getByText(/normal band/i)).toBeInTheDocument();
    expect(panel.querySelector("canvas")).not.toBeNull();
  });

  it("abstains on an immature baseline with the reason the API gave", async () => {
    renderWellness();

    const panel = (await screen.findByRole("region", {
      name: /HRV/,
    })) as HTMLElement;

    // The component, not a dash somebody typed — and the sentence is the
    // API's, so a UI copy of "not enough data" cannot drift from the rule.
    const abstention = within(panel).getByRole("img", {
      name: /^Not assessed:/,
    });
    expect(abstention).toHaveAccessibleName(
      `Not assessed: ${abstentionReason("hrv_rmssd_ms")}`,
    );
    // The series still charts: nine readings are worth looking at, they are
    // just not worth a baseline.
    expect(panel.querySelector("canvas")).not.toBeNull();
    expect(within(panel).queryByText(/normal band/i)).not.toBeInTheDocument();
  });

  it("renders no chart at all for a metric with no readings", async () => {
    renderWellness();

    const panel = (await screen.findByRole("region", {
      name: /Blood oxygen/,
    })) as HTMLElement;

    // Not an empty chart: a plot with nothing in it is a claim that there was
    // something to plot.
    expect(panel.querySelector("canvas")).toBeNull();
    expect(
      within(panel).getByRole("img", { name: /^Not assessed:/ }),
    ).toBeInTheDocument();
  });

  it("shows a trend and no band where a band would be a lie", async () => {
    renderWellness();

    const panel = (await screen.findByRole("region", {
      name: /Weight/,
    })) as HTMLElement;

    // Body weight moves on a scale of weeks; a daily SD deviation from it is a
    // statement nobody should make, so the API sends no band and the page
    // shows the trend instead.
    expect(within(panel).queryByText(/normal band/i)).not.toBeInTheDocument();
    expect(within(panel).getByText(/per week/i)).toBeInTheDocument();
  });

  it("counts the markers outside their band without scoring them", async () => {
    renderWellness();

    const summary = await screen.findByTestId("wellness-readiness");

    expect(summary).toHaveTextContent(
      `${WELLNESS_TREND.readiness.markers_outside_band.statement}`,
    );
    // Arc counts and names. Whether today is a day to train is not this page's
    // sentence to write.
    for (const forbidden of ["readiness score", "recommend", "you should"]) {
      expect(summary.textContent?.toLowerCase()).not.toContain(forbidden);
    }
  });
});
