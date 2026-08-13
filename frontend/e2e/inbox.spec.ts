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
const REJECTED_SESSION = "0199a000-0000-7000-8000-000000000902";
const DUPLICATE_RECORD = "0199a000-0000-7000-8000-000000000201";
const CORRUPT_RECORD = "0199a000-0000-7000-8000-000000000202";
const STRAP_RECORD = "0199a000-0000-7000-8000-000000000203";

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
        // The parser's own sentence (`app.ingest.parsers.fit`), not a tidier
        // one written for the fixture.
        detail:
          "the file is not a readable FIT recording: no samples could be " +
          "decoded from it (not a FIT file @ 0; the Garmin decoder said: " +
          "not a FIT file)",
        status: "pending",
        suspected_session_id: null,
        created_at: "2026-08-06T06:12:33Z",
        resolved_at: null,
      },
      {
        // The other verdict the API lets you overrule: the ride is
        // fine, the strap is not, and the cleaner blanks what it cannot
        // believe — so "ingest it anyway" is a real answer here.
        id: STRAP_RECORD,
        original_filename: "strap-2026-08-06.fit",
        file_hash: "ab12cd34ef56ab78".repeat(4),
        file_sport_index: 0,
        reason: "implausible_channel",
        detail:
          "41% of the hr channel is outside 20-230 bpm; a mis-paired sensor rather than a spike",
        status: "pending",
        suspected_session_id: null,
        created_at: "2026-08-06T06:12:35Z",
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
    if (path.endsWith("/reject") && method === "POST") {
      const record = state.quarantine.find((entry) =>
        path.includes(entry.id),
      ) as QuarantineRecord;
      if (record.status !== "pending") {
        return route.fulfill(
          json({ detail: "That record is already resolved" }, 409),
        );
      }
      // The API's own rule: two verdicts can be overruled, and this
      // fake holds the line so the spec walks the path the server allows.
      if (
        record.reason !== "suspected_duplicate" &&
        record.reason !== "implausible_channel"
      ) {
        return route.fulfill(
          json(
            {
              detail:
                "Only a suspected duplicate or an implausible channel can " +
                "be rejected; this file could not be read",
            },
            409,
          ),
        );
      }
      record.status = "rejected_ingested";
      record.resolved_at = "2026-08-07T09:00:00Z";
      state.events.unshift({
        id: "0199a000-0000-7000-8000-000000000701",
        at: "2026-08-07T09:00:00Z",
        filename: record.original_filename,
        file_hash: record.file_hash,
        outcome: "ingested",
        detail: "1 session(s) ingested, 0 quarantined",
        session_id: REJECTED_SESSION,
      });
      return route.fulfill(
        json({
          record,
          report: {
            filename: record.original_filename,
            file_hash: record.file_hash,
            outcome: "ingested",
            detail: "1 session(s) ingested, 0 quarantined",
            session_ids: [REJECTED_SESSION],
            quarantine_ids: [],
          },
        }),
      );
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
    // WP-7's facets hang one segment below the session, and this session is
    // **unmatched** — so the API 404s all three, with the sentence that names
    // the missing input. Answering them with the session itself (which the
    // looser `/sessions/` test below used to do) is not a lenient fake, it is
    // a fake serving a payload no endpoint can produce.
    if (/\/sessions\/[^/]+\/(score|alignment|verdict)$/.test(path)) {
      return route.fulfill(
        json(
          {
            detail:
              "This session has no score. A session is scored once it is " +
              "linked to a planned session and that link is settled; a " +
              "pending proposal is a question, not a link. A session that " +
              "was scored and then unlinked keeps its versions on its score " +
              "history, but no longer answers to a prescription.",
          },
          404,
        ),
      );
    }
    if (/\/sessions\/[^/]+$/.test(path) && method === "GET") {
      return route.fulfill(json(sessionRead(path.split("/").pop() as string)));
    }

    return route.fulfill(json({ detail: "unmocked" }, 404));
  });

  return state;
}

/**
 * The session an ingested file became — a **device** session, with the file.
 *
 * It used to be a hand-entered one: `recording_kind: "manual"` beside
 * `classification_source: "sport_field"` and no recordings, which is a payload
 * the API cannot produce (nothing classified it from a sport field, because
 * there was no file), and which made the spec walk from an uploaded FIT to a
 * page saying "No device file". `RecordingPanel` — the half of this page that
 * explains the ride's own numbers — was never rendered end to end.
 *
 * The arithmetic is the API's, exactly:
 *
 * * `end_time − start_time` is the **elapsed** time: 05:00 to 06:45 is 6300 s;
 * * a stop is a half-open row range on the 1 Hz grid, so 2400–2700 is
 *   300 rows and 300 seconds, and `elapsed − recording` is that sum exactly
 *   — 6300 − 300 = 6000;
 * * `duration_s` for a device session **is** the recording time
 *   (`_duration`), so both say 6000;
 * * moving time is time at or above 1 km/h, so it sits under the recording
 *   time rather than above it.
 */
function sessionRead(id: string) {
  return {
    id,
    local_date: "2026-08-07",
    start_time: "2026-08-07T05:00:00Z",
    end_time: "2026-08-07T06:45:00Z",
    timezone: "UTC",
    discipline: "cycling",
    classification_source: "sport_field",
    discipline_overridden: false,
    recording_kind: "device",
    status: "unmatched",
    duration_s: 6000,
    recording_time_s: 6000,
    rpe: null,
    notes: null,
    recordings: [
      {
        id: "0199a000-0000-7000-8000-000000000301",
        file_hash: "1f3a9c0e7b5d2468".repeat(4),
        file_sport_index: 0,
        original_ext: "fit",
        sport: "cycling",
        elapsed_time_s: 6300,
        recording_time_s: 6000,
        recording_stops: [{ start_index: 2400, end_index: 2700 }],
        median_time_delta_s: 1,
        moving_time_s: 5820,
        power_source_candidates: ["Wahoo KICKR"],
        power_source: "Wahoo KICKR",
        power_source_rule: "only candidate",
        hr_source_candidates: ["Garmin HRM-Pro"],
        hr_source: "Garmin HRM-Pro",
        hr_source_rule: "only candidate",
        channels: ["power", "hr", "cadence", "speed"],
        anomaly_count: 0,
        created_at: "2026-08-07T06:50:00Z",
      },
    ],
    logged_sets: [],
    created_at: "2026-08-07T06:50:00Z",
    updated_at: "2026-08-07T06:50:00Z",
  };
}

test("answer the inbox, upload a ride, and follow it to its session", async ({
  page,
}) => {
  const state = await mockApi(page);

  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible();
  await expect(page.getByText("3 waiting")).toBeVisible();

  // --- the queue explains itself -------------------------------------------
  const duplicate = page
    .getByTestId("quarantine-record")
    .filter({ hasText: "wahoo-2026-08-05.fit" });
  await expect(duplicate.getByText("Suspected duplicate")).toBeVisible();
  await expect(
    duplicate.getByRole("link", { name: "The session it looks like" }),
  ).toHaveAttribute("href", `/sessions/${RIDE_SESSION}`);

  // Two verdicts can be overruled, and they are offered in their own words:
  // "not a duplicate" for the overlap, "ingest it anyway" for the broken
  // strap. Unreadable bytes stay unreadable, so that card gets neither.
  const strap = page
    .getByTestId("quarantine-record")
    .filter({ hasText: "strap-2026-08-06.fit" });
  await expect(
    strap.getByRole("button", { name: "Ingest it anyway" }),
  ).toBeVisible();
  const corrupt = page
    .getByTestId("quarantine-record")
    .filter({ hasText: "corrupt-export.fit" });
  await expect(
    corrupt.getByRole("button", { name: "Not a duplicate" }),
  ).toHaveCount(0);
  await expect(
    corrupt.getByRole("button", { name: "Ingest it anyway" }),
  ).toHaveCount(0);
  await expect(
    corrupt.getByRole("button", { name: "Discard this copy" }),
  ).toBeVisible();

  // --- answer it ------------------------------------------------------------
  await duplicate.getByRole("button", { name: "Discard this copy" }).click();
  await duplicate.getByRole("button", { name: "Discard" }).click();

  await expect(page.getByText("Already decided")).toBeVisible();
  await expect(duplicate.getByText("Discarded")).toBeVisible();
  await expect(page.getByText("2 waiting")).toBeVisible();
  expect(
    state.quarantine.find((record) => record.id === DUPLICATE_RECORD)?.status,
  ).toBe("confirmed_discarded");

  // --- overrule the other one ----------------------------------------------
  await strap.getByRole("button", { name: "Ingest it anyway" }).click();
  await expect(
    page.getByRole("alertdialog", {
      name: "Ingest it anyway — the broken channel arrives blanked?",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Ingest it" }).click();

  await expect(strap.getByText("Ingested anyway")).toBeVisible();
  await expect(page.getByText("1 waiting")).toBeVisible();
  expect(
    state.quarantine.find((record) => record.id === STRAP_RECORD)?.status,
  ).toBe("rejected_ingested");
  // And the ingest it produced is in the log, linked to the session it became.
  await expect(
    page.getByRole("table").getByRole("link", { name: "strap-2026-08-06.fit" }),
  ).toHaveAttribute("href", `/sessions/${REJECTED_SESSION}`);

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

  // The recording panel: the half of the page that explains the ride's own
  // numbers. It renders only for a session with a file behind it, which is
  // what an uploaded FIT produces — so this is the assertion that proves the
  // upload led somewhere, rather than a "No device file" notice.
  await expect(page.getByText(/No device file/)).toHaveCount(0);
  await expect(page.getByText("Wahoo KICKR")).toBeVisible();
  await expect(page.getByText("Garmin HRM-Pro")).toBeVisible();
  // Both channels had one candidate, so both print the tie-break that was
  // never needed — FIT names candidates and nothing that chose.
  await expect(page.getByText("chosen: only candidate")).toHaveCount(2);
  // 6300 s elapsed, one 300-row stop, 6000 s recorded — the page shows its
  // arithmetic rather than asserting it.
  await expect(page.getByText("1:45:00")).toBeVisible();
  // Twice: the session's "Recording time" and the file's "Recording" are the
  // same 6000 s, which is what makes the session's account of itself the
  // file's account of itself.
  await expect(page.getByText("1:40:00")).toHaveCount(2);
  await expect(page.getByText("1 · 5:00 paused")).toBeVisible();
  // And the wall clock is the wall clock: "Duration" is end minus start, so
  // it differs from the recording time by exactly that paused total.
  await expect(page.getByText("1:45", { exact: true })).toBeVisible();
});
