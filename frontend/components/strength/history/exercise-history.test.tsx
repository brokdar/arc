import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExerciseHistory } from "@/components/strength/history/exercise-history";
import { SQUAT_HISTORY_EMPTY } from "@/tests/mocks/generated-strength-records";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

/**
 * The exercise history view over `GET /exercises/{id}/history`, answered
 * from the generated fixtures — bests, rep maxes and the estimate line are
 * `app.domain.strength_records` run over five stored squat sessions.
 */
function renderHistory(exerciseId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ExerciseHistory exerciseId={exerciseId} />
    </QueryClientProvider>,
  );
}

describe("ExerciseHistory", () => {
  it("shows the estimated max as a rough guide, apart from the bests", async () => {
    renderHistory("back_squat");

    expect(
      await screen.findByRole("heading", { name: "Back Squat" }),
    ).toBeInTheDocument();
    const estimate = screen.getByRole("region", { name: "Estimated max" });
    expect(estimate).toHaveTextContent("About 126.4 kg");
    expect(estimate).toHaveTextContent(
      "A rough guide from rated sets, never a personal best.",
    );
    expect(screen.getByText("5 sessions logged")).toBeInTheDocument();
  });

  it("holds a slot for every rep count, reached or not", async () => {
    renderHistory("back_squat");

    const grid = await screen.findByRole("region", {
      name: "Heaviest for reps",
    });
    const slot = (reps: number) =>
      within(grid).getByRole("group", { name: `${reps} or more reps` });
    expect(slot(1)).toHaveTextContent("110 kg × 3");
    expect(slot(3)).toHaveTextContent("110 kg × 3");
    expect(slot(5)).toHaveTextContent("102.5 kg × 5");
    expect(slot(8)).toHaveTextContent("100 kg × 8");
    // Ten reps were never reached: the slot stays, with its reason.
    expect(
      within(slot(10)).getByRole("img", {
        name: "Not assessed: No set of 10 or more reps yet",
      }),
    ).toBeInTheDocument();
  });

  it("lists every personal best, heaviest first", async () => {
    renderHistory("back_squat");

    const bests = await screen.findByRole("region", { name: "Personal bests" });
    expect(
      within(bests)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("110 kg × 3"),
      expect.stringContaining("102.5 kg × 5"),
      expect.stringContaining("100 kg × 8"),
    ]);
  });

  it("says there is not enough data, and how to get it", async () => {
    server.use(
      http.get("/api/v1/exercises/{exercise_id}/history", ({ response }) =>
        response(200).json(SQUAT_HISTORY_EMPTY),
      ),
    );
    renderHistory("back_squat");

    const estimate = await screen.findByRole("region", {
      name: "Estimated max",
    });
    expect(estimate).toHaveTextContent("Not enough data yet");
    expect(estimate).toHaveTextContent(
      "Rate the last set of this exercise in three sessions to see an estimate.",
    );
    expect(
      screen.getByText(
        "No sets of this exercise logged yet. Log it in a session and your bests appear here.",
      ),
    ).toBeInTheDocument();
  });

  it("names an exercise outside the catalogue", async () => {
    renderHistory("no_such_lift");

    expect(
      await screen.findByText("This exercise is not in the catalogue."),
    ).toBeInTheDocument();
  });
});
