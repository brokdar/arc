import { expect, type Page, test } from "@playwright/test";

/**
 * The WP-3 definition of done, walked end to end in the UI: write a workout,
 * plan it onto a day with an intent and success criteria, and see the card
 * appear on the calendar.
 *
 * UI-only, like the rest of this folder: there is no backend behind the
 * production build, so the API is a small stateful fake installed with
 * `page.route`. It holds the two facts the flow actually depends on — the
 * library grows when a workout is posted, the week grows when a session is —
 * so "the card appeared" means the calendar re-fetched and rendered what the
 * server would have returned, not that a component was re-rendered with props
 * a test handed it.
 */

const TODAY = new Date();
const ISO_TODAY = [
  TODAY.getFullYear(),
  String(TODAY.getMonth() + 1).padStart(2, "0"),
  String(TODAY.getDate()).padStart(2, "0"),
].join("-");

const WORKOUT_ID = "0199a000-0000-7000-8000-0000000000aa";
const SESSION_ID = "0199a000-0000-7000-8000-000000000001";

interface PlannedWorkout {
  id: string;
  name: string;
  structure: unknown;
}

/** Install the fake API. Returns the state so a test can assert against it. */
async function mockApi(page: Page) {
  const state = {
    workouts: [] as PlannedWorkout[],
    sessions: [] as { workoutId: string | null; intent: string | null }[],
    lastSessionBody: null as Record<string, unknown> | null,
  };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path.endsWith("/auth/session")) {
      return route.fulfill(json({ authenticated: true }));
    }
    if (path.endsWith("/athlete")) {
      return route.fulfill(
        json({
          name: "Alex Rider",
          date_of_birth: null,
          sex: "male",
          height_cm: null,
          capabilities: {},
          plan_state: "active",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      );
    }
    if (path.endsWith("/workout-labels")) {
      return route.fulfill(json({ folders: [], tags: [] }));
    }
    if (path.endsWith("/exercises")) {
      return route.fulfill(
        json({ items: [], total: 0, offset: 0, limit: 200 }),
      );
    }
    if (path.includes("/anchors/current")) {
      return route.fulfill(json({ detail: "none in force" }, 404));
    }
    if (path.includes("/purposes/")) {
      return route.fulfill(
        json({
          purpose: path.split("/").pop(),
          discipline: "cycling",
          description: null,
          axes: ["completion", "adherence"],
          default_criteria: [
            {
              kind: "time_in_band",
              band: {
                channel: "power",
                low: 0.92,
                high: 1.08,
                smoothing_s: 30,
              },
              min_fraction: 0.7,
              selector: { kind: "all", role: null, index: null },
            },
          ],
        }),
      );
    }

    if (path.endsWith("/workouts") && method === "POST") {
      const body = request.postDataJSON() as {
        name: string;
        structure: unknown;
      };
      state.workouts.push({
        id: WORKOUT_ID,
        name: body.name,
        structure: body.structure,
      });
      return route.fulfill(json(workoutRead(body.name, body.structure), 201));
    }
    if (path.endsWith("/workouts")) {
      return route.fulfill(
        json({
          items: state.workouts.map((workout) =>
            workoutRead(workout.name, workout.structure),
          ),
          total: state.workouts.length,
          offset: 0,
          limit: 100,
        }),
      );
    }
    if (path.includes("/workouts/")) {
      const workout = state.workouts[0];
      return route.fulfill(
        workout
          ? json(workoutRead(workout.name, workout.structure))
          : json({ detail: "not found" }, 404),
      );
    }

    if (path.endsWith("/planned-sessions") && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.lastSessionBody = body;
      state.sessions.push({
        workoutId: (body.workout_id as string) ?? null,
        intent: (body.intent_text as string) ?? null,
      });
      return route.fulfill(json({ id: SESSION_ID }, 201));
    }
    if (path.includes("/planned-sessions/")) {
      return route.fulfill(json({ detail: "not needed" }, 404));
    }

    if (path.endsWith("/plan/week")) {
      const start = url.searchParams.get("start") ?? ISO_TODAY;
      return route.fulfill(json(week(start, state.sessions)));
    }

    return route.fulfill(json({ detail: "unmocked" }, 404));
  });

  return state;
}

function workoutRead(name: string, structure: unknown) {
  return {
    id: WORKOUT_ID,
    name,
    description: null,
    discipline: "cycling",
    folder: null,
    tags: [],
    structure,
    summary: { step_count: 1, total_duration_s: 1200, total_sets: null },
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function week(
  start: string,
  sessions: { workoutId: string | null; intent: string | null }[],
) {
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(`${start}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + index);
    const iso = date.toISOString().slice(0, 10);
    return {
      date: iso,
      sessions:
        iso === ISO_TODAY
          ? sessions.map((session, ordinal) => ({
              id: `${SESSION_ID}${ordinal}`,
              date: iso,
              discipline: "cycling",
              purpose: "endurance",
              status: "planned",
              title: "Threshold 2×20",
              workout_id: session.workoutId,
              planned_duration_s: 1200,
              total_sets: null,
              step_count: 1,
              intent_text: session.intent,
              intent_version: 1,
              predicted_load: 42.5,
              predicted_intensity_factor: 0.92,
              predicted_volume_load_kg: null,
            }))
          : [],
    };
  });
  return {
    start,
    end: days[6]?.date ?? start,
    days,
    session_count: sessions.length,
    planned_duration_s: sessions.length * 1200,
    // The week rail reads these; a total is never rendered without the count
    // it was computed from, so the fake has to carry both.
    planned_load: sessions.length > 0 ? sessions.length * 42.5 : null,
    load_sessions_counted: sessions.length,
    load_sessions_uncounted: 0,
    by_discipline:
      sessions.length > 0
        ? [
            {
              discipline: "cycling",
              session_count: sessions.length,
              planned_duration_s: sessions.length * 1200,
              planned_load: sessions.length * 42.5,
              total_sets: null,
            },
          ]
        : [],
  };
}

test("write a workout, plan it, and see it on the week", async ({ page }) => {
  const state = await mockApi(page);

  // --- write the workout ---------------------------------------------------
  await page.goto("/workouts/new");
  await page.getByLabel("Name").fill("Threshold 2×20");
  await page.getByRole("button", { name: "Add steady step" }).click();
  await page.getByLabel(/Duration/).fill("20:00");
  await page.getByRole("button", { name: "+ Power" }).click();
  await page.getByLabel("Power low").fill("95");
  await page.getByLabel("Power high").fill("105");

  // The profile is drawn from the draft, before anything is saved.
  await expect(page.locator('[data-slot="workout-profile"] > div')).toHaveCount(
    1,
  );

  await page.getByRole("button", { name: "Save workout" }).click();
  await expect(page).toHaveURL(new RegExp(`/workouts/${WORKOUT_ID}$`));
  expect(state.workouts).toHaveLength(1);

  // --- plan it onto a day --------------------------------------------------
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Calendar" })).toBeVisible();
  // `exact`, because every day column also offers "Plan a session on <day>".
  await page
    .getByRole("button", { name: "Plan a session", exact: true })
    .click();

  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "Plan a session" }),
  ).toBeVisible();
  await expect(dialog.getByLabel("Date")).toHaveValue(ISO_TODAY);

  // The criteria arrive from the purpose's template, phrased in English.
  await expect(
    dialog.getByText(
      "70% of the session's time within 92%–108% of the prescribed power, 30 s average",
    ),
  ).toBeVisible();

  await dialog.getByLabel("Workout").selectOption({ label: "Threshold 2×20" });
  await dialog.getByLabel("Intent").fill("Hold threshold without fading.");
  await dialog.getByRole("button", { name: "Plan it" }).click();

  // --- see the card ---------------------------------------------------------
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("button", { name: /Threshold 2×20/ }),
  ).toBeVisible();
  await expect(page.getByText("Hold threshold without fading.")).toBeVisible();
  await expect(page.getByText("1 planned")).toBeVisible();

  expect(state.lastSessionBody).toMatchObject({
    date: ISO_TODAY,
    purpose: "endurance",
    workout_id: WORKOUT_ID,
    intent_text: "Hold threshold without fading.",
    success_criteria: [{ kind: "time_in_band" }],
  });
});
