import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { WorkoutLibrary } from "@/components/workouts/workout-library";
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

function renderLibrary() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkoutLibrary />
    </QueryClientProvider>,
  );
}

describe("WorkoutLibrary", () => {
  it("lists the library with each workout's own measure", async () => {
    renderLibrary();

    expect(await screen.findByText("VO₂ 5×4′")).toBeInTheDocument();
    expect(screen.getByText("Long endurance")).toBeInTheDocument();
    expect(screen.getByText("Strength — lower")).toBeInTheDocument();
    // Minutes for a ride, sets for a lift.
    expect(screen.getByText("0:57")).toBeInTheDocument();
    expect(screen.getByText("10 sets")).toBeInTheDocument();
  });

  it("draws a bar profile for a ride and none for a lift", async () => {
    const { container } = renderLibrary();
    await screen.findByText("VO₂ 5×4′");

    // Two cycling workouts, two profiles — the strength card has none.
    expect(
      container.querySelectorAll('[data-slot="workout-profile"]'),
    ).toHaveLength(2);
  });

  it("asks the server for the search, rather than filtering a page", async () => {
    const queries: string[] = [];
    server.use(
      http.get("/api/v1/workouts", ({ query, response }) => {
        queries.push(query.get("q") ?? "");
        return response(200).json({
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
        });
      }),
    );

    renderLibrary();
    await userEvent.type(screen.getByLabelText("Search"), "vo2");

    await waitFor(() => expect(queries).toContain("vo2"), { timeout: 2000 });
  });

  /**
   * Typing is not nine searches. Each keystroke used to be its own query key
   * and therefore its own request, and the list re-rendered under the cursor
   * with the results of a prefix nobody meant to search for.
   */
  it("waits for the typing to stop before it searches", async () => {
    const queries: string[] = [];
    server.use(
      http.get("/api/v1/workouts", ({ query, response }) => {
        queries.push(query.get("q") ?? "");
        return response(200).json({
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
        });
      }),
    );

    renderLibrary();
    await userEvent.type(screen.getByLabelText("Search"), "endurance");

    await waitFor(() => expect(queries).toContain("endurance"), {
      timeout: 2000,
    });
    // The empty first load and the settled term — not one per keystroke.
    expect(queries.filter((q) => q !== "" && q !== "endurance")).toEqual([]);
  });

  it("narrows by folder", async () => {
    renderLibrary();
    await screen.findByText("VO₂ 5×4′");

    await userEvent.selectOptions(screen.getByLabelText("Folder"), "Gym");

    await waitFor(() =>
      expect(screen.queryByText("VO₂ 5×4′")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Strength — lower")).toBeInTheDocument();
  });

  it("links each card at the editor for that workout", async () => {
    renderLibrary();

    const card = await screen.findByRole("link", { name: /VO₂ 5×4′/ });
    expect(card).toHaveAttribute(
      "href",
      "/workouts/0199a000-0000-7000-8000-0000000000aa",
    );
  });

  it("names the remedy when the library is empty", async () => {
    server.use(
      http.get("/api/v1/workouts", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 100 }),
      ),
    );

    renderLibrary();

    expect(
      await screen.findByRole("heading", { name: "The library is empty" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("link", { name: "New workout" }).length,
    ).toBeGreaterThan(0);
  });

  it("says so when the library cannot be loaded", async () => {
    server.use(
      http.get("/api/v1/workouts", ({ response }) =>
        response(401).json({ detail: "No valid session" }),
      ),
    );

    renderLibrary();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not load the library/,
    );
  });
});
