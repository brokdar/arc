import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TrainingSettingsPanel } from "@/components/settings/training-settings";
import type { components } from "@/generated/api/schema";
import { LocaleProvider } from "@/lib/i18n/provider";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";
import {
  patchTrainingSettings,
  trainingSettings,
} from "@/tests/mocks/training-settings-state";

type Update = components["schemas"]["TrainingSettingsUpdate"];

function renderPanel(locale = "en") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocaleProvider locale={locale}>
        <TrainingSettingsPanel />
      </LocaleProvider>
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
    // Stored `always` behaves as `last_set`, so it shows as the one option
    // it is: after each exercise.
    expect(
      within(region).getByLabelText("Ask how hard an exercise felt"),
    ).toHaveValue("each");
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

  it("offers two honest ways to be asked, and maps them to stored values", async () => {
    const sent = recordPatches();
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();
    const ask = await within(region).findByLabelText(
      "Ask how hard an exercise felt",
    );

    // "After every set" never asked per set: the gym asks once per exercise.
    expect(
      within(ask)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["After each exercise", "Never"]);

    await user.selectOptions(ask, "never");
    await waitFor(() => expect(sent).toEqual([{ ask_frequency: "never" }]));
    await user.selectOptions(ask, "each");
    await waitFor(() =>
      expect(sent).toEqual([
        { ask_frequency: "never" },
        { ask_frequency: "last_set" },
      ]),
    );
  });

  it("lets an athlete who ticked 'under 18' untick it", async () => {
    const sent = recordPatches();
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();
    const box = await within(region).findByRole("checkbox", {
      name: /I'm under 18/,
    });
    expect(box).not.toBeChecked();

    await user.click(box);
    await waitFor(() => expect(box).toBeChecked());
    await user.click(box);

    await waitFor(() =>
      expect(sent).toEqual([
        { no_max_efforts: true },
        { no_max_efforts: false },
      ]),
    );
    await waitFor(() => expect(box).not.toBeChecked());
  });

  it("speaks German when the language is German", async () => {
    server.use(
      http.get("/api/v1/settings/training", ({ response }) =>
        response(200).json({ ...trainingSettings(), locale: "de" }),
      ),
    );
    renderPanel("de");

    const region = await screen.findByRole("region", { name: "Training" });
    await within(region).findByLabelText("Modus");
    for (const english of [
      "Mode",
      "Units",
      "Language",
      "Plain logbook",
      "High contrast",
      "Ask how hard",
      "Lifted before",
    ]) {
      expect(region).not.toHaveTextContent(english);
    }
    expect(within(region).getByLabelText("Einheiten")).toBeInTheDocument();
    expect(
      within(region).getByRole("checkbox", { name: /Ich bin unter 18/ }),
    ).toBeInTheDocument();
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
