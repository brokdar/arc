import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { SessionList } from "@/components/sessions/session-list";
import { ACTIVITY_IDS } from "@/tests/mocks/fixtures";
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

    // One member exists today; the badge is a lookup, so WP-6's arrival is a
    // compile error here rather than a row that renders a stale word.
    const badge = within(rowFor(ACTIVITY_IDS.gym)).getByText("Unmatched");
    expect(badge).toHaveAttribute("title", expect.stringContaining("WP-6"));
  });

  it("holds the load column open instead of collapsing it", async () => {
    renderList();
    await screen.findByText("1–3 of 3");

    // UI convention 4: the slot keeps its position and says why it is empty.
    for (const id of Object.values(ACTIVITY_IDS)) {
      expect(
        within(rowFor(id)).getByRole("img", {
          name: "Not assessed: Training load arrives with WP-5",
        }),
      ).toBeInTheDocument();
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
