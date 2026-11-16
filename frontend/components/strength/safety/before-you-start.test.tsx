import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BeforeYouStart } from "@/components/strength/safety/before-you-start";
import type { components } from "@/generated/api/schema";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import {
  patchTrainingSettings,
  seedFreshTrainingSettings,
  trainingSettings,
} from "@/tests/mocks/training-settings-state";

type Update = components["schemas"]["TrainingSettingsUpdate"];

const TITLE = "Before you start";

/** A fresh client each time: "shows once" must survive a reload. */
function renderScreen() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BeforeYouStart />
    </QueryClientProvider>,
  );
}

/** Record every PATCH body; the stateful mock applies it as the API would. */
function recordPatches(): Update[] {
  const sent: Update[] = [];
  server.use(
    http.patch("/api/v1/settings/training", async ({ request, response }) => {
      const body = await request.json();
      sent.push(body);
      return response(200).json(patchTrainingSettings(body));
    }),
  );
  return sent;
}

describe("the before-you-start screen (AC-61)", () => {
  it("shows the essentials on first use", async () => {
    seedFreshTrainingSettings();
    renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    expect(
      within(region).getByText(/Sharp pain, or pain in a joint/),
    ).toBeVisible();
    expect(
      within(region).getByText(/Chest pain, fainting or numbness/),
    ).toBeVisible();
    expect(
      within(region).getByRole("checkbox", { name: "I'm under 18" }),
    ).not.toBeChecked();
  });

  it("dismissing it stores before_you_start_seen and it never shows again", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    const { unmount } = renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    await user.click(within(region).getByRole("button", { name: "Got it" }));

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: TITLE })).toBeNull(),
    );
    expect(sent).toEqual([{ before_you_start_seen: true }]);
    expect(trainingSettings().before_you_start_seen).toBe(true);
    expect(trainingSettings().no_max_efforts).toBe(false);

    unmount();
    renderScreen();
    // The settings load, and the screen stays away.
    await waitFor(() => expect(sent).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("region", { name: TITLE })).toBeNull();
  });

  it("marking under 18 shows the message and sets no_max_efforts", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    await user.click(
      within(region).getByRole("checkbox", { name: "I'm under 18" }),
    );

    expect(
      within(region).getByText(
        "Under 18: arc will never ask for a max effort. Lift with an adult who knows how to coach you.",
      ),
    ).toBeVisible();
    await user.click(within(region).getByRole("button", { name: "Got it" }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      before_you_start_seen: true,
      no_max_efforts: true,
    });
    expect(trainingSettings().no_max_efforts).toBe(true);
  });

  it("skipping still marks it seen", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    await user.click(within(region).getByRole("button", { name: "Skip" }));

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: TITLE })).toBeNull(),
    );
    expect(sent).toEqual([{ before_you_start_seen: true }]);
    expect(trainingSettings().before_you_start_seen).toBe(true);
  });

  it("skipping after marking under 18 keeps the flag", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    await user.click(
      within(region).getByRole("checkbox", { name: "I'm under 18" }),
    );
    await user.click(within(region).getByRole("button", { name: "Skip" }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      before_you_start_seen: true,
      no_max_efforts: true,
    });
  });

  it("a failed save keeps the screen and says why", async () => {
    seedFreshTrainingSettings();
    server.use(
      http.patch("/api/v1/settings/training", ({ response }) =>
        response(422).json({ detail: "before_you_start_seen: broken" }),
      ),
    );
    const user = userEvent.setup();
    renderScreen();

    const region = await screen.findByRole("region", { name: TITLE });
    await user.click(within(region).getByRole("button", { name: "Got it" }));

    expect(await within(region).findByRole("alert")).toBeVisible();
    expect(screen.getByRole("region", { name: TITLE })).toBeVisible();
  });
});
