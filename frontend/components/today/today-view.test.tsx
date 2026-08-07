import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { TodayView } from "@/components/today/today-view";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import { planWeekFixture, SESSION_IDS } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: React.PropsWithChildren<{ href: string }>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const today = todayIsoDate();
const start = mondayOf(today);

function renderToday() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TodayView />
    </QueryClientProvider>,
  );
}

/**
 * A week whose sessions all sit on today, so the view has something to lead
 * with whichever weekday the suite happens to run on.
 */
function weekWithToday(
  sessions: ReturnType<typeof planWeekFixture>["days"][number]["sessions"],
) {
  const week = planWeekFixture(start);
  return {
    ...week,
    days: week.days.map((day) =>
      day.date === today ? { ...day, sessions } : { ...day, sessions: [] },
    ),
  };
}

const VO2_CARD = {
  id: SESSION_IDS.vo2,
  date: today,
  discipline: "cycling" as const,
  purpose: "vo2max" as const,
  status: "planned" as const,
  title: "VO₂ 5×4′",
  workout_id: null,
  planned_duration_s: 4140,
  total_sets: null,
  step_count: 11,
  intent_text: "Open the top end without digging a hole.",
  intent_version: 2,
};

const STRENGTH_CARD = {
  ...VO2_CARD,
  id: SESSION_IDS.strength,
  discipline: "strength" as const,
  purpose: "max_strength" as const,
  status: "completed" as const,
  title: "Strength — lower",
  planned_duration_s: 2520,
  total_sets: 16,
  step_count: 4,
  intent_text: "Keep the legs loaded through base.",
  intent_version: 1,
};

describe("TodayView", () => {
  it("leads with the one-sentence headline composed from the plan", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
    );

    renderToday();

    expect(
      await screen.findByRole("heading", {
        name: "1h09 VO₂max ride — 5×4′ at Z5",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open the top end without digging a hole."),
    ).toBeInTheDocument();
  });

  it("resolves the prescription's percentages against the anchor in force", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
    );

    renderToday();

    // The fixture's FTP is 250 W; the VO₂ tree spans 40%–122% of it.
    expect(await screen.findByText("100–305 W")).toBeInTheDocument();
    expect(screen.getByText("40–122% of FTP")).toBeInTheDocument();
  });

  it("stays in percentages when no anchor has been entered", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
      http.get("/api/v1/anchors/current", ({ response }) =>
        response(404).json({ detail: "No version in force" }),
      ),
    );

    renderToday();

    expect(await screen.findByText("40–122% of FTP")).toBeInTheDocument();
    expect(screen.queryByText(/ W$/)).not.toBeInTheDocument();
  });

  it("lists the success criteria as sentences", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
    );

    renderToday();

    expect(
      await screen.findByText(
        "75% of the work steps' time within 95%–105% of the prescribed power",
      ),
    ).toBeInTheDocument();
  });

  it("shows the athlete's own notes in a neutral panel, not the coach's tint", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
    );

    const { container } = renderToday();

    expect(
      await screen.findByText(/Eat before you are hungry/),
    ).toBeInTheDocument();
    // The violet intent surface is reserved for agent-written text (WP-8).
    expect(container.querySelector(".bg-coach-surface")).toBeNull();
  });

  it("renders both of today's sessions, the one still to do first", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([STRENGTH_CARD, VO2_CARD])),
      ),
    );

    renderToday();

    const headings = await screen.findAllByRole("heading", { level: 1 });
    expect(headings[0]).toHaveTextContent("1h09 VO₂max ride");
    expect(headings[1]).toHaveTextContent("16 sets of max strength");
  });

  it("names the movements of a lifting session from the catalogue", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([STRENGTH_CARD])),
      ),
    );

    renderToday();

    // `back_squat` is not readable as a slug; the catalogue supplies the name.
    expect(await screen.findByText("Back Squat")).toBeInTheDocument();
    expect(screen.getByText("Romanian Deadlift")).toBeInTheDocument();
  });

  it("treats an empty day as a rest day, with a way out of it", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([])),
      ),
    );

    renderToday();

    expect(
      await screen.findByRole("heading", { name: "Nothing planned today." }),
    ).toBeInTheDocument();
    expect(screen.getByText("Rest day")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the week" })).toHaveAttribute(
      "href",
      "/calendar",
    );
  });

  it("opens the plan form from the rest-day state", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([])),
      ),
    );

    renderToday();
    await screen.findByText("Rest day");

    const [plan] = screen.getAllByRole("button", { name: "Plan a session" });
    await userEvent.click(plan as HTMLElement);

    expect(
      await screen.findByRole("heading", { name: "Plan a session" }),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("Date")).toHaveValue(today);
  });

  it("lists the week beside it, linking to the calendar", async () => {
    renderToday();

    const aside = await screen.findByRole("complementary");
    expect(within(aside).getByText("This week")).toBeInTheDocument();
    expect(
      within(aside).getByRole("link", { name: "Calendar" }),
    ).toHaveAttribute("href", "/calendar");
    // Every day of the week is represented, empty ones as rest.
    await waitFor(() =>
      expect(within(aside).getAllByText("Rest").length).toBeGreaterThan(0),
    );
    expect(
      within(aside).getByText(
        planWeekFixture(start).days[1]?.sessions[0]?.title ?? "VO₂ 5×4′",
      ),
    ).toBeInTheDocument();
  });

  it("says so when the week cannot be loaded", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(401).json({ detail: "No valid session" }),
      ),
    );

    renderToday();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not load today/,
    );
  });

  it("opens the edit form on today's session", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(200).json(weekWithToday([VO2_CARD])),
      ),
    );

    renderToday();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit session" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Edit session" }),
    ).toBeInTheDocument();
  });
});
