import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { TodayView } from "@/components/today/today-view";
import type { components } from "@/generated/api/schema";
import { AthleteClock } from "@/lib/clock";
import { ATHLETE_TIMEZONE } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import {
  patchTrainingSettings,
  seedFreshTrainingSettings,
  trainingSettings,
} from "@/tests/mocks/training-settings-state";

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

type Update = components["schemas"]["TrainingSettingsUpdate"];

const QUESTION = "Have you lifted before?";

/** A fresh client each time: "never shows again" must survive a reload. */
function renderToday() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AthleteClock timezone={ATHLETE_TIMEZONE}>
        <TodayView />
      </AthleteClock>
    </QueryClientProvider>,
  );
}

/** Record every PATCH body, and let the stateful mock apply it as the API would. */
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

async function question() {
  return screen.findByRole("region", { name: QUESTION });
}

describe("the first-run question on Today", () => {
  it("asks a fresh install, with three answers and a skip", async () => {
    seedFreshTrainingSettings();
    renderToday();

    const region = await question();
    for (const name of ["Never", "A little", "Regularly", "Skip"]) {
      expect(within(region).getByRole("button", { name })).toBeEnabled();
    }
  });

  it("stores “Never”, keeps Simple, and never asks again", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    const { unmount } = renderToday();

    await user.click(
      within(await question()).getByRole("button", { name: "Never" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: QUESTION }),
      ).not.toBeInTheDocument(),
    );
    expect(sent).toEqual([{ lifted_before: "never" }]);
    expect(trainingSettings()).toMatchObject({
      lifted_before: "never",
      mode: "simple",
      first_run_seen: true,
    });

    // A reload, with nothing cached: the stored answer is what keeps it away.
    unmount();
    renderToday();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Today" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(trainingSettings().first_run_seen).toBe(true));
    expect(
      screen.queryByRole("region", { name: QUESTION }),
    ).not.toBeInTheDocument();
  });

  it("“Regularly” earns Standard", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    renderToday();

    await user.click(
      within(await question()).getByRole("button", { name: "Regularly" }),
    );

    await waitFor(() => expect(sent).toEqual([{ lifted_before: "regularly" }]));
    expect(trainingSettings().mode).toBe("standard");
  });

  it("Skip leaves no answer and Simple, and does not ask again", async () => {
    seedFreshTrainingSettings();
    const sent = recordPatches();
    const user = userEvent.setup();
    const { unmount } = renderToday();

    await user.click(
      within(await question()).getByRole("button", { name: "Skip" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: QUESTION }),
      ).not.toBeInTheDocument(),
    );
    expect(sent).toEqual([{ first_run_seen: true }]);
    expect(trainingSettings()).toMatchObject({
      lifted_before: null,
      mode: "simple",
    });

    unmount();
    renderToday();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Today" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: QUESTION }),
    ).not.toBeInTheDocument();
  });

  it("is not shown to an athlete who has already answered", async () => {
    // The suite's default state: answered "regularly" on an earlier day.
    expect(trainingSettings().first_run_seen).toBe(true);
    let served = 0;
    server.use(
      http.get("/api/v1/settings/training", ({ response }) => {
        served += 1;
        return response(200).json(trainingSettings());
      }),
    );
    renderToday();

    // Let the settings query land before asserting the absence.
    await waitFor(() => expect(served).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      screen.queryByRole("region", { name: QUESTION }),
    ).not.toBeInTheDocument();
  });

  it("keeps the question, with an error, when the answer cannot be saved", async () => {
    seedFreshTrainingSettings();
    server.use(
      http.untyped.patch("http://localhost:8000/api/v1/settings/training", () =>
        Response.error(),
      ),
    );
    const user = userEvent.setup();
    renderToday();

    await user.click(
      within(await question()).getByRole("button", { name: "Never" }),
    );

    const region = await question();
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Could not reach the server. Try again.",
    );
    expect(within(region).getByRole("button", { name: "Never" })).toBeEnabled();
    expect(trainingSettings().lifted_before).toBeNull();
  });
});
