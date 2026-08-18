import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import {
  ClockProvider,
  useAthleteTimezone,
  useAthleteToday,
} from "@/lib/clock";
import { todayIsoDate } from "@/lib/dates";
import { ATHLETE_TIMEZONE, athleteToday } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

/**
 * The one clock, and the fact that it is not the browser's.
 *
 * The runner is pinned to `Pacific/Midway` (UTC-11) and the fake backend
 * serves `Pacific/Kiritimati` (UTC+14) — twenty-five hours apart with no DST
 * on either side, so the two are never on the same calendar day. Every
 * assertion below therefore distinguishes the athlete's answer from the
 * browser's on every run, which is exactly what the tests this replaced could
 * not do: they keyed off a browser-derived "today" and so did the components,
 * and the two agreed about being wrong (issue #62).
 */
function Probe() {
  return (
    <>
      <span data-testid="zone">{useAthleteTimezone()}</span>
      <span data-testid="today">{useAthleteToday()}</span>
    </>
  );
}

function renderProbe() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ClockProvider>
        <Probe />
      </ClockProvider>
    </QueryClientProvider>,
  );
}

describe("the athlete's clock", () => {
  it("serves the configured zone and the day on it", async () => {
    renderProbe();

    expect(await screen.findByTestId("zone")).toHaveTextContent(
      ATHLETE_TIMEZONE,
    );
    expect(screen.getByTestId("today")).toHaveTextContent(athleteToday());
  });

  it("is not the browser's day", async () => {
    renderProbe();

    const today = (await screen.findByTestId("today")).textContent;
    expect(today).not.toBe(todayIsoDate("Pacific/Midway"));
  });

  it("holds the page rather than guessing while the zone is unknown", () => {
    renderProbe();

    // Nothing below can name a day without it, and the guess would be the
    // browser's — the bug. So the gate says "loading", and every consumer
    // below it can treat the zone as a plain string.
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByTestId("today")).not.toBeInTheDocument();
  });

  it("says so rather than falling back when the clock cannot be read", async () => {
    server.use(
      // Untyped: an infrastructure failure has no schema to conform to.
      http.untyped.get("http://localhost:8000/api/v1/clock", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderProbe();

    expect(
      await screen.findByText(/Could not read the athlete's timezone/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("today")).not.toBeInTheDocument();
  });
});
