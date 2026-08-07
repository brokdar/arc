import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { SessionForm } from "@/components/plan/session-form";
import {
  purposeTemplateFixture,
  SESSION_IDS,
  WORKOUT_IDS,
} from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const DATE = "2026-08-06";

function renderForm(props: Partial<Parameters<typeof SessionForm>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <SessionForm date={DATE} onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { ...result, onClose };
}

/** The criteria cards, as the editor renders them. */
function criteria() {
  return screen.getAllByTestId("criterion");
}

describe("planning a session", () => {
  it("opens on the day that was clicked", async () => {
    renderForm();

    expect(await screen.findByLabelText("Date")).toHaveValue(DATE);
  });

  it("pre-fills the criteria from the purpose's template", async () => {
    renderForm();

    await waitFor(() => expect(criteria()).toHaveLength(2));
    expect(
      screen.getByText(
        "70% of the session's time within 92%–108% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Lasts at least 1:30:00")).toBeInTheDocument();
    expect(
      screen.getByText(/From this purpose's template/),
    ).toBeInTheDocument();
  });

  it("re-derives the criteria when the purpose changes", async () => {
    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.selectOptions(screen.getByLabelText("Purpose"), "vo2max");

    await waitFor(() => expect(criteria()).toHaveLength(1));
    expect(
      screen.getByText(
        "85% of the work steps' time within 95%–105% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
  });

  it("stops following the purpose once the athlete has edited the criteria", async () => {
    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.click(
      within(criteria()[1] as HTMLElement).getByRole("button", {
        name: "Remove criterion",
      }),
    );
    expect(criteria()).toHaveLength(1);

    await userEvent.selectOptions(screen.getByLabelText("Purpose"), "vo2max");

    // Still the athlete's list, not the VO₂max template's.
    expect(criteria()).toHaveLength(1);
    expect(
      screen.getByText(
        "70% of the session's time within 92%–108% of the prescribed power, 30 s average",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Reset to the purpose's template/ }),
    ).toBeInTheDocument();
  });

  it("swaps the criteria vocabulary when the purpose crosses disciplines", async () => {
    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.selectOptions(
      screen.getByLabelText("Purpose"),
      "max_strength",
    );

    await waitFor(() =>
      expect(
        screen.getByText("90% of the prescribed sets completed"),
      ).toBeInTheDocument(),
    );
    // A ride's criteria are not on offer for a lifting session.
    expect(
      within(screen.getByLabelText("Add a criterion")).queryByText(
        "Time in band",
      ),
    ).toBeNull();
  });

  /**
   * The race this closes: `POST /planned-sessions` fired before
   * `GET /purposes/{purpose}` answered posted `success_criteria: []`, and a
   * session frozen with no criteria is indistinguishable afterwards from one
   * the athlete deliberately left unjudged.
   */
  it("refuses to save while the purpose's criteria template is still loading", async () => {
    const posted = vi.fn();
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/purposes/{purpose}", async ({ params, response }) => {
        await held;
        return response(200).json(purposeTemplateFixture(params.purpose));
      }),
      http.post("/api/v1/planned-sessions", ({ response }) => {
        posted();
        return response(201).json(plannedSession());
      }),
    );

    renderForm();

    const submit = await screen.findByRole("button", { name: "Plan it" });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/Loading this purpose's criteria template/),
    ).toBeInTheDocument();

    await userEvent.click(submit);
    expect(posted).not.toHaveBeenCalled();

    release();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Plan it" })).toBeEnabled();
  });

  it("lets the athlete proceed deliberately when the template cannot be loaded", async () => {
    server.use(
      // Untyped: the endpoint declares no failure the athlete can cause, so
      // the case being simulated is the network, not a refusal.
      http.untyped.get("http://localhost:8000/api/v1/purposes/:purpose", () =>
        HttpResponse.error(),
      ),
    );

    renderForm();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not load this purpose's template/,
    );
    // Not blocked: an empty criteria list is now a choice made in front of a
    // message saying it is one.
    expect(screen.getByRole("button", { name: "Plan it" })).toBeEnabled();
  });

  /**
   * The bug: the "add a criterion" menu re-derives its options from the
   * discipline, but the *selected* kind was remembered. A touched list plus a
   * purpose change across disciplines left `time_in_band` selected behind a
   * strength menu, and Add posted a criterion the domain refuses.
   */
  it("adds a criterion the new discipline allows after the purpose crosses over", async () => {
    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    // Touch the list, so it stops following the purpose and the editor stays
    // mounted across the change.
    await userEvent.click(
      within(criteria()[1] as HTMLElement).getByRole("button", {
        name: "Remove criterion",
      }),
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Purpose"),
      "max_strength",
    );

    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    // The athlete's own criterion is still theirs; the *added* one is the
    // strength menu's first kind, not the cycling one left over in state.
    expect(criteria()).toHaveLength(2);
    expect(
      within(criteria()[1] as HTMLElement).getByText(
        "90% of the prescribed sets completed",
      ),
    ).toBeInTheDocument();
    expect(
      within(criteria()[1] as HTMLElement).queryByText(
        /of the prescribed power/,
      ),
    ).toBeNull();
  });

  it("posts the chosen library workout with the intent and the criteria", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/planned-sessions", async ({ request, response }) => {
        bodies.push(await request.json());
        return response(201).json(plannedSession());
      }),
    );

    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.selectOptions(
      screen.getByLabelText("Workout"),
      WORKOUT_IDS.long,
    );
    await userEvent.type(
      screen.getByLabelText("Intent"),
      "Build durability before the Ötztal.",
    );
    await userEvent.type(
      screen.getByLabelText(/Notes to self/),
      "Eat before you are hungry.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Plan it" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      date: DATE,
      purpose: "endurance",
      workout_id: WORKOUT_IDS.long,
      intent_text: "Build durability before the Ötztal.",
      coach_notes: "Eat before you are hungry.",
      success_criteria: [
        { kind: "time_in_band" },
        { kind: "duration_floor", min_seconds: 5400 },
      ],
    });
    // No `structure` beside the workout: a prescription has one source.
    expect(bodies[0]).not.toHaveProperty("structure");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("posts an inline prescription instead when one is described here", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/planned-sessions", async ({ request, response }) => {
        bodies.push(await request.json());
        return response(201).json(plannedSession());
      }),
    );

    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.click(
      screen.getByRole("button", { name: "Describe it here" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Add steady step" }),
    );
    await userEvent.type(screen.getByLabelText(/Duration/), "45:00");
    await userEvent.click(screen.getByRole("button", { name: "Plan it" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      structure: {
        discipline: "cycling",
        steps: [{ kind: "steady", duration_s: 2700 }],
      },
    });
    expect(bodies[0]).not.toHaveProperty("workout_id");
  });

  it("insists on a prescription before it posts anything", async () => {
    const posted = vi.fn();
    server.use(
      http.post("/api/v1/planned-sessions", ({ response }) => {
        posted();
        return response(201).json(plannedSession());
      }),
    );

    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.click(screen.getByRole("button", { name: "Plan it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Choose a workout, or describe one here.",
    );
    expect(posted).not.toHaveBeenCalled();
  });

  it("shows the API's refusal rather than failing silently", async () => {
    server.use(
      http.post("/api/v1/planned-sessions", ({ response }) =>
        response(422).json({
          detail: "No ftp version is in force; the prescription needs one",
        }),
      ),
    );

    renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.selectOptions(
      screen.getByLabelText("Workout"),
      WORKOUT_IDS.long,
    );
    await userEvent.click(screen.getByRole("button", { name: "Plan it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No ftp version is in force",
    );
  });
});

/** The backdrop Base UI closes the dialog on when it is pressed. */
function backdrop(): HTMLElement {
  const found = document.querySelector('[data-slot="dialog-overlay"]');
  if (!found) {
    throw new Error("the dialog rendered no backdrop");
  }
  return found as HTMLElement;
}

describe("discarding a draft", () => {
  it("closes immediately when nothing has been typed", async () => {
    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));

    await userEvent.click(backdrop());

    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByText("Discard this draft?")).not.toBeInTheDocument();
  });

  it("keeps the dialog and asks when an outside press would discard work", async () => {
    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.type(screen.getByLabelText("Intent"), "Hold threshold.");

    await userEvent.click(backdrop());

    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("alertdialog", { name: "Discard this draft?" }),
    ).toBeInTheDocument();
    // Still there, still typed in.
    expect(screen.getByLabelText("Intent")).toHaveValue("Hold threshold.");
  });

  it("keeps the dialog on escape too, and lets the athlete carry on", async () => {
    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.type(screen.getByLabelText("Intent"), "Hold threshold.");

    await userEvent.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Intent")).toHaveValue("Hold threshold.");
  });

  it("closes on Discard, once the athlete has said so", async () => {
    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.type(screen.getByLabelText("Intent"), "Hold threshold.");

    await userEvent.click(backdrop());
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(onClose).toHaveBeenCalled();
  });

  it("guards the form's own Cancel button by the same rule", async () => {
    const { onClose } = renderForm();
    await waitFor(() => expect(criteria()).toHaveLength(2));
    await userEvent.type(screen.getByLabelText("Intent"), "Hold threshold.");

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("alertdialog", { name: "Discard this draft?" }),
    ).toBeInTheDocument();
  });
});

describe("editing a planned session", () => {
  it("loads the session and PATCHes only what changed", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.patch(
        "/api/v1/planned-sessions/{planned_session_id}",
        async ({ request, response }) => {
          bodies.push(await request.json());
          return response(200).json(plannedSession());
        },
      ),
    );

    renderForm({ sessionId: SESSION_IDS.vo2 });

    expect(
      await screen.findByRole("heading", { name: "Edit session" }),
    ).toBeInTheDocument();
    // The saved intent, not the template — a saved session's criteria are its own.
    await waitFor(() => expect(criteria()).toHaveLength(3));
    expect(screen.getByLabelText("Purpose")).toHaveValue("vo2max");
    expect(screen.getByLabelText(/Notes to self/)).toHaveValue(
      "Two minutes in on the first one is where it is decided.",
    );

    await userEvent.type(screen.getByLabelText("Intent"), " Sharpen up.");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({
      intent_text: "Open the top end without digging a hole. Sharpen up.",
    });
  });

  it("closes without a request when nothing was changed", async () => {
    const patched = vi.fn();
    server.use(
      http.patch(
        "/api/v1/planned-sessions/{planned_session_id}",
        ({ response }) => {
          patched();
          return response(200).json(plannedSession());
        },
      ),
    );

    const { onClose } = renderForm({ sessionId: SESSION_IDS.vo2 });
    await waitFor(() => expect(criteria()).toHaveLength(3));

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(patched).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

function plannedSession() {
  // The shape the endpoints answer with; the form only reads it back on GET.
  return {
    id: SESSION_IDS.vo2,
    date: DATE,
    discipline: "cycling" as const,
    status: "planned" as const,
    intent_versions: 1,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    intent: {
      id: "intent",
      artefact_id: "artefact",
      version: 1,
      as_of: "2026-08-01T00:00:00Z",
      superseded_by: null,
      recompute_reason: null,
      edited_post_hoc: false,
      purpose: "endurance" as const,
      intent_text: null,
      coach_notes: null,
      workout_id: WORKOUT_IDS.long,
      pinned_anchor_versions: {},
      structure: { discipline: "cycling" as const, steps: [] },
      success_criteria: [],
      summary: { step_count: 0, total_duration_s: null, total_sets: null },
    },
    // Nothing prescribed, so nothing to resolve; the form only reads the
    // intent back.
    pinned_anchors: [],
    resolved_steps: [],
    predicted_load: null,
    predicted_volume: null,
  };
}
