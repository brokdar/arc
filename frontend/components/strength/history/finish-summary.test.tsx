import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FinishSummary } from "@/components/strength/history/finish-summary";
import { SESSION_BESTS } from "@/tests/mocks/generated-strength-records";
import {
  BEST_SESSION_CLIENT_ID,
  NO_BEST_SESSION_CLIENT_ID,
} from "@/tests/mocks/handlers-strength-records";
import { server } from "@/tests/mocks/server";

/** Resolves once the personal-bests request has been answered. */
function answered(): Promise<number> {
  return new Promise((resolve) => {
    const listener = ({
      request,
      response,
    }: {
      request: Request;
      response: Response;
    }) => {
      if (new URL(request.url).pathname !== "/api/v1/personal-bests") return;
      server.events.removeListener("response:mocked", listener);
      resolve(response.status);
    };
    server.events.on("response:mocked", listener);
  });
}

/**
 * AC-67: the finish summary's personal bests. The response is the generated
 * fixture — `new_bests` run over a stored session and projected through the
 * endpoint's own function — so "100 kg × 8 beats 100 kg × 7" is the
 * domain's verdict, not this file's.
 */
function renderSummary(sessionClientId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FinishSummary sessionClientId={sessionClientId} />
    </QueryClientProvider>,
  );
}

describe("FinishSummary", () => {
  it("names each new best with its reps, weight and exercise", async () => {
    renderSummary(BEST_SESSION_CLIENT_ID);

    const section = await screen.findByRole("region", { name: "New bests" });
    expect(section).toHaveTextContent(
      "New best: 8 reps at 100 kg on Romanian deadlift",
    );
    // One line per best in the payload — the squats matched last time.
    expect(SESSION_BESTS.bests).toHaveLength(1);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("shows no bests section when the session set none", async () => {
    const reply = answered();
    renderSummary(NO_BEST_SESSION_CLIENT_ID);

    // The absence is asserted after the answer has landed and rendered.
    expect(await reply).toBe(200);
    await act(async () => {});
    expect(
      screen.queryByRole("region", { name: "New bests" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/New best/)).not.toBeInTheDocument();
  });

  it("shows nothing for a session the server has not received yet", async () => {
    const reply = answered();
    renderSummary("0199c000-0000-7000-8000-00000000dead");

    expect(await reply).toBe(404);
    await act(async () => {});
    expect(screen.queryByText(/New best/)).not.toBeInTheDocument();
  });
});
