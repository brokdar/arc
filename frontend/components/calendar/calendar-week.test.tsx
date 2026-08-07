import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CalendarWeek } from "@/components/calendar/calendar-week";
import { SESSION_DRAG_TYPE } from "@/components/calendar/session-card";
import { addDays, mondayOf, todayIsoDate } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";
import {
  plannedSessionFixture,
  planWeekFixture,
  SESSION_IDS,
} from "@/tests/mocks/fixtures";
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

const start = mondayOf(todayIsoDate());

function renderCalendar() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CalendarWeek />
    </QueryClientProvider>,
  );
}

/** The seven columns are `<section>`s; a section with a name is a region. */
function dayColumns() {
  return screen.getAllByRole("region");
}

describe("CalendarWeek", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders seven days with the week's planned sessions", async () => {
    renderCalendar();

    expect(await screen.findByText("Strength — lower")).toBeInTheDocument();
    expect(dayColumns()).toHaveLength(7);
    expect(screen.getByText("VO₂ 5×4′")).toBeInTheDocument();
    expect(screen.getByText("Long endurance")).toBeInTheDocument();
    // Every day carries its own dd.mm, empty ones included.
    for (let offset = 0; offset < 7; offset += 1) {
      expect(
        screen.getByText(formatDayMonth(addDays(start, offset))),
      ).toBeInTheDocument();
    }
  });

  it("titles a card by its purpose when the session has none", async () => {
    renderCalendar();

    // The Wednesday session has `title: null` and purpose `recovery`; the
    // badge says Recovery too, so there are two — the heading is the card's.
    const cards = await screen.findAllByText("Recovery");
    expect(cards.length).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByRole("button", { name: /Recovery/ }),
    ).toBeInTheDocument();
  });

  it("summarises the week in the header", async () => {
    renderCalendar();

    const week = planWeekFixture(start);
    expect(
      await screen.findByText(new RegExp(`${week.session_count} planned`)),
    ).toBeInTheDocument();
  });

  it("shows the discipline's own measure: minutes for a ride, sets for lifting", async () => {
    renderCalendar();

    expect(await screen.findByText("1:09")).toBeInTheDocument();
    expect(screen.getByText("16 sets")).toBeInTheDocument();
  });

  it("moves a session to the day it is dropped on", async () => {
    const moved = vi.fn();
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/move",
        async ({ params, request, response }) => {
          moved({
            id: params.planned_session_id,
            body: await request.json(),
          });
          return response(200).json(
            plannedSessionFixture(params.planned_session_id),
          );
        },
      ),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    const friday = addDays(start, 4);
    fireEvent.drop(screen.getByTestId(`day-${friday}`), {
      dataTransfer: {
        getData: () => SESSION_IDS.strength,
      },
    });

    await waitFor(() =>
      expect(moved).toHaveBeenCalledWith({
        id: SESSION_IDS.strength,
        body: { date: friday },
      }),
    );
  });

  it("hands the dragged session's id to the drop target", async () => {
    renderCalendar();

    const setData = vi.fn();
    fireEvent.dragStart(
      await screen.findByRole("button", { name: /Strength — lower/ }),
      { dataTransfer: { setData, effectAllowed: "none" } },
    );

    expect(setData).toHaveBeenCalledWith(
      SESSION_DRAG_TYPE,
      SESSION_IDS.strength,
    );
    // A text/plain copy too: some browsers refuse to start a drag without one.
    expect(setData).toHaveBeenCalledWith("text/plain", SESSION_IDS.strength);
  });

  it("ignores a drop that carries no session", async () => {
    const moved = vi.fn();
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/move",
        ({ params, response }) => {
          moved();
          return response(200).json(
            plannedSessionFixture(params.planned_session_id),
          );
        },
      ),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    fireEvent.drop(screen.getByTestId(`day-${addDays(start, 4)}`), {
      dataTransfer: { getData: () => "" },
    });

    expect(moved).not.toHaveBeenCalled();
  });

  it("steps a week at a time and comes back to this one", async () => {
    const requested: string[] = [];
    server.use(
      http.get("/api/v1/plan/week", ({ query, response }) => {
        const requestedStart = query.get("start") ?? start;
        requested.push(requestedStart);
        return response(200).json(planWeekFixture(requestedStart));
      }),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() => expect(requested).toContain(addDays(start, 7)));

    await userEvent.click(
      screen.getByRole("button", { name: "Previous week" }),
    );
    await waitFor(() => expect(requested).toContain(start));
  });

  it("opens a sheet with the full prescription and its criteria", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );

    const sheet = await screen.findByRole("dialog");
    expect(
      within(sheet).getByRole("heading", { name: "VO₂ 5×4′" }),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "75% of the work steps' time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(
        "No more than 6:00 with heart rate above 178 bpm, raw samples",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(/Eat before you are hungry/),
    ).toBeInTheDocument();
  });

  it("says each step's target both ways: what was prescribed and what it resolves to", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    // `88–93 % FTP` is what survives an FTP change; the watts are what the
    // athlete rides. Both, side by side, on the same row.
    expect(within(sheet).getByText("50–60 % FTP")).toBeInTheDocument();
    expect(within(sheet).getByText(/125–150 W/)).toBeInTheDocument();
    // Repeats are expanded, so the list and the bars above it are the same
    // eleven things in the same order.
    expect(within(sheet).getAllByText("114–122 % FTP")).toHaveLength(5);
  });

  it("names the pinned anchor and marks an estimate as an estimate", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByText(/Resolved against/)).toBeInTheDocument();
    expect(within(sheet).getByText("FTP 250 W")).toBeInTheDocument();
    expect(within(sheet).getByText("effective 01.06.2026")).toBeInTheDocument();

    // An estimate has to read as an estimate, not as a fact: the mark carries
    // its own styling and its own note, not just a different word.
    const provenance = within(sheet).getByText("estimated");
    expect(provenance).toHaveAttribute("data-untested", "true");
    expect(provenance).toHaveAttribute(
      "title",
      expect.stringContaining("not a test"),
    );
  });

  it("shows the predicted load with the arithmetic behind it, one disclosure away", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByText("92")).toBeInTheDocument();
    expect(within(sheet).getByText("IF 0.90")).toBeInTheDocument();
    // Never a total without its coverage, on a session as on a week.
    expect(
      within(sheet).getByText(/86% of the time carried a power target/),
    ).toBeInTheDocument();

    await userEvent.click(within(sheet).getByText("How this was computed"));
    expect(within(sheet).getByText(/TSS = duration_s/)).toBeInTheDocument();
    expect(
      within(sheet).getByText("250 W (estimated, effective 2026-06-01)"),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText("target ranges reduced to their midpoint"),
    ).toBeInTheDocument();
    expect(within(sheet).getByText(/Allen & Coggan/)).toBeInTheDocument();
  });

  it("says why a session has no predicted load rather than showing a zero", async () => {
    server.use(
      http.get(
        "/api/v1/planned-sessions/{planned_session_id}",
        ({ params, response }) => {
          const session = plannedSessionFixture(params.planned_session_id);
          return response(200).json({
            ...session,
            pinned_anchors: [],
            predicted_load: null,
          });
        },
      ),
    );

    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(
      within(sheet).getByLabelText("Not assessed: No FTP anchor pinned"),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(/no FTP anchor is pinned to this session/),
    ).toBeInTheDocument();
  });

  it("states each criterion's smoothing window", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByText(/30 s average/)).toBeInTheDocument();
    expect(within(sheet).getByText(/raw samples/)).toBeInTheDocument();
  });

  it("copies a session from the sheet onto another date", async () => {
    const copied = vi.fn();
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/copy",
        async ({ params, request, response }) => {
          copied({ id: params.planned_session_id, body: await request.json() });
          return response(201).json(
            plannedSessionFixture(params.planned_session_id),
          );
        },
      ),
    );

    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy" }));

    await waitFor(() =>
      expect(copied).toHaveBeenCalledWith({
        id: SESSION_IDS.vo2,
        body: { date: addDays(start, 1) },
      }),
    );
  });

  it("deletes a session from the sheet", async () => {
    const deleted = vi.fn();
    server.use(
      http.delete(
        "/api/v1/planned-sessions/{planned_session_id}",
        ({ params, response }) => {
          deleted(params.planned_session_id);
          return response(204).empty();
        },
      ),
    );

    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() => expect(deleted).toHaveBeenCalledWith(SESSION_IDS.vo2));
  });

  it("renders a strength prescription as grouped lines, not a profile", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /Strength — lower/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByText("Back Squat")).toBeInTheDocument();
    expect(within(sheet).getByText("Superset A")).toBeInTheDocument();
    expect(
      within(sheet).getByText(/4×5 · 82% e1RM · RIR 2/),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText("90% of the prescribed sets completed"),
    ).toBeInTheDocument();
  });

  it("says nothing about the plan state while the plan is active", async () => {
    renderCalendar();
    await screen.findByText("Strength — lower");

    expect(screen.queryByText("Plan paused")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Pause plan/ }),
    ).toBeInTheDocument();
  });

  it("banners a paused plan and resumes it", async () => {
    const patched = vi.fn();
    server.use(
      http.get("/api/v1/athlete", ({ response }) =>
        response(200).json({
          name: "Alex Rider",
          date_of_birth: null,
          sex: "male",
          height_cm: null,
          capabilities: {},
          plan_state: "paused",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      ),
      http.patch("/api/v1/athlete", async ({ request, response }) => {
        patched(await request.json());
        return response(200).json({
          name: "Alex Rider",
          date_of_birth: null,
          sex: "male",
          height_cm: null,
          capabilities: {},
          plan_state: "active",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }),
    );

    renderCalendar();

    const banner = await screen.findByRole("status");
    expect(within(banner).getByText("Plan paused")).toBeInTheDocument();
    // The toolbar toggle offers the same action; resume from the banner.
    await userEvent.click(
      within(banner).getByRole("button", { name: /Resume plan/ }),
    );

    await waitFor(() =>
      expect(patched).toHaveBeenCalledWith({ plan_state: "active" }),
    );
  });

  it("plans a session on the day whose + was clicked", async () => {
    renderCalendar();
    await screen.findByText("Strength — lower");

    const thursday = addDays(start, 3);
    await userEvent.click(
      within(screen.getByTestId(`day-${thursday}`)).getByRole("button", {
        name: /^Plan a session on/,
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "Plan a session" }),
    ).toBeInTheDocument();
    // Pre-filled from the column, so planning Thursday is one click.
    expect(await screen.findByLabelText("Date")).toHaveValue(thursday);
  });

  it("edits the session itself from the sheet, not the workout behind it", async () => {
    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");
    // The library workout is one click away, but it is a different thing.
    expect(
      within(sheet).getByRole("link", { name: "Open workout" }),
    ).toHaveAttribute("href", "/workouts/0199a000-0000-7000-8000-0000000000aa");

    await userEvent.click(
      within(sheet).getByRole("button", { name: "Edit session" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Edit session" }),
    ).toBeInTheDocument();
  });

  it("reports a week it could not load instead of showing an empty one", async () => {
    server.use(
      http.get("/api/v1/plan/week", ({ response }) =>
        response(401).json({ detail: "No valid session" }),
      ),
    );

    renderCalendar();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not load this week/,
    );
  });
});
