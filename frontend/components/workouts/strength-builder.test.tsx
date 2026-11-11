import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkoutEditor } from "@/components/workouts/workout-editor";
import type { components } from "@/generated/api/schema";
import { http } from "@/tests/mocks/handlers";
import { server } from "@/tests/mocks/server";

type WorkoutCreate = components["schemas"]["WorkoutCreate"];

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

const ECHO_ID = "01990000-0000-7000-8000-00000000e001";

/**
 * The library's create endpoint, answering with what it was sent.
 *
 * The summary is derived from the request the way the service derives it
 * (`WorkoutSummarySchema`: one step per line, no duration for a lift, rounds
 * summed with a per-side pair counted once), so the reply is one the API
 * could produce for this body rather than a canned workout.
 */
function echoCreate(bodies: WorkoutCreate[]) {
  return http.post("/api/v1/workouts", async ({ request, response }) => {
    const body = await request.json();
    bodies.push(body);
    const structure = body.structure;
    const items =
      structure.discipline === "strength"
        ? structure.groups.flatMap((group) => group.items)
        : [];
    return response(201).json({
      id: ECHO_ID,
      name: body.name,
      description: body.description ?? null,
      discipline: structure.discipline ?? "cycling",
      folder: body.folder ?? null,
      tags: body.tags ?? [],
      structure:
        structure.discipline === "strength"
          ? { ...structure, discipline: "strength" }
          : { discipline: "strength", groups: [] },
      summary: {
        step_count: items.length,
        total_duration_s: null,
        total_sets: items.reduce((sum, item) => sum + item.sets, 0),
      },
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    });
  });
}

function renderEditor() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkoutEditor workoutId={null} />
    </QueryClientProvider>,
  );
}

async function startStrengthWorkout(name: string): Promise<HTMLElement> {
  renderEditor();
  await userEvent.type(await screen.findByLabelText("Name"), name);
  await userEvent.selectOptions(
    await screen.findByLabelText("Discipline"),
    "strength",
  );
  const row = screen.getAllByTestId("draft-set")[0] as HTMLElement;
  await userEvent.type(within(row).getByLabelText("Movement"), "Back Squat");
  await userEvent.type(within(row).getByLabelText(/Value/), "100");
  return row;
}

async function typeInto(row: HTMLElement, label: string, text: string) {
  const input = within(row).getByLabelText(label);
  await userEvent.clear(input);
  await userEvent.type(input, text);
}

beforeEach(() => {
  push.mockClear();
});

describe("the strength builder's prescription fields", () => {
  it("saves a rep range, a set kind and the main-lift tags", async () => {
    const bodies: WorkoutCreate[] = [];
    server.use(echoCreate(bodies));

    const row = await startStrengthWorkout("Backoffs");
    await typeInto(row, "Reps", "8");
    await typeInto(row, "Reps up to", "12");
    await typeInto(row, "RIR", "1");
    await typeInto(row, "RIR up to", "2");
    await userEvent.selectOptions(
      within(row).getByLabelText("Set kind"),
      "backoff",
    );
    await userEvent.selectOptions(
      within(row).getByLabelText("Main lift"),
      "yes",
    );
    await userEvent.selectOptions(
      within(row).getByLabelText("Warm-up"),
      "auto",
    );
    await userEvent.selectOptions(
      within(row).getByLabelText("Progression"),
      "double_progression",
    );

    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    const structure = bodies[0]?.structure;
    expect(structure?.discipline).toBe("strength");
    const [item] =
      structure?.discipline === "strength"
        ? (structure.groups[0]?.items ?? [])
        : [];
    expect(item).toMatchObject({
      exercise_id: "back_squat",
      reps: [8, 12],
      rir: [1, 2],
      set_kind: "backoff",
      main: true,
      warmup: "auto",
      progression: "double_progression",
    });
    // The echoed workout is the one the editor moves on to.
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(`/workouts/${ECHO_ID}`),
    );
  });

  it("sends a fixed count, and leaves the new tags out, when they are not set", async () => {
    const bodies: WorkoutCreate[] = [];
    server.use(echoCreate(bodies));

    const row = await startStrengthWorkout("Straight sets");
    await typeInto(row, "Reps", "5");
    // A range whose ends agree is a fixed count, and goes out as one.
    await typeInto(row, "Reps up to", "5");

    await userEvent.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    const structure = bodies[0]?.structure;
    const [item] =
      structure?.discipline === "strength"
        ? (structure.groups[0]?.items ?? [])
        : [];
    expect(item).toMatchObject({
      reps: 5,
      set_kind: null,
      main: null,
      warmup: null,
      progression: null,
    });
  });

  it("refuses a range written top first, inline, and will not save it", async () => {
    const bodies: WorkoutCreate[] = [];
    server.use(echoCreate(bodies));

    const row = await startStrengthWorkout("Backwards");
    await typeInto(row, "Reps", "12");
    await typeInto(row, "Reps up to", "8");

    expect(within(row).getByRole("alert")).toHaveTextContent(
      "The top of the rep range is below its bottom.",
    );
    expect(within(row).getByLabelText("Reps up to")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    const save = screen.getByRole("button", { name: "Save workout" });
    expect(save).toBeDisabled();

    await typeInto(row, "Reps up to", "15");

    expect(within(row).queryByRole("alert")).toBeNull();
    expect(save).toBeEnabled();
    expect(bodies).toHaveLength(0);
  });
});
