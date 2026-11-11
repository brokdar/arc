import { type BrowserContext, expect, type Page, test } from "@playwright/test";

/**
 * The logging screen in a real browser (AC-49, AC-52): the tap budget at
 * phone sizes, and a whole session logged with the network off.
 *
 * UI-only, against the production build, the API faked on the context as in
 * `offline-sync.spec.ts`. The fake server is the server's behaviour in
 * miniature where these tests lean on it: `GET /strength/packet` serves the
 * captured packet of `tests/mocks/handlers-packet.ts` (one-arm row 3×10 at
 * 20 kg, then a 45 s plank) under Standard settings; `POST /sync` applies
 * each mutation once, keyed by its id, and keeps every set under its
 * session's client id and its own; `GET /sessions/{id}` reads that store
 * back. `/plan/week` is unreachable throughout, so Today lists the session
 * from the packet the first visit stored — the path a gym with no signal
 * takes.
 *
 * Taps are counted twice: each step is one `click()` here, and a capturing
 * `pointerdown` listener in the page (kept in `sessionStorage`, so it
 * survives the page load Start causes) counts what the browser dispatched.
 */

const ATHLETE_ZONE = "Pacific/Kiritimati";
const PLANNED_ID = "0199c000-0000-7000-8000-00000000a001";

function athleteToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ATHLETE_ZONE }).format(
    new Date(),
  );
}

function addDays(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** The captured packet (`tests/mocks/handlers-packet.ts`), Standard mode. */
function packet(today: string) {
  const prescription = {
    duration_s: null,
    per_side: null,
    rir: null,
    rest_s: null,
    tempo: null,
    notes: null,
    set_kind: null,
    main: null,
    warmup: null,
    progression: null,
  };
  const exercise = {
    restrictions: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    starts_on: today,
    ends_before: addDays(today, 7),
    settings: { mode: "standard", plain_logbook: false, units: "kg" },
    effort: { scale: "reps_left", ask: "always" },
    gym: {
      bar_kg: 20,
      plate_pairs: [
        { kg: 25, pairs: 4 },
        { kg: 20, pairs: 2 },
        { kg: 10, pairs: 2 },
        { kg: 5, pairs: 2 },
        { kg: 2.5, pairs: 2 },
        { kg: 1.25, pairs: 2 },
      ],
      dumbbell_step_kg: 2,
      dumbbell_max_kg: 50,
      stack_step_kg: 5,
      stack_max_kg: 150,
    },
    sessions: [
      {
        planned_session_id: PLANNED_ID,
        date: today,
        time_of_day: null,
        purpose: "max_strength",
        intent_text: null,
        key: false,
        plan_id: "0199c000-0000-7000-8000-00000000b001",
        follow_exactly: false,
        lines: [
          {
            line_ref: "0.0",
            group_index: 0,
            item_index: 0,
            group_label: null,
            exercise_id: "single_arm_dumbbell_row",
            exercise_name: "Single-Arm Dumbbell Row",
            prescription: {
              ...prescription,
              exercise_id: "single_arm_dumbbell_row",
              sets: 3,
              reps: 10,
              per_side: true,
              load: { kind: "kg", value: 20 },
              warmup: "none",
            },
            main: true,
            equipment: "dumbbell",
            prefill: {
              source: "prescription",
              load_kg: 20,
              reps: 10,
              duration_s: null,
              starting_flow: false,
            },
            warmups: [],
            plates_per_side: null,
          },
          {
            line_ref: "1.0",
            group_index: 1,
            item_index: 0,
            group_label: null,
            exercise_id: "front_plank",
            exercise_name: "Front Plank",
            prescription: {
              ...prescription,
              exercise_id: "front_plank",
              sets: 3,
              reps: null,
              duration_s: 45,
              load: { kind: "bodyweight", value: null },
            },
            main: false,
            equipment: null,
            prefill: {
              source: "prescription",
              load_kg: null,
              reps: null,
              duration_s: 45,
              starting_flow: false,
            },
            warmups: [],
            plates_per_side: null,
          },
        ],
      },
    ],
    last_numbers: [],
    library: [
      {
        ...exercise,
        id: "single_arm_dumbbell_row",
        name: "Single-Arm Dumbbell Row",
        category: "pull",
        unilateral: true,
        muscles: { lats: 1.0, rhomboids: 1.0, biceps: 0.5 },
        mechanic: "multi_joint",
        pattern: "horizontal_pull",
        equipment: ["bench", "dumbbell"],
        load_type: "dumbbell",
      },
      {
        ...exercise,
        id: "front_plank",
        name: "Front Plank",
        category: "core",
        unilateral: false,
        muscles: { rectus_abdominis: 1.0, transversus_abdominis: 1.0 },
        mechanic: "single_joint",
        pattern: "anti_extension",
        equipment: ["bodyweight"],
        load_type: "bodyweight",
        restrictions: ["prone"],
      },
    ],
    notes: [],
  };
}

interface Mutation {
  kind: string;
  mutation_id: string;
  session_client_id?: string;
  set_client_id?: string;
  set?: Record<string, unknown>;
}

interface StoredSession {
  id: string;
  clientId: string;
  finished: boolean;
  sets: Map<string, Record<string, unknown>>;
}

interface FakeServer {
  online: boolean;
  applied: Set<string>;
  received: Mutation[];
  sessions: StoredSession[];
}

async function fakeApi(context: BrowserContext): Promise<FakeServer> {
  const server: FakeServer = {
    online: true,
    applied: new Set(),
    received: [],
    sessions: [],
  };
  const today = athleteToday();
  await context.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not Found" } }),
  );
  await context.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ json: { authenticated: true } }),
  );
  await context.route("**/api/v1/clock", (route) =>
    route.fulfill({ json: { timezone: ATHLETE_ZONE, today } }),
  );
  await context.route("**/api/v1/plan/week**", (route) =>
    route.abort("internetdisconnected"),
  );
  await context.route("**/health", (route) =>
    server.online
      ? route.fulfill({ json: { status: "ok" } })
      : route.abort("internetdisconnected"),
  );
  await context.route("**/api/v1/strength/packet", (route) =>
    server.online
      ? route.fulfill({ json: packet(today) })
      : route.abort("internetdisconnected"),
  );
  await context.route("**/api/v1/sync", (route) => {
    if (!server.online) return route.abort("internetdisconnected");
    const body = route.request().postDataJSON() as { mutations: Mutation[] };
    const results = body.mutations.map((m) => {
      if (server.applied.has(m.mutation_id)) {
        return { mutation_id: m.mutation_id, status: "already_applied" };
      }
      server.applied.add(m.mutation_id);
      server.received.push(m);
      let session = server.sessions.find(
        (s) => s.clientId === m.session_client_id,
      );
      if (m.kind === "session_start" && !session && m.session_client_id) {
        session = {
          id: `0199d000-0000-7000-8000-${String(server.sessions.length + 1).padStart(12, "0")}`,
          clientId: m.session_client_id,
          finished: false,
          sets: new Map(),
        };
        server.sessions.push(session);
      }
      if (session && m.kind === "set_upsert" && m.set_client_id) {
        session.sets.set(m.set_client_id, {
          ...(session.sets.get(m.set_client_id) ?? {}),
          ...m.set,
        });
      }
      if (session && m.kind === "set_delete" && m.set_client_id) {
        session.sets.delete(m.set_client_id);
      }
      if (session && m.kind === "session_finish") session.finished = true;
      return { mutation_id: m.mutation_id, status: "applied" };
    });
    return route.fulfill({ json: { schema_version: 1, results } });
  });
  await context.route("**/api/v1/sessions/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").pop();
    const session = server.sessions.find((s) => s.id === id);
    if (!session) return route.fulfill({ status: 404, json: {} });
    return route.fulfill({
      json: {
        id: session.id,
        logged_sets: [...session.sets.entries()].map(([clientId, set]) => ({
          ...set,
          client_id: clientId,
        })),
      },
    });
  });
  return server;
}

/** Count every pointer press the browser dispatches, across page loads. */
async function countTaps(page: Page) {
  await page.addInitScript(() => {
    document.addEventListener(
      "pointerdown",
      () => {
        const n = Number(sessionStorage.getItem("taps") ?? "0");
        sessionStorage.setItem("taps", String(n + 1));
      },
      true,
    );
  });
}

async function taps(page: Page): Promise<number> {
  return page.evaluate(() => Number(sessionStorage.getItem("taps") ?? "0"));
}

async function largeText(page: Page) {
  await page.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      document.documentElement.style.fontSize = "200%";
    });
  });
}

/** One online visit: the worker installs and the packet is stored. */
async function syncOnce(page: Page) {
  await page.goto("/today");
  await expect(
    page.getByRole("heading", { name: "Today", level: 1 }),
  ).toBeVisible();
  await expect
    .poll(
      () =>
        page.evaluate(async () => {
          if (!navigator.serviceWorker.controller) return false;
          const shell = (await caches.keys()).find((name) =>
            name.startsWith("arc-shell-"),
          );
          if (!shell) return false;
          const cache = await caches.open(shell);
          return Boolean(
            (await cache.match("/today")) &&
              (await cache.match("/train/shell")),
          );
        }),
      { timeout: 20_000 },
    )
    .toBe(true);
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            new Promise<boolean>((resolve) => {
              const open = indexedDB.open("arc-offline");
              open.onerror = () => resolve(false);
              open.onsuccess = () => {
                const db = open.result;
                if (!db.objectStoreNames.contains("packet")) {
                  db.close();
                  resolve(false);
                  return;
                }
                const get = db
                  .transaction("packet")
                  .objectStore("packet")
                  .get("current");
                get.onsuccess = () => {
                  db.close();
                  resolve(Boolean(get.result));
                };
                get.onerror = () => resolve(false);
              };
            }),
        ),
      { timeout: 20_000 },
    )
    .toBe(true);
  // Today again, now listing the stored session with its Start.
  await page.reload();
}

const checkButton = (page: Page, n: number, summary: string) =>
  page.getByRole("button", { name: `Log set ${n}: ${summary}` });

for (const text of ["100 %", "200 %"] as const) {
  test(`the tap budget holds at 360 px and ${text} text (AC-49)`, async ({
    page,
    context,
  }) => {
    await page.setViewportSize({ width: 360, height: 640 });
    if (text === "200 %") await largeText(page);
    await countTaps(page);
    await fakeApi(context);
    await syncOnce(page);

    // Start: one tap, and the session is started — its first set is up.
    let count = await taps(page);
    await page.getByRole("link", { name: /^Start / }).click();
    await expect(
      page.getByRole("heading", { name: "Set 1 of 3" }),
    ).toBeVisible();
    expect(await taps(page)).toBe(count + 1);

    // A set as planned: one tap.
    count = await taps(page);
    await checkButton(page, 1, "20 kg × 10").click();
    await expect(
      page.getByRole("heading", { name: "Set 2 of 3" }),
    ).toBeVisible();
    expect(await taps(page)).toBe(count + 1);

    count = await taps(page);
    await checkButton(page, 2, "20 kg × 10").click();
    await expect(
      page.getByRole("heading", { name: "Set 3 of 3" }),
    ).toBeVisible();
    expect(await taps(page)).toBe(count + 1);

    // The exercise's last set with rating "2": one tap logs and rates.
    count = await taps(page);
    await page
      .getByRole("button", { name: "2 reps left — log the set" })
      .click();
    await expect(
      page.getByRole("heading", { name: "Front Plank", level: 1 }),
    ).toBeVisible();
    expect(await taps(page)).toBe(count + 1);

    for (const n of [1, 2, 3]) {
      count = await taps(page);
      await checkButton(page, n, "45 s").click();
      expect(await taps(page)).toBe(count + 1);
    }

    // Finish after the last set, with the session rating: two taps.
    count = await taps(page);
    await page.getByRole("button", { name: "Finish", exact: true }).click();
    await page.getByRole("button", { name: "7 of 10 — finish" }).click();
    await expect(
      page.getByRole("heading", { name: "Session saved" }),
    ).toBeVisible();
    expect(await taps(page)).toBe(count + 2);
  });
}

test("at 320 px the ✓ and the current set are on screen without scrolling", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await fakeApi(context);
  await syncOnce(page);
  await page.getByRole("link", { name: /^Start / }).click();

  await expect(
    page.getByRole("heading", { name: "Set 1 of 3" }),
  ).toBeInViewport();
  await expect(checkButton(page, 1, "20 kg × 10")).toBeInViewport();

  // With a row logged, the rest timer and Undo above the ✓.
  await checkButton(page, 1, "20 kg × 10").click();
  await expect(page.getByRole("timer", { name: "Rest" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Set 2 of 3" }),
  ).toBeInViewport();
  await expect(checkButton(page, 2, "20 kg × 10")).toBeInViewport();
});

test("in landscape the ✓ stays reachable", async ({ page, context }) => {
  await page.setViewportSize({ width: 640, height: 360 });
  await fakeApi(context);
  await syncOnce(page);
  await page.getByRole("link", { name: /^Start / }).click();

  await expect(checkButton(page, 1, "20 kg × 10")).toBeInViewport();
  await checkButton(page, 1, "20 kg × 10").click();
  await expect(page.getByRole("timer", { name: "Rest" })).toBeVisible();
  await expect(checkButton(page, 2, "20 kg × 10")).toBeInViewport();
});

test("a whole session offline uploads with every set's client id (AC-52)", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 360, height: 640 });
  const server = await fakeApi(context);
  await syncOnce(page);

  server.online = false;
  await context.setOffline(true);
  await page.reload();
  await page.getByRole("link", { name: /^Start / }).click();
  await expect(
    page.getByRole("heading", { name: "Single-Arm Dumbbell Row", level: 1 }),
  ).toBeVisible();

  await checkButton(page, 1, "20 kg × 10").click();
  await checkButton(page, 2, "20 kg × 10").click();
  await expect(page.getByRole("heading", { name: "Set 3 of 3" })).toBeVisible();

  // A forced reload mid-session offers Resume at the next unlogged set —
  // once the second set is on disk (IndexedDB writes land a beat after the
  // screen moves on; the outbox item first, then the screen's memory).
  await expect
    .poll(() =>
      page.evaluate(
        (id) =>
          new Promise<number>((resolve) => {
            const open = indexedDB.open("arc-offline");
            open.onsuccess = () => {
              const db = open.result;
              const get = db
                .transaction("training")
                .objectStore("training")
                .get(id);
              get.onsuccess = () => {
                db.close();
                resolve(get.result?.sets?.length ?? 0);
              };
            };
          }),
        PLANNED_ID,
      ),
    )
    .toBe(2);
  await page.reload();
  const resume = page.getByRole("button", {
    name: "Resume — set 3 of Single-Arm Dumbbell Row",
  });
  await expect(resume).toBeVisible();
  await resume.click();
  await expect(page.getByRole("heading", { name: "Set 3 of 3" })).toBeVisible();

  await page.getByRole("button", { name: "2 reps left — log the set" }).click();
  for (const n of [1, 2, 3]) {
    await checkButton(page, n, "45 s").click();
  }
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  await page.getByRole("button", { name: "7 of 10 — finish" }).click();
  await expect(
    page.getByRole("heading", { name: "Session saved" }),
  ).toBeVisible();
  expect(server.received).toEqual([]);

  server.online = true;
  await context.setOffline(false);

  await expect
    .poll(() => server.sessions[0]?.finished ?? false, { timeout: 15_000 })
    .toBe(true);
  const logged = server.received
    .filter((m) => m.kind === "set_upsert" && m.set?.set_index !== undefined)
    .map((m) => m.set_client_id);
  expect(logged).toHaveLength(6);

  const stored = await page.evaluate(
    async (id) =>
      (await (await fetch(`/api/v1/sessions/${id}`)).json()) as {
        logged_sets: {
          client_id: string;
          reps?: number;
          rir?: number | null;
        }[];
      },
    server.sessions[0].id,
  );
  expect(stored.logged_sets.map((s) => s.client_id).sort()).toEqual(
    [...logged].sort(),
  );
  // The row's three sets carry the rating given on the last one.
  expect(
    stored.logged_sets.filter((s) => s.reps === 10).map((s) => s.rir),
  ).toEqual([2, 2, 2]);
  expect(server.sessions).toHaveLength(1);
});
