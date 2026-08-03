import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ItemsList } from "@/components/items/items-list";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("ItemsList", () => {
  it("renders items from the API", async () => {
    renderWithQuery(<ItemsList />);

    expect(await screen.findByText("First item")).toBeInTheDocument();
    expect(screen.getByText(/from msw/)).toBeInTheDocument();
  });

  it("shows an empty state when there are no items", async () => {
    server.use(
      http.get("/api/v1/items", ({ response }) =>
        response(200).json({ items: [], total: 0, offset: 0, limit: 50 }),
      ),
    );

    renderWithQuery(<ItemsList />);

    expect(await screen.findByText("No items yet.")).toBeInTheDocument();
  });

  it("shows an error state when the API fails", async () => {
    server.use(
      // 500 is (rightly) not part of the contract — use the untyped escape
      // hatch for infrastructure-failure simulation.
      http.untyped.get("http://localhost:8000/api/v1/items", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithQuery(<ItemsList />);

    expect(
      await screen.findByText("Failed to load items."),
    ).toBeInTheDocument();
  });
});
