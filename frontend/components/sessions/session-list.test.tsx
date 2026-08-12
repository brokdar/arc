import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { SessionList } from "@/components/sessions/session-list";
import {
  ACTIVITY_IDS,
  RIDE_METRICS,
  sessionRunFixture,
  toListItem,
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

function renderList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionList />
    </QueryClientProvider>,
  );
}

/** The row that links to one session. */
function rowFor(sessionId: string): HTMLElement {
  return screen
    .getAllByRole("link")
    .filter(
      (link) => link.getAttribute("href") === `/sessions/${sessionId}`,
    )[0] as HTMLElement;
}

describe("the session log", () => {
  it("reads backwards, and every row links to its session", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    const rows = screen
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    // Newest first — the opposite of the calendar, deliberately.
    expect(rows).toEqual([
      `/sessions/${ACTIVITY_IDS.gym}`,
      `/sessions/${ACTIVITY_IDS.outdoorRide}`,
      `/sessions/${ACTIVITY_IDS.trainerRide}`,
    ]);
  });

  it("states each session's own duration and where it came from", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    const ride = within(rowFor(ACTIVITY_IDS.outdoorRide));
    expect(ride.getByText("05.08.2026")).toBeInTheDocument();
    expect(ride.getByText("Ride")).toBeInTheDocument();
    // 9540 s elapsed minus a 600 s coffee stop: 8940 s is 2:29.
    expect(ride.getByText("2:29")).toBeInTheDocument();
    expect(ride.getByText("Device")).toBeInTheDocument();

    const gym = within(rowFor(ACTIVITY_IDS.gym));
    expect(gym.getByText("Strength")).toBeInTheDocument();
    expect(gym.getByText("Manual")).toBeInTheDocument();
    expect(gym.getByText("1:00")).toBeInTheDocument();
  });

  it("takes the plan badge from the row rather than assuming it", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // The badge is a lookup keyed by the generated enum, which is what made
    // WP-6's three new members a compile error rather than a row rendering a
    // raw enum value.
    const badge = within(rowFor(ACTIVITY_IDS.trainerRide)).getByText(
      "Unmatched",
    );
    expect(badge).toHaveAttribute(
      "title",
      expect.stringContaining("Not yet linked"),
    );
  });

  it("says a proposal is waiting, where the session's own status cannot", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // A pending proposal deliberately leaves the session `unmatched` (a
    // proposal is a question, and neither side moves until it is answered), so
    // a badge reading the session alone would say "Unmatched" about exactly
    // the rows that are waiting on a click.
    const badge = within(rowFor(ACTIVITY_IDS.gym)).getByText("Proposed");
    expect(badge).toHaveAttribute(
      "title",
      expect.stringContaining("waiting on you"),
    );
    expect(
      within(rowFor(ACTIVITY_IDS.outdoorRide)).getByText("Proposed"),
    ).toBeInTheDocument();
  });

  it("shows the load and the model it came from", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // The ride is the one session with a metric artefact; the number and the
    // basis come off it rather than being typed beside it, so the row and the
    // page it opens cannot disagree (A5.2 — a load from heart rate and a load
    // from power are not the same measurement).
    const row = within(rowFor(ACTIVITY_IDS.outdoorRide));
    expect(
      row.getByText(String(Math.round(RIDE_METRICS.load.training_load ?? 0))),
    ).toBeInTheDocument();
    expect(row.getByText("power")).toBeInTheDocument();
  });

  it("shows how far the ride went, and holds the column when it cannot", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // Off the same artefact the load comes from, so the row and the session
    // page cannot disagree about the distance either.
    const kilometres = RIDE_METRICS.speed?.distance_km?.value ?? 0;
    expect(kilometres).toBeGreaterThan(0);
    const row = within(rowFor(ACTIVITY_IDS.outdoorRide));
    expect(row.getByText(kilometres.toFixed(1))).toBeInTheDocument();
    // A strength session never had a speed channel; the slot stays and says so.
    expect(
      within(rowFor(ACTIVITY_IDS.gym)).getByRole("img", {
        name: /Not assessed: No distance/,
      }),
    ).toBeInTheDocument();
  });

  it("holds the load column open when there is nothing to put in it", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // UI convention 4: the slot keeps its position and says why it is empty.
    // Two of the three seeded sessions have no artefact yet, which is a real
    // state — not a loading one — and the reason names it.
    for (const id of [ACTIVITY_IDS.gym, ACTIVITY_IDS.trainerRide]) {
      const placeholder = within(rowFor(id)).getByRole("img", {
        name: /Not assessed: No training load/,
      });
      expect(placeholder).toBeInTheDocument();
    }
  });

  it("filters on the server rather than over the page it has", async () => {
    const asked: (string | null)[] = [];
    server.use(
      http.get("/api/v1/sessions", ({ query, response }) => {
        asked.push(query.get("discipline"));
        return response(200).json({
          items: [],
          total: 0,
          offset: 0,
          limit: 25,
        });
      }),
    );
    const user = userEvent.setup();
    renderList();
    await screen.findByText("none yet");

    await user.selectOptions(screen.getByLabelText("Discipline"), "strength");

    await screen.findByText("Nothing in that discipline");
    expect(asked).toEqual([null, "strength"]);
  });
});

describe("a log longer than one page", () => {
  /** 57 rides — two full pages of 25 and a short third. */
  function longLog(): (string | null)[] {
    const rows = sessionRunFixture(57).map(toListItem);
    const offsets: (string | null)[] = [];
    server.use(
      http.get("/api/v1/sessions", ({ query, response }) => {
        offsets.push(query.get("offset"));
        const offset = Number(query.get("offset") ?? 0);
        const limit = Number(query.get("limit") ?? 25);
        return response(200).json({
          items: rows.slice(offset, offset + limit),
          total: rows.length,
          offset,
          limit,
        });
      }),
    );
    return offsets;
  }

  it("walks forwards and back, one page at a time", async () => {
    const offsets = longLog();
    const user = userEvent.setup();
    renderList();

    expect(await screen.findByText("1–25 of 57")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Newer sessions" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Older sessions" }),
    ).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Older sessions" }));
    expect(await screen.findByText("26–50 of 57")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Newer sessions" }),
    ).toBeEnabled();

    // The last page is short, and the range says how short rather than
    // running to the page size.
    await user.click(screen.getByRole("button", { name: "Older sessions" }));
    expect(await screen.findByText("51–57 of 57")).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(7);
    expect(
      screen.getByRole("button", { name: "Older sessions" }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Newer sessions" }));
    expect(await screen.findByText("26–50 of 57")).toBeInTheDocument();

    // Every step asked the server for its page: a pager that paged the rows
    // it already had would show the same twenty-five three times.
    expect(offsets).toEqual(["0", "25", "50", "25"]);
  });

  it("returns to the first page when the filter changes under it", async () => {
    // Page three of "all" is not page three of "cycling", and holding the
    // offset across a filter change lands on rows that may not exist.
    const offsets = longLog();
    const user = userEvent.setup();
    renderList();
    await screen.findByText("1–25 of 57");

    await user.click(screen.getByRole("button", { name: "Older sessions" }));
    await screen.findByText("26–50 of 57");
    await user.selectOptions(screen.getByLabelText("Discipline"), "cycling");

    expect(await screen.findByText("1–25 of 57")).toBeInTheDocument();
    expect(offsets).toEqual(["0", "25", "0"]);
  });
});

describe("a log with nothing in it", () => {
  it("names the missing input and where to supply it", async () => {
    server.use(
      http.get("/api/v1/sessions", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 25 }),
      ),
    );
    renderList();

    expect(await screen.findByText("No sessions yet")).toBeInTheDocument();
    expect(
      screen.getByText(/Drop FIT, TCX or GPX files into the inbox folder/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open the inbox" }),
    ).toHaveAttribute("href", "/inbox");
  });

  it("says so when the log cannot be reached", async () => {
    server.use(
      http.get("/api/v1/sessions", ({ response }) =>
        response(401).json({ detail: "No valid session" }),
      ),
    );
    renderList();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the log",
    );
  });
});
