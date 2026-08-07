import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import {
  anchorVersionFixture,
  contentHash,
  DETAILS,
  EXERCISES,
  ingestedSessionFixture,
  ingestState,
  mintId,
  plannedSessionFixture,
  planWeekFixture,
  purposeTemplateFixture,
  SESSION_IDS,
  toListItem,
  WORKOUT_LABELS,
  WORKOUTS,
  workoutFixture,
} from "@/tests/mocks/fixtures";

/**
 * Typed MSW handlers: paths, params, and response bodies are all inferred
 * from the generated OpenAPI types, so a backend contract change that isn't
 * reflected here becomes a type-check failure instead of silent mock drift.
 *
 * **The mutating handlers honour the request.** A fake that answers `POST` and
 * `PATCH` with a canned body cannot fail when the form drops a field, so a
 * test written against it passes whatever the component sends; the same goes
 * for a `move` that ignores the date it was given. These echo what they were
 * asked to do, which is the only version of them a test can actually be
 * wrong about.
 */
export const http = createOpenApiHttp<paths>({
  baseUrl: "http://localhost:8000",
});

/** Default happy-path handlers. Override per-test with server.use(...). */
export const handlers = [
  // Authenticated by default, so component tests don't each have to log in.
  http.get("/api/v1/auth/session", ({ response }) =>
    response(200).json({ authenticated: true }),
  ),
  http.post("/api/v1/auth/login", ({ response }) => response(204).empty()),
  http.get("/api/v1/athlete", ({ response }) =>
    response(200).json({
      name: "Alex Rider",
      date_of_birth: "1990-06-15",
      sex: "male",
      height_cm: 181.5,
      capabilities: {},
      plan_state: "active",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }),
  ),
  http.patch("/api/v1/athlete", async ({ request, response }) => {
    const body = await request.json();
    return response(200).json({
      name: "Alex Rider",
      date_of_birth: "1990-06-15",
      sex: "male",
      height_cm: 181.5,
      capabilities: {},
      plan_state: body.plan_state ?? "active",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  }),

  // The week the calendar asks for, built around whatever `start` it sends.
  http.get("/api/v1/plan/week", ({ query, response }) => {
    const start = query.get("start") ?? mondayOf(todayIsoDate());
    return response(200).json(planWeekFixture(start));
  }),
  http.get(
    "/api/v1/planned-sessions/{planned_session_id}",
    ({ params, response }) =>
      response(200).json(plannedSessionFixture(params.planned_session_id)),
  ),
  // A move lands on the date it was given. Answering with the session's old
  // date would let a component that sent the wrong one still pass.
  http.post(
    "/api/v1/planned-sessions/{planned_session_id}/move",
    async ({ params, request, response }) => {
      const body = await request.json();
      return response(200).json({
        ...plannedSessionFixture(params.planned_session_id),
        date: body.date,
      });
    },
  ),
  // A copy is a *new* session: its own id, back to `planned` however the
  // original ended up, and an intent history that starts at version 1.
  http.post(
    "/api/v1/planned-sessions/{planned_session_id}/copy",
    async ({ params, request, response }) => {
      const body = await request.json();
      const source = plannedSessionFixture(params.planned_session_id);
      return response(201).json({
        ...source,
        id: SESSION_IDS.copy,
        date: body.date,
        status: "planned",
        intent_versions: 1,
        intent: { ...source.intent, version: 1 },
      });
    },
  ),
  http.delete("/api/v1/planned-sessions/{planned_session_id}", ({ response }) =>
    response(204).empty(),
  ),
  http.post("/api/v1/planned-sessions", async ({ request, response }) =>
    response(201).json(
      applyIntent(plannedSessionFixture(SESSION_IDS.vo2), await request.json()),
    ),
  ),
  http.patch(
    "/api/v1/planned-sessions/{planned_session_id}",
    async ({ params, request, response }) =>
      response(200).json(
        applyIntent(
          plannedSessionFixture(params.planned_session_id),
          await request.json(),
        ),
      ),
  ),

  // The library, the catalogue and the templates the creator draws on. The
  // list endpoint filters here the way the API does, so a search test is a
  // test of the *component's* query, not of a stubbed result.
  http.get("/api/v1/workouts", ({ query, response }) => {
    const search = query.get("q")?.toLowerCase() ?? "";
    const folder = query.get("folder");
    const tag = query.get("tag");
    const discipline = query.get("discipline");
    const items = WORKOUTS.filter(
      (workout) =>
        (search === "" ||
          workout.name.toLowerCase().includes(search) ||
          (workout.description ?? "").toLowerCase().includes(search)) &&
        (!folder || workout.folder === folder) &&
        (!tag || workout.tags.includes(tag)) &&
        (!discipline || workout.discipline === discipline),
    );
    return response(200).json({
      items,
      total: items.length,
      offset: 0,
      limit: 100,
    });
  }),
  http.get("/api/v1/workouts/{workout_id}", ({ params, response }) =>
    response(200).json(workoutFixture(params.workout_id)),
  ),
  http.post("/api/v1/workouts", ({ response }) =>
    response(201).json(workoutFixture(WORKOUTS[0]?.id ?? "")),
  ),
  http.patch("/api/v1/workouts/{workout_id}", ({ params, response }) =>
    response(200).json(workoutFixture(params.workout_id)),
  ),
  http.delete("/api/v1/workouts/{workout_id}", ({ response }) =>
    response(204).empty(),
  ),
  http.get("/api/v1/workout-labels", ({ response }) =>
    response(200).json(WORKOUT_LABELS),
  ),
  http.get("/api/v1/exercises", ({ response }) =>
    response(200).json({
      items: EXERCISES,
      total: EXERCISES.length,
      offset: 0,
      limit: 200,
    }),
  ),
  http.get("/api/v1/purposes/{purpose}", ({ params, response }) =>
    response(200).json(purposeTemplateFixture(params.purpose)),
  ),
  http.get("/api/v1/anchors/current", ({ query, response }) => {
    const anchorType = query.get("anchor_type");
    return anchorType === "ftp" || anchorType === "lthr"
      ? response(200).json(anchorVersionFixture(anchorType))
      : response(404).json({ detail: "No max_hr version in force" });
  }),

  // --- ingestion: the queue, the log, the upload ----------------------------
  //
  // These four are stateful, and have to be: a confirm that answered with a
  // canned record could not tell "discard this one" from "discard whichever",
  // and the second confirm on the same record has to be the 409 the API gives
  // — which is only true when something remembers the first. `resetMockState`
  // in the global afterEach is what keeps that state from leaking between
  // tests.
  http.get("/api/v1/ingest/quarantine", ({ query, response }) => {
    const { quarantine } = ingestState();
    // Pending first, then most recent — the order `list_quarantine` sorts in.
    const ordered = [...quarantine].sort(
      (left, right) =>
        Number(right.status === "pending") -
          Number(left.status === "pending") ||
        right.created_at.localeCompare(left.created_at),
    );
    return response(200).json(
      page(ordered, query.get("offset"), query.get("limit")),
    );
  }),
  http.get("/api/v1/ingest/events", ({ query, response }) => {
    const { events } = ingestState();
    const ordered = [...events].sort((left, right) =>
      right.at.localeCompare(left.at),
    );
    return response(200).json(
      page(ordered, query.get("offset"), query.get("limit")),
    );
  }),
  http.post(
    "/api/v1/ingest/quarantine/{record_id}/confirm",
    ({ params, response }) => {
      const state = ingestState();
      const record = state.quarantine.find(
        (entry) => entry.id === params.record_id,
      );
      if (!record) {
        return response(404).json({ detail: "No such quarantine record" });
      }
      if (record.status !== "pending") {
        return response(409).json({
          detail: `Quarantine record ${record.id} is already resolved`,
        });
      }
      record.status = "confirmed_discarded";
      record.resolved_at = NOW;
      return response(200).json(record);
    },
  ),
  http.post(
    "/api/v1/ingest/quarantine/{record_id}/reject",
    ({ params, response }) => {
      const state = ingestState();
      const record = state.quarantine.find(
        (entry) => entry.id === params.record_id,
      );
      if (!record) {
        return response(404).json({ detail: "No such quarantine record" });
      }
      if (record.status !== "pending") {
        return response(409).json({
          detail: `Quarantine record ${record.id} is already resolved`,
        });
      }
      if (!OVERRULABLE.has(record.reason)) {
        // The API's own rule (D107): two verdicts can be overruled — a
        // suspected duplicate and an implausible channel — and nothing about
        // disagreeing with the parser makes unreadable bytes readable.
        return response(409).json({
          detail:
            "Only a suspected duplicate or an implausible channel can be " +
            "rejected; this file could not be read",
        });
      }
      record.status = "rejected_ingested";
      record.resolved_at = NOW;
      const session = ingestedSessionFixture(
        record.file_hash,
        record.original_filename,
      );
      state.sessions.unshift(session);
      state.events.unshift({
        id: mintId(),
        at: NOW,
        filename: record.original_filename,
        file_hash: record.file_hash,
        outcome: "ingested",
        detail: "1 session(s) ingested, 0 quarantined",
        session_id: session.id,
      });
      return response(200).json({
        record,
        report: {
          filename: record.original_filename,
          file_hash: record.file_hash,
          outcome: "ingested",
          detail: "1 session(s) ingested, 0 quarantined",
          session_ids: [session.id],
          quarantine_ids: [],
        },
      });
    },
  ),
  // The outcome is a function of the bytes that were posted, not of which
  // test installed the handler: the same file twice is a duplicate because
  // its digest is already known, and an extension no parser reads is
  // quarantined the way the pipeline quarantines one.
  http.post("/api/v1/ingest/upload", async ({ request, response }) => {
    const part = readUploadedFile(
      await request.text(),
      request.headers.get("content-type") ?? "",
    );
    if (part === null || part.body === "") {
      return response(422).json({ detail: "The uploaded file is empty" });
    }
    const state = ingestState();
    const filename = part.filename;
    const hash = contentHash(part.body);

    const already = state.known.get(hash);
    if (already) {
      const detail = `already ingested as ${already.length} recording(s) of this file`;
      state.events.unshift({
        id: mintId(),
        at: NOW,
        filename,
        file_hash: hash,
        outcome: "duplicate_file",
        detail,
        session_id: already[0] ?? null,
      });
      return response(200).json({
        filename,
        file_hash: hash,
        outcome: "duplicate_file",
        detail,
        session_ids: already,
        quarantine_ids: [],
      });
    }

    const extension = filename.split(".").pop()?.toLowerCase() ?? "";
    if (!READABLE_EXTENSIONS.has(extension)) {
      const detail = DETAILS.noParser(filename);
      const record: components["schemas"]["QuarantineRecordRead"] = {
        id: mintId(),
        original_filename: filename,
        file_hash: hash,
        file_sport_index: null,
        reason: "unreadable_file",
        detail,
        status: "pending",
        suspected_session_id: null,
        created_at: NOW,
        resolved_at: null,
      };
      state.quarantine.unshift(record);
      state.events.unshift({
        id: mintId(),
        at: NOW,
        filename,
        file_hash: hash,
        outcome: "quarantined",
        detail,
        session_id: null,
      });
      return response(200).json({
        filename,
        file_hash: hash,
        outcome: "quarantined",
        detail,
        session_ids: [],
        quarantine_ids: [record.id],
      });
    }

    const session = ingestedSessionFixture(hash, filename);
    state.sessions.unshift(session);
    state.known.set(hash, [session.id]);
    const detail = "1 session(s) ingested, 0 quarantined";
    state.events.unshift({
      id: mintId(),
      at: NOW,
      filename,
      file_hash: hash,
      outcome: "ingested",
      detail,
      session_id: session.id,
    });
    return response(200).json({
      filename,
      file_hash: hash,
      outcome: "ingested",
      detail,
      session_ids: [session.id],
      quarantine_ids: [],
    });
  }),

  // --- the session log ------------------------------------------------------
  http.get("/api/v1/sessions", ({ query, response }) => {
    const discipline = query.get("discipline");
    const start = query.get("start");
    const end = query.get("end");
    const rows = ingestState()
      .sessions.filter(
        (session) =>
          (!discipline || session.discipline === discipline) &&
          (!start || session.local_date >= start) &&
          (!end || session.local_date <= end),
      )
      .map(toListItem);
    return response(200).json(
      page(rows, query.get("offset"), query.get("limit")),
    );
  }),
  http.get("/api/v1/sessions/{session_id}", ({ params, response }) => {
    const session = ingestState().sessions.find(
      (row) => row.id === params.session_id,
    );
    return session
      ? response(200).json(session)
      : response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
  }),
  // A correction answers with the session as corrected — including the date
  // a new timezone re-derives, which is the whole point of the field (D93).
  // Answering with the stored row would let a page that sent the wrong zone
  // still look right.
  http.patch(
    "/api/v1/sessions/{session_id}",
    async ({ params, request, response }) => {
      const session = ingestState().sessions.find(
        (row) => row.id === params.session_id,
      );
      if (!session) {
        return response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
      }
      const body = await request.json();
      if (body.discipline) {
        session.discipline = body.discipline;
        session.discipline_overridden = true;
        session.classification_source = "manual";
      }
      if (body.timezone) {
        if (UNRESOLVABLE_ZONES.has(body.timezone)) {
          return response(422).json({
            detail: `'${body.timezone}' is neither 'UTC', a UTC±HH:MM offset, nor a known IANA timezone name`,
          });
        }
        session.timezone = body.timezone;
        session.local_date = statedLocalDate(session.start_time, body.timezone);
      }
      session.updated_at = NOW;
      return response(200).json(session);
    },
  ),
];

/**
 * The zones this mock refuses, listed rather than decided.
 *
 * `app.services.activity` resolves a zone with `zoneinfo` against the tzdata
 * on the server; the browser has its own copy and its own opinion, so the
 * mock cannot *compute* the API's answer. It states it. A zone a test uses
 * that is in neither this set nor `LOCAL_DATES` makes `statedLocalDate` throw
 * — refusing to invent an answer is the point.
 */
const UNRESOLVABLE_ZONES = new Set([
  "Middle-earth/Shire",
  "Middle/Earth",
  "Mars/Olympus_Mons",
]);

/**
 * What day a timezone puts a session on, **stated**, not derived.
 *
 * The handler used to answer this by calling `localStamp` — the application's
 * own function, and the one the page under test uses to render the date it
 * gets back. A mock that computes its reply with the code being tested agrees
 * with that code by construction: break `localStamp` and the fixture breaks
 * with it, the assertion still passes, and the test has verified nothing but
 * that a function equals itself. So the answers are written down, worked out
 * from the offsets by hand.
 */
const LOCAL_DATES: Readonly<Record<string, string>> = {
  // The trainer ride, 2026-08-03 16:02 UTC.
  //   +12:00 in August (NZST) → 04:02 on the 4th.
  "2026-08-03T16:02:00Z|Pacific/Auckland": "2026-08-04",
  //   +14:00 → 06:02 on the 4th.
  "2026-08-03T16:02:00Z|Pacific/Kiritimati": "2026-08-04",
  //   the stored zone itself: +02:00 → 18:02, still the 3rd.
  "2026-08-03T16:02:00Z|UTC+02:00": "2026-08-03",
  //   UTC → 16:02, the 3rd.
  "2026-08-03T16:02:00Z|UTC": "2026-08-03",
  //   +02:00 (CEST) → 18:02, the 3rd.
  "2026-08-03T16:02:00Z|Europe/Zurich": "2026-08-03",
  // The morning ride, 2026-08-05 05:14 UTC — early enough that nothing west
  // of UTC moves it, and +02:00 does not either.
  "2026-08-05T05:14:00Z|Europe/Zurich": "2026-08-05",
  "2026-08-05T05:14:00Z|UTC": "2026-08-05",
  //   −10:00 (HST) → 19:14 on the 4th.
  "2026-08-05T05:14:00Z|Pacific/Honolulu": "2026-08-04",
  // The gym session, 2026-08-06 16:30 UTC.
  "2026-08-06T16:30:00Z|Europe/Zurich": "2026-08-06",
  "2026-08-06T16:30:00Z|UTC": "2026-08-06",
  //   +12:00 → 04:30 on the 7th.
  "2026-08-06T16:30:00Z|Pacific/Auckland": "2026-08-07",
};

function statedLocalDate(startTime: string, timezone: string): string {
  const stated = LOCAL_DATES[`${startTime}|${timezone}`];
  if (stated === undefined) {
    throw new Error(
      `The sessions mock has no stated local_date for '${timezone}' at ` +
        `${startTime}. Add the day it falls on to LOCAL_DATES (or the zone to ` +
        "UNRESOLVABLE_ZONES) rather than letting the mock derive its own answer.",
    );
  }
  return stated;
}

/** The instant the mock pipeline claims to have acted. */
const NOW = "2026-08-07T09:00:00Z";

/** What the parsers can open (`app.ingest.parsers.base.SUPPORTED_EXTENSIONS`). */
const READABLE_EXTENSIONS = new Set(["fit", "gpx", "tcx"]);

/** The verdicts `IngestService.reject` accepts (D107); every other one is a 409. */
const OVERRULABLE = new Set<components["schemas"]["QuarantineReason"]>([
  "suspected_duplicate",
  "implausible_channel",
]);

/**
 * Pull the `file` part out of a multipart body, by hand.
 *
 * `request.formData()` would be the obvious way, and it does not work here:
 * under jsdom the `File` global is jsdom's, undici's multipart parser builds
 * its entries with it, and undici's own brand check then rejects them. Reading
 * the raw body sidesteps that entirely — and it is still the bytes the
 * component actually posted, which is the only thing the assertion needs.
 */
function readUploadedFile(
  body: string,
  contentType: string,
): { filename: string; body: string } | null {
  const found = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType);
  const boundary = found?.[1] ?? found?.[2];
  if (!boundary) {
    return null;
  }
  for (const section of body.split(`--${boundary}`)) {
    const split = section.indexOf("\r\n\r\n");
    if (split === -1 || !/name="file"/.test(section.slice(0, split))) {
      continue;
    }
    return {
      filename: /filename="([^"]*)"/.exec(section.slice(0, split))?.[1] ?? "",
      // The part's content ends at the CRLF that precedes the next boundary.
      body: section.slice(split + 4).replace(/\r\n$/, ""),
    };
  }
  return null;
}

/**
 * Slice a list the way every paginated endpoint here does.
 *
 * The handler honours `offset` and `limit` rather than returning everything,
 * so a page that never sends them — or sends the same offset twice — fails
 * instead of looking like it worked.
 */
function page<T>(
  items: readonly T[],
  rawOffset: string | null,
  rawLimit: string | null,
): { items: T[]; total: number; offset: number; limit: number } {
  const offset = Number(rawOffset ?? 0);
  const limit = Number(rawLimit ?? 50);
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    offset,
    limit,
  };
}

type PlannedSession = components["schemas"]["PlannedSessionRead"];
type PlannedSessionWrite =
  | components["schemas"]["PlannedSessionCreate"]
  | components["schemas"]["PlannedSessionUpdate"];

/**
 * Answer a write with what it actually asked for.
 *
 * The API appends a new intent version carrying the fields it was sent, so a
 * response that quietly kept the old purpose or dropped the criteria would be
 * a response no server produces — and would hide exactly the class of bug
 * these tests exist to catch (a form that posts an empty `success_criteria`).
 * Only the intent fields are echoed; the resolved steps and predictions stay
 * the fixture's, because recomputing them here would be reimplementing the
 * domain in a mock.
 */
function applyIntent(
  session: PlannedSession,
  body: PlannedSessionWrite,
): PlannedSession {
  return {
    ...session,
    date: body.date ?? session.date,
    intent: {
      ...session.intent,
      version: session.intent.version + 1,
      purpose: body.purpose ?? session.intent.purpose,
      intent_text:
        body.intent_text === undefined
          ? session.intent.intent_text
          : body.intent_text,
      coach_notes:
        body.coach_notes === undefined
          ? session.intent.coach_notes
          : body.coach_notes,
      success_criteria:
        body.success_criteria ?? session.intent.success_criteria,
      workout_id:
        body.workout_id === undefined
          ? session.intent.workout_id
          : body.workout_id,
      structure: body.structure ?? session.intent.structure,
    },
  };
}
