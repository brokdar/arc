import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TodayCard } from "@/components/cards/card";
import { cardState, queueCard } from "@/tests/mocks/cards-state";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const CARD_ID = "0199a000-0000-7000-8000-00000000c001";

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TodayCard />
    </QueryClientProvider>,
  );
}

function queueImportCard() {
  return queueCard({
    id: CARD_ID,
    headline: "Your import needs a look",
    because: "Three sets came in without a weight.",
  });
}

describe("TodayCard", () => {
  it("renders the visible card's headline, its because, and the two answers", async () => {
    queueImportCard();

    renderCard();

    const card = await screen.findByRole("region", {
      name: "Your import needs a look",
    });
    expect(card).toHaveTextContent("Three sets came in without a weight.");
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Not now" })).toBeEnabled();
  });

  it("posts accept, and the echoed state takes the card away", async () => {
    queueImportCard();
    const sent: unknown[] = [];
    server.events.on("request:start", async ({ request }) => {
      if (request.method === "POST" && request.url.endsWith("/act")) {
        sent.push(await request.clone().json());
      }
    });
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Your import needs a look" }),
      ).not.toBeInTheDocument(),
    );
    expect(sent).toEqual([{ action: "accept" }]);
    expect(cardState().find((card) => card.id === CARD_ID)?.state).toBe(
      "accepted",
    );
    server.events.removeAllListeners();
  });

  it("posts snooze for Not now", async () => {
    queueImportCard();
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Not now" }));

    await waitFor(() =>
      expect(cardState().find((card) => card.id === CARD_ID)?.state).toBe(
        "snoozed",
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Your import needs a look" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("renders nothing at all when no card is visible", async () => {
    let asked = false;
    server.use(
      http.get("/api/v1/cards", ({ response }) => {
        asked = true;
        return response(200).json([]);
      }),
    );

    const { container } = renderCard();

    await waitFor(() => expect(asked).toBe(true));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("keeps the card and says so inline when the answer fails", async () => {
    queueImportCard();
    server.use(
      http.post("/api/v1/cards/{card_id}/act", ({ response }) =>
        response(409).json({
          detail: "This card is already expired; it cannot be answered.",
        }),
      ),
    );
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Accept" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This card is already expired; it cannot be answered.",
    );
    expect(
      screen.getByRole("region", { name: "Your import needs a look" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("keeps the card when the server cannot be reached", async () => {
    queueImportCard();
    server.use(
      http.untyped.post("http://localhost:8000/api/v1/cards/:card_id/act", () =>
        Response.error(),
      ),
    );
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Accept" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the server. Try again.",
    );
    expect(
      screen.getByRole("region", { name: "Your import needs a look" }),
    ).toBeInTheDocument();
  });
});
