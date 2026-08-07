import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkoutEditor } from "@/components/workouts/workout-editor";
import { WORKOUT_IDS } from "@/tests/mocks/fixtures";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

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

function renderEditor(workoutId: string | null = null) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkoutEditor workoutId={workoutId} />
    </QueryClientProvider>,
  );
}

/** The step cards, in document order — depth-first, so nesting is visible. */
function stepCards() {
  return screen.getAllByTestId("draft-step");
}

beforeEach(() => {
  push.mockClear();
});

describe("the endurance builder", () => {
  it("starts empty and says what to do about it", async () => {
    renderEditor();

    expect(await screen.findByText(/No steps yet/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("draft-step")).toHaveLength(0);
  });

  it("adds, nests and removes steps", async () => {
    renderEditor();

    await userEvent.click(
      await screen.findByRole("button", { name: "Add steady step" }),
    );
    expect(stepCards()).toHaveLength(1);

    // A repeat block arrives with a work step and a rest step inside it.
    await userEvent.click(screen.getByRole("button", { name: "Add repeat" }));
    expect(stepCards()).toHaveLength(4);

    // The block's own "add … inside" button nests into it, not beside it.
    await userEvent.click(
      screen.getByRole("button", { name: "Add steady step inside" }),
    );
    const repeat = stepCards().find((card) =>
      within(card).queryByText(/^Repeat · /),
    );
    expect(repeat).toBeDefined();
    expect(
      within(repeat as HTMLElement).getByText("Repeat · 3 steps"),
    ).toBeInTheDocument();

    // Removing the block takes its children with it.
    await userEvent.click(
      within(repeat as HTMLElement).getAllByRole("button", {
        name: "Remove step",
      })[0] as HTMLElement,
    );
    expect(stepCards()).toHaveLength(1);
  });

  it("redraws the profile as the draft changes", async () => {
    const { container } = renderEditor();

    await userEvent.click(
      await screen.findByRole("button", { name: "Add steady step" }),
    );
    // One step, one bar — drawn at a placeholder width until it has a
    // duration, which the caption says out loud.
    expect(
      container.querySelectorAll('[data-slot="workout-profile"] > div'),
    ).toHaveLength(1);
    expect(screen.getByText(/placeholders/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/Duration/), "10:00");

    await waitFor(() => expect(screen.getByText("10:00")).toBeInTheDocument());
    expect(screen.queryByText(/placeholders/)).not.toBeInTheDocument();

    // A 4× repeat of two steps adds eight more bars, expanded.
    await userEvent.click(screen.getByRole("button", { name: "Add repeat" }));
    await waitFor(() =>
      expect(
        container.querySelectorAll('[data-slot="workout-profile"] > div'),
      ).toHaveLength(9),
    );
  });

  it("sends a percentage target as fractions, and an absolute one in watts", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/workouts", async ({ request, response }) => {
        bodies.push(await request.json());
        return response(201).json({
          id: WORKOUT_IDS.vo2,
          name: "Threshold",
          description: null,
          discipline: "cycling",
          folder: null,
          tags: [],
          structure: { discipline: "cycling", steps: [] },
          summary: { step_count: 0, total_duration_s: null, total_sets: null },
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        });
      }),
    );

    renderEditor();

    await userEvent.type(await screen.findByLabelText("Name"), "Threshold");
    await userEvent.click(
      screen.getByRole("button", { name: "Add steady step" }),
    );
    await userEvent.type(screen.getByLabelText(/Duration/), "20:00");

    await userEvent.click(screen.getByRole("button", { name: "+ Power" }));
    await userEvent.type(screen.getByLabelText("Power low"), "95");
    await userEvent.type(screen.getByLabelText("Power high"), "105");

    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      name: "Threshold",
      structure: {
        discipline: "cycling",
        steps: [
          {
            kind: "steady",
            duration_s: 1200,
            targets: {
              power: {
                kind: "percent_of_anchor",
                anchor_type: "ftp",
                pct_low: 0.95,
                pct_high: 1.05,
              },
            },
          },
        ],
      },
    });

    // Flip the same target to absolute: a different document, not a format.
    await userEvent.selectOptions(
      screen.getByLabelText("Power target kind"),
      "absolute",
    );
    await userEvent.clear(screen.getByLabelText("Power low"));
    await userEvent.type(screen.getByLabelText("Power low"), "240");
    await userEvent.clear(screen.getByLabelText("Power high"));
    await userEvent.type(screen.getByLabelText("Power high"), "260");
    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toMatchObject({
      structure: {
        steps: [
          {
            targets: {
              power: { kind: "absolute", unit: "W", low: 240, high: 260 },
            },
          },
        ],
      },
    });
  });

  it("refuses to save a workout the domain would reject, and says why", async () => {
    const posted = vi.fn();
    server.use(
      http.post("/api/v1/workouts", ({ response }) => {
        posted();
        return response(422).json({ detail: "no" });
      }),
    );

    renderEditor();

    await userEvent.click(
      await screen.findByRole("button", { name: "Save workout" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Give the workout a name.");
    expect(alert).toHaveTextContent("A workout needs at least one step.");
    expect(posted).not.toHaveBeenCalled();
  });

  it("shows the server's 422 next to the fields", async () => {
    server.use(
      http.post("/api/v1/workouts", ({ response }) =>
        response(422).json({
          detail: "a step needs exactly one of duration_s or distance_m",
        }),
      ),
    );

    renderEditor();
    await userEvent.type(await screen.findByLabelText("Name"), "Broken");
    await userEvent.click(
      screen.getByRole("button", { name: "Add steady step" }),
    );
    await userEvent.type(screen.getByLabelText(/Duration/), "5:00");
    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "a step needs exactly one of duration_s or distance_m",
    );
  });
});

describe("the strength builder", () => {
  async function switchToStrength() {
    await userEvent.selectOptions(
      await screen.findByLabelText("Discipline"),
      "strength",
    );
  }

  it("groups paired movements into a superset", async () => {
    renderEditor();
    await switchToStrength();

    expect(screen.getAllByTestId("draft-group")).toHaveLength(1);
    expect(screen.getAllByTestId("draft-set")).toHaveLength(1);
    expect(screen.queryByText("Superset")).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "+ Pair a movement (superset)" }),
    );

    // Two lines in one group *is* a superset — no flag to keep in step.
    expect(screen.getAllByTestId("draft-set")).toHaveLength(2);
    expect(screen.getByText("Superset")).toBeInTheDocument();
    expect(screen.getByTestId("draft-group")).toHaveAttribute(
      "data-superset",
      "true",
    );
  });

  it("sends sets, reps, load, RIR and rest, grouped as they were entered", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/workouts", async ({ request, response }) => {
        bodies.push(await request.json());
        return response(201).json({
          id: WORKOUT_IDS.lower,
          name: "Lower",
          description: null,
          discipline: "strength",
          folder: null,
          tags: [],
          structure: { discipline: "strength", groups: [] },
          summary: { step_count: 0, total_duration_s: null, total_sets: null },
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        });
      }),
    );

    renderEditor();
    await userEvent.type(await screen.findByLabelText("Name"), "Lower");
    await switchToStrength();

    const first = screen.getAllByTestId("draft-set")[0] as HTMLElement;
    await userEvent.type(
      within(first).getByLabelText("Movement"),
      "Back Squat",
    );
    await userEvent.clear(within(first).getByLabelText("Sets"));
    await userEvent.type(within(first).getByLabelText("Sets"), "4");
    await userEvent.clear(within(first).getByLabelText("Reps"));
    await userEvent.type(within(first).getByLabelText("Reps"), "5");
    await userEvent.selectOptions(
      within(first).getByLabelText("Load"),
      "percent_e1rm",
    );
    await userEvent.type(within(first).getByLabelText(/Value/), "82");
    await userEvent.type(within(first).getByLabelText("RIR"), "2");
    await userEvent.type(within(first).getByLabelText(/Rest/), "180");

    await userEvent.click(
      screen.getByRole("button", { name: "+ Pair a movement (superset)" }),
    );
    const second = screen.getAllByTestId("draft-set")[1] as HTMLElement;
    await userEvent.type(
      within(second).getByLabelText("Movement"),
      "Hanging Leg Raise",
    );
    await userEvent.selectOptions(
      within(second).getByLabelText("Load"),
      "bodyweight",
    );

    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      structure: {
        discipline: "strength",
        groups: [
          {
            items: [
              {
                exercise_id: "back_squat",
                sets: 4,
                reps: 5,
                load: { kind: "percent_e1rm", value: 0.82 },
                rir: 2,
                rest_s: 180,
              },
              {
                exercise_id: "hanging_leg_raise",
                load: { kind: "bodyweight", value: null },
              },
            ],
          },
        ],
      },
    });
  });
});

describe("leaving an unsaved draft", () => {
  it("goes straight back to the library when nothing has been typed", async () => {
    renderEditor();
    await screen.findByLabelText("Name");

    // Whether the link's own navigation survives is the assertion; jsdom
    // cannot follow it, so a document-level listener reads the verdict after
    // the component's handler has had its say and then stops the navigation.
    const prevented: boolean[] = [];
    document.addEventListener(
      "click",
      (event) => {
        prevented.push(event.defaultPrevented);
        event.preventDefault();
      },
      { once: true },
    );
    await userEvent.click(screen.getByRole("link", { name: /Library/ }));

    expect(prevented).toEqual([false]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  /**
   * The browser's own back button, a typed URL and a closed tab are the three
   * exits the application cannot render a prompt into, so it asks the browser
   * to. `preventDefault` during the event is the whole protocol.
   */
  it("asks the browser to warn before the tab is closed on a draft", async () => {
    renderEditor();
    await userEvent.type(await screen.findByLabelText("Name"), "Threshold");

    const unload = createEvent(
      "beforeunload",
      window,
      { bubbles: false, cancelable: true },
      { EventType: "Event" },
    );
    fireEvent(window, unload);

    expect(unload.defaultPrevented).toBe(true);
  });

  it("asks before the Library link throws a draft away", async () => {
    renderEditor();
    await userEvent.type(await screen.findByLabelText("Name"), "Threshold");

    await userEvent.click(screen.getByRole("link", { name: /Library/ }));

    expect(
      screen.getByRole("alertdialog", {
        name: "Discard this draft and go back to the library?",
      }),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
    // Still here, still typed in.
    expect(screen.getByLabelText("Name")).toHaveValue("Threshold");

    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("navigates once the athlete has said discard", async () => {
    renderEditor();
    await userEvent.type(await screen.findByLabelText("Name"), "Threshold");

    await userEvent.click(screen.getByRole("link", { name: /Library/ }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(push).toHaveBeenCalledWith("/workouts");
  });
});

describe("editing a saved workout", () => {
  it("takes two clicks to delete, so a mis-click cannot", async () => {
    const deleted = vi.fn();
    server.use(
      http.delete("/api/v1/workouts/{workout_id}", ({ params, response }) => {
        deleted(params.workout_id);
        return response(204).empty();
      }),
    );

    renderEditor(WORKOUT_IDS.long);
    await screen.findByDisplayValue("Long endurance");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(deleted).not.toHaveBeenCalled();
    expect(screen.getByText("Delete this workout?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toHaveBeenCalledWith(WORKOUT_IDS.long));
  });

  it("loads the prescription into the builder and PATCHes it back", async () => {
    const patched: unknown[] = [];
    server.use(
      http.patch(
        "/api/v1/workouts/{workout_id}",
        async ({ request, response }) => {
          patched.push(await request.json());
          return response(200).json({
            id: WORKOUT_IDS.long,
            name: "Long endurance",
            description: null,
            discipline: "cycling",
            folder: null,
            tags: [],
            structure: { discipline: "cycling", steps: [] },
            summary: {
              step_count: 0,
              total_duration_s: null,
              total_sets: null,
            },
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
          });
        },
      ),
    );

    renderEditor(WORKOUT_IDS.long);

    expect(
      await screen.findByDisplayValue("Long endurance"),
    ).toBeInTheDocument();
    expect(stepCards()).toHaveLength(3);
    // The discipline is fixed once saved: switching it would discard the tree.
    expect(screen.getByLabelText("Discipline")).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0]).toMatchObject({
      name: "Long endurance",
      structure: { discipline: "cycling" },
    });
  });
});
