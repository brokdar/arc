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

/** One of the fixture's own cards, moved onto today. */
function cardFor(sessionId: string) {
  const card = planWeekFixture(start)
    .days.flatMap((day) => day.sessions)
    .find((session) => session.id === sessionId);
  if (!card) {
    throw new Error(`no ${sessionId} in the week fixture`);
  }
  return { ...card, date: today };
}

const VO2_CARD = cardFor(SESSION_IDS.vo2);
const STRENGTH_CARD = cardFor(SESSION_IDS.strength);

/** Serve today's week from the fixture's own cards. */
function serveWeek(
  sessions: ReturnType<typeof planWeekFixture>["days"][number]["sessions"],
) {
  server.use(
    http.get("/api/v1/plan/week", ({ response }) =>
      response(200).json(weekWithToday(sessions)),
    ),
  );
}

describe("TodayView", () => {
  it("leads with the one-sentence headline composed from the plan", async () => {
    serveWeek([VO2_CARD]);

    renderToday();

    expect(
      await screen.findByRole("heading", {
        name: "57min VO₂max ride — 5×4′ at Z5",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open the top end without digging a hole."),
    ).toBeInTheDocument();
  });

  /**
   * The invariant this page exists to respect.
   *
   * The session pinned an **estimated 250 W**; the anchor in force is a
   * **tested 265 W**. Resolving against "now" would render 106–323 W and
   * quietly restate every planned watt the next time the athlete tests — so
   * the pinned numbers must be on screen, the current ones must not, and the
   * provenance shown must be the pin's own.
   */
  it("resolves the prescription against the anchors the session pinned, not the ones in force", async () => {
    const currentAnchorRequests: string[] = [];
    serveWeek([VO2_CARD]);
    server.use(
      http.get("/api/v1/anchors/current", ({ query, response }) => {
        currentAnchorRequests.push(query.get("anchor_type") ?? "");
        return response(404).json({ detail: "not consulted" });
      }),
    );

    renderToday();

    // 40 % and 122 % of the *pinned* 250 W.
    expect(await screen.findByText("100–305 W")).toBeInTheDocument();
    expect(screen.getByText("40–122% of FTP")).toBeInTheDocument();
    // The same percentages against the current 265 W. Nowhere on the page.
    expect(screen.queryByText("106–323 W")).not.toBeInTheDocument();

    // And the value is labelled with the pin's provenance, not the current
    // version's: the plan was written against a guess.
    const provenance = screen.getByText("estimated");
    expect(provenance).toHaveAttribute("data-untested", "true");
    expect(screen.getByText("FTP 250 W")).toBeInTheDocument();
    expect(screen.queryByText("FTP 265 W")).not.toBeInTheDocument();
    expect(screen.queryByText("tested")).not.toBeInTheDocument();

    // Belt and braces: the endpoint that could only ever answer "now" is not
    // consulted at all.
    expect(currentAnchorRequests).toEqual([]);
  });

  it("says each step's target both ways, prescribed and resolved", async () => {
    serveWeek([VO2_CARD]);

    renderToday();

    expect(await screen.findAllByText("114–122 % FTP")).toHaveLength(5);
    expect(screen.getAllByText(/285–305 W/).length).toBeGreaterThan(0);
  });

  it("stays in percentages when the session pinned no anchor", async () => {
    serveWeek([VO2_CARD]);
    server.use(
      http.get(
        "/api/v1/planned-sessions/{planned_session_id}",
        async ({ params, response }) => {
          const { plannedSessionFixture } = await import(
            "@/tests/mocks/fixtures"
          );
          const session = plannedSessionFixture(params.planned_session_id);
          return response(200).json({
            ...session,
            pinned_anchors: [],
            predicted_load: null,
            resolved_steps: session.resolved_steps.map((step) => ({
              ...step,
              start_targets: step.start_targets.map((target) => ({
                ...target,
                resolved_low: null,
                resolved_high: null,
                anchor_version_id: null,
              })),
              end_targets: step.end_targets.map((target) => ({
                ...target,
                resolved_low: null,
                resolved_high: null,
                anchor_version_id: null,
              })),
            })),
          });
        },
      ),
    );

    renderToday();

    expect(await screen.findByText("40–122% of FTP")).toBeInTheDocument();
    expect(screen.queryByText("100–305 W")).not.toBeInTheDocument();
  });

  it("lists the success criteria as sentences", async () => {
    serveWeek([VO2_CARD]);

    renderToday();

    expect(
      await screen.findByText(
        "75% of the work steps' time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
  });

  it("shows the athlete's own notes in a neutral panel, not the coach's tint", async () => {
    serveWeek([VO2_CARD]);

    const { container } = renderToday();

    expect(
      await screen.findByText(/Two minutes in on the first one/),
    ).toBeInTheDocument();
    // The violet intent surface is reserved for agent-written text (WP-8).
    expect(container.querySelector(".bg-coach-surface")).toBeNull();
  });

  /**
   * One document, one `h1`. Today can hold two sessions and used to render an
   * `h1` for each, leaving a screen reader with two documents on one screen.
   */
  it("renders both of today's sessions under one page-owned h1", async () => {
    serveWeek([STRENGTH_CARD, VO2_CARD]);

    renderToday();

    await screen.findByText("Open the top end without digging a hole.");
    const [pageHeading, ...others] = screen.getAllByRole("heading", {
      level: 1,
    });
    expect(pageHeading).toHaveTextContent("Today");
    expect(others).toHaveLength(0);

    const headlines = screen.getAllByRole("heading", { level: 2 });
    expect(headlines[0]).toHaveTextContent("57min VO₂max ride");
    expect(headlines[1]).toHaveTextContent("10 sets of max strength");
  });

  it("names the movements of a lifting session from the catalogue", async () => {
    serveWeek([STRENGTH_CARD]);

    renderToday();

    // `back_squat` is not readable as a slug; the catalogue supplies the name.
    expect(await screen.findByText("Back Squat")).toBeInTheDocument();
    expect(screen.getByText("Romanian Deadlift")).toBeInTheDocument();
  });

  it("treats an empty day as a rest day, with a way out of it", async () => {
    serveWeek([]);

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
    serveWeek([]);

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
    serveWeek([VO2_CARD]);

    renderToday();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit session" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Edit session" }),
    ).toBeInTheDocument();
  });
});
