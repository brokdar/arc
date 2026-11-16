import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { PreferencesPanel } from "@/components/strength/swap/preferences-panel";
import { resetPreferences } from "@/tests/mocks/handlers-preferences";

/**
 * The exercise page's preferences over `/exercises/{id}/preferences`
 * (stateful MSW, honouring each request as the service does): avoid, less
 * often, and *Ease back in*, which moves only when the athlete taps.
 */

beforeEach(() => resetPreferences());

function renderPanel(exerciseId = "back_squat") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PreferencesPanel exerciseId={exerciseId} />
    </QueryClientProvider>,
  );
}

describe("Ease back in", () => {
  it("starts at the easiest step and climbs only on the athlete's tap", async () => {
    renderPanel();

    fireEvent.click(
      await screen.findByRole("button", { name: "Ease back in" }),
    );

    expect(await screen.findByText("Step 1 of 3: Forward Lunge")).toBeVisible();
    // Nothing moves it but the tap: a fresh read is still step 1.
    renderPanel();
    await waitFor(() =>
      expect(screen.getAllByText("Step 1 of 3: Forward Lunge")).toHaveLength(2),
    );
  });

  it("the tap moves one step, and past the last the ladder ends", async () => {
    renderPanel();
    fireEvent.click(
      await screen.findByRole("button", { name: "Ease back in" }),
    );
    await screen.findByText("Step 1 of 3: Forward Lunge");

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(await screen.findByText("Step 2 of 3: Box Squat")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(
      await screen.findByText("Step 3 of 3: Bulgarian Split Squat"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));

    expect(
      await screen.findByRole("button", { name: "Ease back in" }),
    ).toBeVisible();
  });

  it("says so when nothing is easier", async () => {
    renderPanel("front_plank");

    fireEvent.click(
      await screen.findByRole("button", { name: "Ease back in" }),
    );

    expect(
      await screen.findByText(
        "Nothing in the library is easier than this one.",
      ),
    ).toBeVisible();
  });
});

describe("avoid and less often", () => {
  it("stores the athlete's choice and shows it", async () => {
    renderPanel();

    const avoid = await screen.findByRole("checkbox", {
      name: "Never suggest this one",
    });
    expect(avoid).not.toBeChecked();
    fireEvent.click(avoid);

    expect(
      await screen.findByRole("checkbox", {
        name: "Never suggest this one",
        checked: true,
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "Suggest it less often" }),
    ).not.toBeChecked();
  });
});
