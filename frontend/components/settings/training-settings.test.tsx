import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TrainingSettingsPanel } from "@/components/settings/training-settings";
import type { components } from "@/generated/api/schema";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import {
  patchTrainingSettings,
  trainingSettings,
} from "@/tests/mocks/training-settings-state";

type Update = components["schemas"]["TrainingSettingsUpdate"];

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TrainingSettingsPanel />
    </QueryClientProvider>,
  );
}

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

async function panel() {
  return screen.findByRole("region", { name: "Training" });
}

describe("the Training section of Settings", () => {
  it("shows the stored settings", async () => {
    renderPanel();
    const region = await panel();

    // The suite's default athlete answered "regularly", so arc chose Standard.
    expect(await within(region).findByLabelText("Mode")).toHaveValue(
      "standard",
    );
    expect(within(region).getByLabelText("Units")).toHaveValue("kg");
    expect(within(region).getByLabelText("Language")).toHaveValue("en");
    expect(
      within(region).getByLabelText("Ask how hard a set felt"),
    ).toHaveValue("always");
    expect(
      within(region).getByRole("checkbox", { name: /Plain logbook/ }),
    ).not.toBeChecked();
    expect(
      within(region).getByRole("checkbox", { name: /High contrast/ }),
    ).not.toBeChecked();
  });

  it("sends only the setting that changed, and shows what was stored", async () => {
    const sent = recordPatches();
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();

    await user.selectOptions(
      await within(region).findByLabelText("Units"),
      "lb",
    );
    await user.selectOptions(within(region).getByLabelText("Mode"), "full");
    await user.click(
      within(region).getByRole("checkbox", { name: /Plain logbook/ }),
    );

    await waitFor(() =>
      expect(sent).toEqual([
        { units: "lb" },
        { mode: "full" },
        { plain_logbook: true },
      ]),
    );
    expect(trainingSettings()).toMatchObject({
      units: "lb",
      mode: "full",
      plain_logbook: true,
    });
    await waitFor(() =>
      expect(
        within(region).getByRole("checkbox", { name: /Plain logbook/ }),
      ).toBeChecked(),
    );
    expect(within(region).getByLabelText("Mode")).toHaveValue("full");
  });

  it("says so when a change cannot be saved", async () => {
    server.use(
      http.untyped.patch("http://localhost:8000/api/v1/settings/training", () =>
        Response.error(),
      ),
    );
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();

    await user.selectOptions(
      await within(region).findByLabelText("Units"),
      "lb",
    );

    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Could not reach the server. Try again.",
    );
    expect(trainingSettings().units).toBe("kg");
    expect(within(region).getByLabelText("Units")).toHaveValue("kg");
  });
});
