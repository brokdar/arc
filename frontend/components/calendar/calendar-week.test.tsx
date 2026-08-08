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

/**
 * `next/navigation`, reading jsdom's own address bar — and noticing it move.
 *
 * The component writes its position with the native History API, so pointing
 * the mock at `window.location` means the tests assert the real observable —
 * where the browser ends up — instead of a spy's call list.
 *
 * Subscribed, not merely read: Next patches `pushState` / `replaceState` so
 * that a component which writes the URL re-renders off it, and a component
 * whose open sheet is *derived* from the URL is only testable against a mock
 * that does the same. `popstate` is in the same subscription, which is what
 * makes a Back press a real assertion here rather than a simulated one.
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
      () => "/calendar",
    );

  return {
    usePathname: () => useAddressBar().split("?")[0] ?? "/",
    useSearchParams: () =>
      new URLSearchParams(useAddressBar().split("?")[1] ?? ""),
  };
});

const start = mondayOf(todayIsoDate());

/** Where the address bar is, in the form the app writes it. */
function addressBar(): string {
  return `${window.location.pathname}${window.location.search}`;
}

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

/** Apply the moves a stateful fake has accepted, the way the server would. */
function withMoves(
  week: ReturnType<typeof planWeekFixture>,
  moves: Map<string, string>,
): ReturnType<typeof planWeekFixture> {
  const sessions = week.days
    .flatMap((day) => day.sessions)
    .map((session) => ({
      ...session,
      date: moves.get(session.id) ?? session.date,
    }));
  return {
    ...week,
    days: week.days.map((day) => ({
      ...day,
      sessions: sessions.filter((session) => session.date === day.date),
    })),
  };
}

/**
 * The same week with nothing planned in it — every total null, never 0, the
 * way `app.services.plan` builds one.
 */
function emptyWeek(weekStart: string): ReturnType<typeof planWeekFixture> {
  const week = planWeekFixture(weekStart);
  return {
    ...week,
    days: week.days.map((day) => ({ ...day, sessions: [] })),
    session_count: 0,
    planned_duration_s: null,
    duration_sessions_counted: 0,
    duration_sessions_uncounted: 0,
    planned_load: null,
    load_sessions_counted: 0,
    load_sessions_uncounted: 0,
    by_discipline: [],
  };
}

/** The seven columns are `<section>`s; a section with a name is a region. */
function dayColumns() {
  return screen.getAllByRole("region");
}

/** Every `start` the component asks the week endpoint for, in order. */
function recordWeekRequests(): string[] {
  const requested: string[] = [];
  server.use(
    http.get("/api/v1/plan/week", ({ query, response }) => {
      const requestedStart = query.get("start") ?? start;
      requested.push(requestedStart);
      return response(200).json(planWeekFixture(requestedStart));
    }),
  );
  return requested;
}

describe("CalendarWeek", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Every test starts at the bare `/calendar`, which means "this week".
    window.history.replaceState(null, "", "/calendar");
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

  it("marks the card of a session with a proposal waiting on it", async () => {
    // A pending proposal changes neither status by design (D140), so the
    // card's status dot cannot say it: the marker is the only thing on the
    // week that shows a link the athlete has not answered. `completed` and
    // `displaced` need no marker — the dot already carries both.
    server.use(
      http.get("/api/v1/plan/week", ({ query, response }) => {
        const week = planWeekFixture(query.get("start") ?? start);
        return response(200).json({
          ...week,
          days: week.days.map((day) => ({
            ...day,
            sessions: day.sessions.map((session) =>
              session.id === SESSION_IDS.vo2
                ? {
                    ...session,
                    match_status: "pending" as const,
                    matched_session_id: SESSION_IDS.copy,
                  }
                : session,
            ),
          })),
        });
      }),
    );
    renderCalendar();

    const card = await screen.findByRole("button", { name: /VO₂ 5×4′/ });
    const marker = within(card).getByText("Proposal");
    expect(marker).toHaveAttribute(
      "title",
      expect.stringContaining("waiting on you"),
    );
    // The other cards say nothing new.
    expect(screen.getAllByText("Proposal")).toHaveLength(1);
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

    // 720 + 5 × (240 + 180) + 600 for the VO₂ ride; the lift has no duration
    // at all and shows its ten sets instead.
    expect(await screen.findByText("0:57")).toBeInTheDocument();
    expect(screen.getByText("10 sets")).toBeInTheDocument();
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

  /**
   * The fake used to answer every move with the session's *original* date, so
   * "the card moved" could only ever be asserted about the request. It now
   * applies what it is told, and the assertion is about the calendar.
   */
  it("lands the card on the day it was dropped on, and leaves it there", async () => {
    const moves = new Map<string, string>();
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/move",
        async ({ params, request, response }) => {
          const body = await request.json();
          moves.set(params.planned_session_id, body.date);
          return response(200).json({
            ...plannedSessionFixture(params.planned_session_id),
            date: body.date,
          });
        },
      ),
      http.get("/api/v1/plan/week", ({ query, response }) => {
        const requested = query.get("start") ?? start;
        return response(200).json(withMoves(planWeekFixture(requested), moves));
      }),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    const friday = addDays(start, 4);
    fireEvent.drop(screen.getByTestId(`day-${friday}`), {
      dataTransfer: { getData: () => SESSION_IDS.strength },
    });

    // Optimistically first, and still there after the week is refetched.
    await waitFor(() =>
      expect(
        within(screen.getByTestId(`day-${friday}`)).getByRole("button", {
          name: /Strength — lower/,
        }),
      ).toBeInTheDocument(),
    );
    expect(
      within(screen.getByTestId(`day-${start}`)).queryByRole("button", {
        name: /Strength — lower/,
      }),
    ).not.toBeInTheDocument();
  });

  /**
   * A card dropped back where it started has not moved. Firing the mutation
   * would spend a request, an optimistic update and an invalidation of every
   * cached week on saying nothing — and would append an audit row claiming
   * the athlete rescheduled something.
   */
  it("does not move a card dropped back on its own day", async () => {
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

    fireEvent.drop(screen.getByTestId(`day-${start}`), {
      dataTransfer: { getData: () => SESSION_IDS.strength },
    });

    expect(moved).not.toHaveBeenCalled();
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

  it("shows the week the query string names, taken literally", async () => {
    // A Wednesday, deliberately: the endpoint takes `start` literally (D55),
    // so a link to a Wednesday shows the seven days from that Wednesday
    // rather than being quietly snapped back to its Monday.
    window.history.replaceState(null, "", "/calendar?week=2026-03-04");
    const requested = recordWeekRequests();

    renderCalendar();
    await screen.findByText("Strength — lower");

    await waitFor(() => expect(requested).toContain("2026-03-04"));
    expect(screen.getByText("04.03 – 10.03.2026")).toBeInTheDocument();
  });

  it("falls back to this week when the param is missing or unreadable", async () => {
    const requested = recordWeekRequests();

    // `2026-02-31` has the right shape and names no day; `next-week` is not
    // even a date. Neither may reach the API as a `start`.
    for (const search of ["", "?week=next-week", "?week=2026-02-31"]) {
      window.history.replaceState(null, "", `/calendar${search}`);
      const view = renderCalendar();
      await screen.findAllByText("Strength — lower");
      view.unmount();
    }

    expect(new Set(requested)).toEqual(new Set([start]));
  });

  it("steps a week at a time by writing the position into the URL", async () => {
    const requested = recordWeekRequests();

    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(screen.getByRole("button", { name: "Next week" }));
    expect(addressBar()).toBe(`/calendar?week=${addDays(start, 7)}`);

    await waitFor(() => expect(requested).toContain(addDays(start, 7)));

    await userEvent.click(
      screen.getByRole("button", { name: "Previous week" }),
    );
    // Back on this week the param is dropped rather than written out: a URL
    // that means "the week I am in" is still right tomorrow.
    expect(addressBar()).toBe("/calendar");
  });

  it("sends `This week` back to the bare address, not to a dated one", async () => {
    window.history.replaceState(
      null,
      "",
      `/calendar?week=${addDays(start, 21)}`,
    );
    const requested = recordWeekRequests();

    renderCalendar();
    await screen.findByText("Strength — lower");
    expect(requested).toContain(addDays(start, 21));

    await userEvent.click(screen.getByRole("button", { name: "This week" }));
    expect(addressBar()).toBe("/calendar");

    await waitFor(() => expect(requested).toContain(start));
  });

  /**
   * The open sheet is the second facet of this page's address (D88). It used
   * to be `useState<WeekSession | null>`, which could not be reloaded,
   * bookmarked or sent to anyone — the exact failure `?week=` had already
   * been fixed for.
   */
  describe("the open session in the address bar", () => {
    it("opens the session the address bar names, without a click", async () => {
      window.history.replaceState(
        null,
        "",
        `/calendar?session=${SESSION_IDS.vo2}`,
      );

      renderCalendar();

      const sheet = await screen.findByRole("dialog");
      expect(
        within(sheet).getByRole("heading", { name: "VO₂ 5×4′" }),
      ).toBeInTheDocument();
    });

    /**
     * The link has to work from anywhere, so the sheet cannot depend on the
     * card: with an empty week on screen every fact it shows — the date, the
     * intent, the workout's own name — is read off the session it fetched.
     */
    it("opens a session the week on screen does not carry", async () => {
      server.use(
        http.get("/api/v1/plan/week", ({ query, response }) =>
          response(200).json(emptyWeek(query.get("start") ?? start)),
        ),
      );
      window.history.replaceState(
        null,
        "",
        `/calendar?session=${SESSION_IDS.long}`,
      );

      renderCalendar();

      const sheet = await screen.findByRole("dialog");
      // The name is the library workout's, which only the card carries — so
      // with no card the sheet asks the library rather than heading itself
      // "Endurance" while the calendar calls the same session something else.
      expect(
        await within(sheet).findByRole("heading", { name: "Long endurance" }),
      ).toBeInTheDocument();
      expect(
        within(sheet).getByText(/Build durability before the Ötztal/),
      ).toBeInTheDocument();
    });

    it("puts the open session in the address bar, and takes only it back out", async () => {
      window.history.replaceState(null, "", "/calendar?week=2026-03-04");

      renderCalendar();
      await userEvent.click(
        await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
      );

      expect(addressBar()).toBe(
        `/calendar?week=2026-03-04&session=${SESSION_IDS.vo2}`,
      );

      await userEvent.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      // The week the athlete was reading is not collateral damage.
      expect(addressBar()).toBe("/calendar?week=2026-03-04");
    });

    /**
     * Opening pushes a history entry, which is the whole reason the gesture
     * every phone already has closes the sheet. Nothing here simulates that:
     * `history.back()` is the browser's own, and the component notices it
     * because its open-state is derived from the URL rather than kept beside
     * it.
     */
    it("closes the sheet when the browser goes back", async () => {
      renderCalendar();
      await userEvent.click(
        await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
      );
      await screen.findByRole("dialog");

      window.history.back();

      await waitFor(() => expect(addressBar()).toBe("/calendar"));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
    });

    /**
     * The two facets do not interfere: the sheet is a modal, so the week
     * controls are inert while one is open and cannot page out from under it.
     * What is left to prove is that neither param erases the other on the way
     * in — an address naming both is a link someone sent.
     */
    it("keeps the week the address names while the sheet is open", async () => {
      const requested = recordWeekRequests();
      window.history.replaceState(
        null,
        "",
        `/calendar?week=2026-03-04&session=${SESSION_IDS.vo2}`,
      );

      renderCalendar();
      await screen.findByRole("dialog");

      expect(requested).toContain("2026-03-04");
      expect(screen.getByText("04.03 – 10.03.2026")).toBeInTheDocument();
      // The week controls are behind the modal, and not reachable from it.
      expect(
        screen.queryByRole("button", { name: "Next week" }),
      ).not.toBeInTheDocument();
    });

    it("treats a session parameter that is not an id as absent", async () => {
      const asked: string[] = [];
      server.use(
        http.get(
          "/api/v1/planned-sessions/{planned_session_id}",
          ({ params, response }) => {
            asked.push(params.planned_session_id);
            return response(200).json(
              plannedSessionFixture(params.planned_session_id),
            );
          },
        ),
      );
      window.history.replaceState(
        null,
        "",
        "/calendar?week=2026-03-04&session=yesterday",
      );

      renderCalendar();
      await screen.findByText("Strength — lower");

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      // Never spent on a request: the id is a path segment, and the 404 would
      // have read as a session that had been deleted.
      expect(asked).toEqual([]);
      // And swept out of the address bar, rather than left there being ignored.
      await waitFor(() =>
        expect(addressBar()).toBe("/calendar?week=2026-03-04"),
      );
    });

    /**
     * A well-formed id that names nothing is a different thing from garbage:
     * the link was a session once, or was mistyped by a byte. Dropping the
     * param would make a dead link look like one that worked.
     */
    it("says a link names no session rather than closing over it", async () => {
      server.use(
        http.get(
          "/api/v1/planned-sessions/{planned_session_id}",
          ({ response }) =>
            response(404).json({ detail: "That session no longer exists" }),
        ),
      );
      window.history.replaceState(
        null,
        "",
        `/calendar?session=${SESSION_IDS.copy}`,
      );

      renderCalendar();

      const sheet = await screen.findByRole("dialog");
      expect(
        await within(sheet).findByText("That session no longer exists"),
      ).toBeInTheDocument();
      expect(addressBar()).toBe(`/calendar?session=${SESSION_IDS.copy}`);
    });
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
      within(sheet).getByText(/Two minutes in on the first one/),
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
    // twelve things in the same order.
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

    // Recomputed against the domain, not rounded off a guess: the 1 Hz
    // expansion through a 30 s rolling mean puts this ride at 78 TSS.
    expect(within(sheet).getByText("78")).toBeInTheDocument();
    expect(within(sheet).getByText("IF 0.91")).toBeInTheDocument();
    // Never a total without its coverage, on a session as on a week: the
    // cool-down states no power target, so a sixth of the ride is uncovered.
    expect(
      within(sheet).getByText(/82% of the time carried a power target/),
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

  it("takes two clicks to delete a session, so a mis-click cannot", async () => {
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
    // Armed, not fired: deleting a session destroys its intent history.
    expect(deleted).not.toHaveBeenCalled();
    expect(within(sheet).getByText("Delete this session?")).toBeInTheDocument();

    await userEvent.click(
      within(sheet).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() => expect(deleted).toHaveBeenCalledWith(SESSION_IDS.vo2));
  });

  /**
   * The sheet used to close the instant Delete was pressed, taking the
   * server's refusal with it — the session stayed on the calendar and nothing
   * on screen said why.
   */
  it("keeps the sheet open and says why when a delete is refused", async () => {
    server.use(
      http.delete(
        "/api/v1/planned-sessions/{planned_session_id}",
        ({ response }) =>
          response(404).json({ detail: "That session no longer exists" }),
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
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Delete" }),
    );

    expect(
      await within(sheet).findByText("That session no longer exists"),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  /**
   * A rollback on its own is indistinguishable from a drag that did not take:
   * the card slides back to where it started and the calendar says nothing.
   */
  it("rolls a refused move back and says so on the page", async () => {
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/move",
        ({ response }) => response(422).json({ detail: "The plan is paused" }),
      ),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    const monday = start;
    const friday = addDays(start, 4);
    fireEvent.drop(screen.getByTestId(`day-${friday}`), {
      dataTransfer: { getData: () => SESSION_IDS.strength },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The plan is paused",
    );
    // Rolled back: the card is on Monday again, not on Friday.
    await waitFor(() =>
      expect(
        within(screen.getByTestId(`day-${monday}`)).getByRole("button", {
          name: /Strength — lower/,
        }),
      ).toBeInTheDocument(),
    );
    expect(
      within(screen.getByTestId(`day-${friday}`)).queryByRole("button", {
        name: /Strength — lower/,
      }),
    ).not.toBeInTheDocument();

    // And the strip can be got rid of once it has been read.
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("The plan is paused")).not.toBeInTheDocument();
  });

  it("says a copy landed, and where", async () => {
    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    fireEvent.change(within(sheet).getByLabelText("Copy to"), {
      target: { value: addDays(start, 3) },
    });
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy" }));

    expect(await within(sheet).findByRole("status")).toHaveTextContent(
      /^Copied to /,
    );
  });

  it("says why a copy was refused instead of closing over it", async () => {
    server.use(
      http.post(
        "/api/v1/planned-sessions/{planned_session_id}/copy",
        ({ response }) =>
          response(422).json({ detail: "Nothing may be planned in the past" }),
      ),
    );

    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy" }));

    expect(
      await within(sheet).findByText("Nothing may be planned in the past"),
    ).toBeInTheDocument();
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

  /**
   * The other axis. A lift has no TSS and never will, so the sheet reports
   * kilograms — with the count of sets those kilograms came from, because a
   * volume totalled over three of ten sets is not the session's volume.
   */
  it("reports a lifting session's volume in kilograms, with its coverage", async () => {
    renderCalendar();

    await userEvent.click(
      await screen.findByRole("button", { name: /Strength — lower/ }),
    );
    const sheet = await screen.findByRole("dialog");

    expect(within(sheet).getByText("Predicted volume")).toBeInTheDocument();
    // 3 × 8 × 80: only the kilogram sets count.
    expect(within(sheet).getByText("1920")).toBeInTheDocument();
    expect(
      within(sheet).getByText(/30% of the sets are prescribed in kilograms/),
    ).toBeInTheDocument();
    // And never in the TSS slot.
    expect(within(sheet).queryByText("Predicted load")).not.toBeInTheDocument();
  });

  it("says why a lifting session has no volume load rather than showing a zero", async () => {
    renderCalendar();

    await userEvent.click(await screen.findByRole("button", { name: /Core/ }));
    const sheet = await screen.findByRole("dialog");

    expect(
      within(sheet).getByLabelText(
        "Not assessed: No set is prescribed in kilograms",
      ),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText(/prescribes its loads as bodyweight/),
    ).toBeInTheDocument();
    expect(within(sheet).queryByText("0")).not.toBeInTheDocument();
  });

  /**
   * The sheet's date pickers are a draft. An outside press used to throw a
   * typed date away without a word.
   */
  it("asks before an outside press discards a date typed into the sheet", async () => {
    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    const sheet = await screen.findByRole("dialog");

    fireEvent.change(within(sheet).getByLabelText("Move to"), {
      target: { value: addDays(start, 4) },
    });
    await userEvent.keyboard("{Escape}");

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("alertdialog", { name: "Discard the date you typed?" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("closes the sheet at once when nothing was typed into it", async () => {
    renderCalendar();
    await userEvent.click(
      await screen.findByRole("button", { name: /VO₂ 5×4′/ }),
    );
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  /**
   * Paging must not blank the page: the week you were looking at stays put,
   * visibly stale, until the next one arrives.
   */
  it("keeps the current week on screen while the next one loads", async () => {
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/plan/week", async ({ query, response }) => {
        const requested = query.get("start") ?? start;
        if (requested !== start) {
          await held;
        }
        return response(200).json(planWeekFixture(requested));
      }),
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(screen.getByRole("button", { name: "Next week" }));

    await waitFor(() =>
      expect(screen.getByTestId("week-body")).toHaveAttribute(
        "data-stale",
        "true",
      ),
    );
    // Still the week you were reading, not "Loading the week…".
    expect(screen.getByText("Strength — lower")).toBeInTheDocument();
    expect(screen.queryByText("Loading the week…")).not.toBeInTheDocument();

    release();
    await waitFor(() =>
      expect(screen.getByTestId("week-body")).not.toHaveAttribute("data-stale"),
    );
  });

  /**
   * Planning from the toolbar of a week you paged to must not write the
   * session into *this* week, where the athlete cannot see it.
   */
  it("pre-fills the toolbar's plan form with a day inside the week on screen", async () => {
    const other = addDays(start, 21);
    window.history.replaceState(null, "", `/calendar?week=${other}`);

    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(
      screen.getByRole("button", { name: "Plan a session" }),
    );

    expect(await screen.findByLabelText("Date")).toHaveValue(other);
  });

  it("still pre-fills today when today is the week on screen", async () => {
    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(
      screen.getByRole("button", { name: "Plan a session" }),
    );

    expect(await screen.findByLabelText("Date")).toHaveValue(todayIsoDate());
  });

  /**
   * The week is one facet of this page's address. Rebuilding the query string
   * from it alone would silently drop whatever the next facet turns out to be.
   */
  it("keeps every other query parameter when it moves the week", async () => {
    window.history.replaceState(
      null,
      "",
      "/calendar?week=2026-03-04&view=list",
    );

    renderCalendar();
    await screen.findByText("Strength — lower");

    await userEvent.click(screen.getByRole("button", { name: "Next week" }));
    expect(addressBar()).toBe("/calendar?week=2026-03-11&view=list");

    await userEvent.click(screen.getByRole("button", { name: "This week" }));
    // Only the week is dropped on the way back to the bare address.
    expect(addressBar()).toBe("/calendar?view=list");
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
