import { expect, type Page, test } from "@playwright/test";

/**
 * The inbox, walked end to end in the UI: answer a suspected duplicate, upload
 * a file, and follow the session it became.
 *
 * UI-only, like the rest of this folder: there is no backend behind the
 * production build, so the API is a small stateful fake installed with
 * `page.route`. It holds the two facts the flow depends on — a resolved
 * quarantine record stays resolved, and an uploaded file becomes a session the
 * log can link to — so "the queue emptied" means the page refetched and
 * rendered what the server would have returned.
 */

const RIDE_SESSION = "0199a000-0000-7000-8000-000000000101";
const INGESTED_SESSION = "0199a000-0000-7000-8000-000000000901";
const DUPLICATE_RECORD = "0199a000-0000-7000-8000-000000000201";
const CORRUPT_RECORD = "0199a000-0000-7000-8000-000000000202";

interface QuarantineRecord {
  id: string;
  original_filename: string;
  file_hash: string;
  file_sport_index: number | null;
  reason: string;
  detail: string;
  status: "pending" | "confirmed_discarded" | "rejected_ingested";
  suspected_session_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

interface IngestEvent {
  id: string;
  at: string;
  filename: string;
  file_hash: string | null;
  outcome: string;
  detail: string | null;
  session_id: string | null;
}

/** Install the fake API. Returns the state so a test can assert against it. */
async function mockApi(page: Page) {
  const state = {
    quarantine: [
      {
        id: DUPLICATE_RECORD,
        original_filename: "wahoo-2026-08-05.fit",
        file_hash: "9d4c6f21ae08b357".repeat(4),
        file_sport_index: 0,
        reason: "suspected_duplicate",
        detail:
          "87% of this activity's time range overlaps the session already recorded on 2026-08-05; confirm to discard it, or reject to keep both",
        status: "pending",
        suspected_session_id: RIDE_SESSION,
        created_at: "2026-08-06T06:12:31Z",
        resolved_at: null,
      },
      {
        id: CORRUPT_RECORD,
        original_filename: "corrupt-export.fit",
        file_hash: "c0ffee11deadbeef".repeat(4),
        file_sport_index: null,
        reason: "unreadable_file",
        detail: "not a FIT file: bad header magic",
        status: "pending",
        suspected_session_id: null,
        created_at: "2026-08-06T06:12:33Z",
        resolved_at: null,
      },
    ] as QuarantineRecord[],
    events: [
      {
        id: "0199a000-0000-7000-8000-000000000501",
        at: "2026-08-05T07:55:12Z",
        filename: "2026-08-05-morning-ride.fit",
        file_hash: "1f3a9c0e7b5d2468".repeat(4),
        outcome: "ingested",
        detail: "1 session(s) ingested, 0 quarantined",
        session_id: RIDE_SESSION,
      },
    ] as IngestEvent[],
    uploaded: [] as string[],
  };

  const json = (body: unknown, status = 200) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  const paged = (items: unknown[]) => ({
    items,
    total: items.length,
    offset: 0,
    limit: 50,
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
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

    if (path.endsWith("/ingest/quarantine")) {
      const ordered = [...state.quarantine].sort(
        (left, right) =>
          Number(right.status === "pending") -
          Number(left.status === "pending"),
      );
      return route.fulfill(json(paged(ordered)));
    }
    if (path.endsWith("/ingest/events")) {
      return route.fulfill(json(paged(state.events)));
    }
    if (path.endsWith("/confirm") && method === "POST") {
      const record = state.quarantine.find((entry) =>
        path.includes(entry.id),
      ) as QuarantineRecord;
      if (record.status !== "pending") {
        return route.fulfill(
          json({ detail: "That record is already resolved" }, 409),
        );
      }
      record.status = "confirmed_discarded";
      record.resolved_at = "2026-08-07T09:00:00Z";
      return route.fulfill(json(record));
    }
    if (path.endsWith("/ingest/upload") && method === "POST") {
      // The fake reads the filename out of the multipart body it was posted,
      // so the outcome is a function of the file, not of the route.
      const posted = request.postData() ?? "";
      const filename = /filename="([^"]*)"/.exec(posted)?.[1] ?? "upload";
      state.uploaded.push(filename);
      state.events.unshift({
        id: `0199a000-0000-7000-8000-00000000060${state.uploaded.length}`,
        at: "2026-08-07T09:00:00Z",
        filename,
        file_hash: "aa".repeat(32),
        outcome: "ingested",
        detail: "1 session(s) ingested, 0 quarantined",
        session_id: INGESTED_SESSION,
      });
      return route.fulfill(
        json({
          filename,
          file_hash: "aa".repeat(32),
          outcome: "ingested",
          detail: "1 session(s) ingested, 0 quarantined",
          session_ids: [INGESTED_SESSION],
          quarantine_ids: [],
        }),
      );
    }

    if (path.endsWith("/sessions")) {
      return route.fulfill(json(paged([])));
    }
    if (path.includes("/sessions/") && method === "GET") {
      return route.fulfill(json(sessionRead(path.split("/").pop() as string)));
    }

    return route.fulfill(json({ detail: "unmocked" }, 404));
  });

  return state;
}

/** A hand-entered session, which needs no recording metadata to render. */
function sessionRead(id: string) {
  return {
    id,
    local_date: "2026-08-07",
    start_time: "2026-08-07T05:00:00Z",
    end_time: "2026-08-07T06:00:00Z",
    timezone: "UTC",
    discipline: "cycling",
    classification_source: "sport_field",
    discipline_overridden: false,
    recording_kind: "manual",
    status: "unmatched",
    duration_s: 3600,
    recording_time_s: null,
    rpe: null,
    notes: null,
    recordings: [],
    logged_sets: [],
    created_at: "2026-08-07T06:05:00Z",
    updated_at: "2026-08-07T06:05:00Z",
  };
}

test("answer the inbox, upload a ride, and follow it to its session", async ({
  page,
}) => {
  const state = await mockApi(page);

  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible();
  await expect(page.getByText("2 waiting")).toBeVisible();

  // --- the queue explains itself -------------------------------------------
  const duplicate = page
    .getByTestId("quarantine-record")
    .filter({ hasText: "wahoo-2026-08-05.fit" });
  await expect(duplicate.getByText("Suspected duplicate")).toBeVisible();
  await expect(
    duplicate.getByRole("link", { name: "The session it looks like" }),
  ).toHaveAttribute("href", `/sessions/${RIDE_SESSION}`);

  // Only the duplicate holds something safe to ingest, so only it is offered
  // the second answer.
  const corrupt = page
    .getByTestId("quarantine-record")
    .filter({ hasText: "corrupt-export.fit" });
  await expect(
    corrupt.getByRole("button", { name: "Not a duplicate" }),
  ).toHaveCount(0);

  // --- answer it ------------------------------------------------------------
  await duplicate.getByRole("button", { name: "Discard this copy" }).click();
  await duplicate.getByRole("button", { name: "Discard" }).click();

  await expect(page.getByText("Already decided")).toBeVisible();
  await expect(duplicate.getByText("Discarded")).toBeVisible();
  await expect(page.getByText("1 waiting")).toBeVisible();
  expect(
    state.quarantine.find((record) => record.id === DUPLICATE_RECORD)?.status,
  ).toBe("confirmed_discarded");

  // --- upload a file --------------------------------------------------------
  await page.getByLabel("Activity file").setInputFiles({
    name: "evening-ride.fit",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("a synthetic FIT file"),
  });
  await page.getByRole("button", { name: "Upload" }).click();

  await expect(page.getByRole("status")).toContainText(
    "evening-ride.fit was ingested.",
  );
  expect(state.uploaded).toEqual(["evening-ride.fit"]);
  // The log is the other half of what happened, and it refetched.
  await expect(
    page.getByRole("table").getByRole("link", { name: "evening-ride.fit" }),
  ).toBeVisible();

  // --- follow it to the session --------------------------------------------
  await page.getByRole("link", { name: "Open the session" }).click();
  await expect(page).toHaveURL(new RegExp(`/sessions/${INGESTED_SESSION}$`));
  await expect(
    page.getByRole("heading", { name: "Corrections" }),
  ).toBeVisible();
  await expect(page.getByText(/No device file/)).toBeVisible();
});
