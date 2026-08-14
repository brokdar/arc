import { createOpenApiHttp } from "openapi-msw";

import type { components, paths } from "@/generated/api/schema";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import {
  AGENT_NOW,
  alignmentRead,
  anchorHistory,
  appendAnchorVersion,
  applyLinkStatuses,
  athleteRecord,
  contentHash,
  currentAnchor,
  DETAILS,
  declarationRead,
  defaultZoneModel,
  EXERCISES,
  ingestedSessionFixture,
  ingestState,
  linkForPlanned,
  linkForSession,
  linkRecord,
  MATCH_NOW,
  matchRead,
  matchSummary,
  mintId,
  noteList,
  patchAthlete,
  patchWellnessDay,
  plannedSessionFixture,
  planWeekFixture,
  proposalById,
  proposalList,
  purposeTemplateFixture,
  RIDE_METRICS,
  RIDE_STREAMS,
  rateNote,
  reasonsRead,
  reasonsRefusal,
  restoreLinkStatuses,
  SCORING_NOW,
  SESSION_IDS,
  scoreRead,
  scoringFor,
  statedBreakdown,
  statedRematch,
  statedScoring,
  toListItem,
  updateProposal,
  WORKOUT_LABELS,
  WORKOUTS,
  wellnessDay,
  wellnessInputs,
  wellnessRange,
  wellnessTrend,
  wellnessWeightInForce,
  withMatch,
  workoutFixture,
  zoneModelMatches,
  zonesFixture,
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
  // The profile is *stateful*: the red flag is set from the UI and read back
  // by the shell's banner, so a PATCH that answered with a canned profile
  // would let a form that sends the wrong body still light the banner up.
  http.get("/api/v1/athlete", ({ response }) =>
    response(200).json(athleteRecord()),
  ),
  http.patch("/api/v1/athlete", async ({ request, response }) => {
    const result = patchAthlete(await request.json());
    return "detail" in result
      ? response(422).json({ detail: result.detail })
      : response(200).json(result.athlete);
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
  // --- WP-1: anchors and the zones derived from them ------------------------
  //
  // Stateful, like the ingest queue and for the same reason: an append that
  // answered with a canned version could not fail when the form sends the
  // wrong anchor type or drops the protocol off a tested value, and "the
  // history now has the version I just entered, and it is the one in force"
  // is the whole behaviour of the settings page. The refusals live in
  // `appendAnchorVersion`, which applies the service's rules rather than
  // guessing at them.
  http.get("/api/v1/anchors", ({ query, response }) => {
    const anchorType = query.get("anchor_type") as
      | components["schemas"]["AnchorType"]
      | null;
    return response(200).json(
      page(anchorHistory(anchorType), query.get("offset"), query.get("limit")),
    );
  }),
  http.post("/api/v1/anchors", async ({ request, response }) => {
    const result = appendAnchorVersion(await request.json());
    return "detail" in result
      ? response(422).json({ detail: result.detail })
      : response(201).json(result.version);
  }),
  http.get("/api/v1/anchors/current", ({ query, response }) => {
    const anchorType = query.get(
      "anchor_type",
    ) as components["schemas"]["AnchorType"];
    const version = currentAnchor(anchorType);
    return version
      ? response(200).json(version)
      : response(404).json({
          detail: `No ${anchorType} anchor is in force; append one first`,
        });
  }),
  http.get("/api/v1/zones", ({ query, response }) => {
    const anchorType = query.get(
      "anchor_type",
    ) as components["schemas"]["AnchorType"];
    const model =
      (query.get("zone_model") as components["schemas"]["ZoneModel"] | null) ??
      defaultZoneModel(anchorType);
    // Two refusals the API makes and this has to make too: no model derives
    // from `max_hr` at all, and asking for the power scheme off a heart rate
    // produces plausible-looking nonsense, so the pairing is checked.
    if (!model) {
      return response(422).json({
        detail: `no zone model derives from ${anchorType}`,
      });
    }
    if (!zoneModelMatches(model, anchorType)) {
      return response(422).json({
        detail: `zone model ${model} does not derive from ${anchorType}`,
      });
    }
    const version = currentAnchor(anchorType);
    return version
      ? response(200).json(zonesFixture(version, model))
      : response(404).json({
          detail: `No ${anchorType} anchor is in force; append one first`,
        });
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
        // The API's own rule: two verdicts can be overruled — a
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
      // The link is attached on the way out rather than stored on the row: it
      // is a fact about two resources, and a copy on the session would go
      // stale the moment a confirm or an unlink moved it.
      .map((session) => withMatch(toListItem(session)));
    return response(200).json(
      page(rows, query.get("offset"), query.get("limit")),
    );
  }),
  http.get("/api/v1/sessions/{session_id}", ({ params, response }) => {
    const session = ingestState().sessions.find(
      (row) => row.id === params.session_id,
    );
    // The day and the weight are resolved at response time from the wellness
    // store rather than baked into the fixture, so a test that records a
    // morning and then opens that day's session sees the two joined — which
    // is the whole point of carrying them here.
    return session
      ? response(200).json({
          ...withMatch(session),
          wellness: wellnessDay(session.local_date),
          weight_kg_in_force: wellnessWeightInForce(session.local_date),
        })
      : response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
  }),
  // The chart payload. Served only for a session that has a recording: a
  // typed-in gym session never had samples, and the 404's *detail* is the
  // empty state the page renders, so the mock states it rather than an
  // empty body.
  http.get("/api/v1/sessions/{session_id}/streams", ({ params, response }) => {
    const session = ingestState().sessions.find(
      (row) => row.id === params.session_id,
    );
    if (!session) {
      return response(404).json({
        detail: `Session ${params.session_id} not found`,
      });
    }
    if (session.recordings.length === 0) {
      return response(404).json({
        detail: `Session ${session.id} has no recorded stream: it was entered by hand, so there are no per-second samples to chart`,
      });
    }
    return response(200).json(RIDE_STREAMS);
  }),
  // Recompute **appends**: the handler honours that by bumping the version
  // and stating a reason, and by leaving the numbers alone — nothing about
  // the ride changed, so nothing derived from it should either. A canned
  // reply that always said "version 1" could not fail when the mutation
  // stopped writing anything.
  http.post(
    "/api/v1/sessions/{session_id}/metrics/recompute",
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
      const next = {
        ...RIDE_METRICS,
        version: (session.metrics?.version ?? 0) + 1,
        computed_at: NOW,
        recompute_reason: body?.reason ?? "recomputed on request",
      };
      session.metrics = next;
      session.load = next.load.training_load ?? null;
      session.load_basis = next.load.load_basis ?? null;
      return response(200).json(next);
    },
  ),
  // A correction answers with the session as corrected — including the date
  // a new timezone re-derives, which is the whole point of the field.
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
      // The context fields (#23) are patch-semantics: an omitted field is
      // untouched, an explicit null clears. The handler honours what it was
      // sent — a canned echo could not fail when the form dropped a field.
      if (body.rpe !== undefined) {
        session.rpe = body.rpe;
      }
      if (body.temperature_c !== undefined) {
        session.temperature_c = body.temperature_c;
      }
      session.updated_at = NOW;
      return response(200).json(withMatch(session));
    },
  ),

  // --- the plan a recording is matched against ------------------------------
  //
  // The date range is honoured, because the picker's whole job is to ask for
  // the days either side of one session: a handler that returned the plan
  // whatever it was asked for could not fail when the component sent the
  // wrong window.
  http.get("/api/v1/planned-sessions", ({ query, response }) => {
    const start = query.get("start");
    const end = query.get("end");
    const status = query.get("status");
    const rows = ingestState()
      .planned.filter(
        (planned) =>
          (!start || planned.date >= start) &&
          (!end || planned.date <= end) &&
          (!status || planned.status === status),
      )
      .map((planned) => ({
        ...planned,
        match: matchSummary(linkForPlanned(planned.id)),
      }))
      .sort((left, right) => left.date.localeCompare(right.date));
    return response(200).json(
      page(rows, query.get("offset"), query.get("limit")),
    );
  }),

  // --- matches (WP-6) -------------------------------------------------------
  //
  // Stateful for the same reason the quarantine handlers are: a confirm that
  // answered with a canned `confirmed` link could not tell whether the page
  // confirmed the right one, could not make the *next* GET agree with it, and
  // could not produce the 409 the API gives when the same proposal is answered
  // twice. Every status either side moves to is the table in
  // `app.services.matching`'s docstring, applied by `applyLinkStatuses`.
  http.get("/api/v1/matches", ({ query, response }) => {
    const status = query.get("status");
    const rows = ingestState()
      .matches.filter((link) => !status || link.status === status)
      .map((link) => matchRead(link));
    return response(200).json(
      page(rows, query.get("offset"), query.get("limit")),
    );
  }),
  http.get("/api/v1/matches/{match_id}", ({ params, response }) => {
    const link = ingestState().matches.find(
      (row) => row.id === params.match_id,
    );
    return link
      ? response(200).json(matchRead(link))
      : response(404).json({ detail: `Match ${params.match_id} not found` });
  }),
  http.post("/api/v1/matches", async ({ request, response }) => {
    const state = ingestState();
    const body = await request.json();
    const session = state.sessions.find((row) => row.id === body.session_id);
    const planned = state.planned.find(
      (row) => row.id === body.planned_session_id,
    );
    if (!session) {
      return response(404).json({
        detail: `Session ${body.session_id} not found`,
      });
    }
    if (!planned) {
      return response(404).json({
        detail: `Planned session ${body.planned_session_id} not found`,
      });
    }
    if (linkForSession(session.id)) {
      return response(409).json({
        detail:
          `Session ${session.id} is already matched; unlink it or swap the ` +
          "existing match to another planned session.",
      });
    }
    if (linkForPlanned(planned.id)) {
      return response(409).json({
        detail:
          `Planned session ${planned.id} is already matched to another ` +
          "session; unlink that one first.",
      });
    }
    if (session.discipline !== planned.discipline) {
      return response(422).json({
        detail:
          `A ${session.discipline} session cannot answer to a ` +
          `${planned.discipline} planned session`,
      });
    }
    const link = linkRecord({
      sessionId: session.id,
      plannedSessionId: planned.id,
      status: body.displaced ? "displaced" : "confirmed",
      createdBy: "athlete",
    });
    state.matches.unshift(link);
    applyLinkStatuses(link);
    return response(201).json(matchRead(link));
  }),
  http.post("/api/v1/matches/{match_id}/confirm", ({ params, response }) => {
    const link = ingestState().matches.find(
      (row) => row.id === params.match_id,
    );
    if (!link) {
      return response(404).json({
        detail: `Match ${params.match_id} not found`,
      });
    }
    if (link.status === "confirmed" || link.status === "displaced") {
      return response(409).json({
        detail: `Match ${link.id} is already confirmed`,
      });
    }
    link.status = "confirmed";
    link.confirmed_at = MATCH_NOW;
    link.updated_at = MATCH_NOW;
    applyLinkStatuses(link);
    return response(200).json(matchRead(link));
  }),
  // Rejecting is not unlinking: the planned session goes back to what it was,
  // and the session becomes `unplanned` — the athlete saying "that ride was
  // not that session", which is an answer rather than an undo.
  http.post("/api/v1/matches/{match_id}/reject", ({ params, response }) => {
    const state = ingestState();
    const link = state.matches.find((row) => row.id === params.match_id);
    if (!link) {
      return response(404).json({
        detail: `Match ${params.match_id} not found`,
      });
    }
    if (link.status === "confirmed" || link.status === "displaced") {
      return response(409).json({
        detail: `Match ${link.id} is your own; unlink it instead of rejecting it`,
      });
    }
    restoreLinkStatuses(link);
    state.matches = state.matches.filter((row) => row.id !== link.id);
    const session = state.sessions.find((row) => row.id === link.session_id);
    if (session) {
      session.status = "unplanned";
    }
    return response(200).json({
      session_id: link.session_id,
      status: session?.status ?? "unplanned",
      match: null,
    });
  }),
  http.patch(
    "/api/v1/matches/{match_id}",
    async ({ params, request, response }) => {
      const state = ingestState();
      const link = state.matches.find((row) => row.id === params.match_id);
      if (!link) {
        return response(404).json({
          detail: `Match ${params.match_id} not found`,
        });
      }
      const body = await request.json();
      const target = state.planned.find(
        (row) => row.id === body.planned_session_id,
      );
      if (!target) {
        return response(404).json({
          detail: `Planned session ${body.planned_session_id} not found`,
        });
      }
      const session = state.sessions.find((row) => row.id === link.session_id);
      if (session && session.discipline !== target.discipline) {
        return response(422).json({
          detail:
            `A ${session.discipline} session cannot answer to a ` +
            `${target.discipline} planned session`,
        });
      }
      const taken = linkForPlanned(target.id);
      if (taken && taken.id !== link.id) {
        return response(409).json({
          detail:
            `Planned session ${target.id} is already matched to another ` +
            "session; unlink that one first.",
        });
      }
      // The old planned session goes back to exactly what it was, and the new
      // one records what *it* was, so a later unlink restores the right thing.
      restoreLinkStatuses(link);
      link.planned_session_id = target.id;
      link.previous_planned_status = target.status;
      link.similarity = statedBreakdown(link.session_id, target.id).score;
      link.status = "confirmed";
      link.confirmed_at = MATCH_NOW;
      link.updated_at = MATCH_NOW;
      applyLinkStatuses(link);
      return response(200).json(matchRead(link));
    },
  ),
  http.delete("/api/v1/matches/{match_id}", ({ params, response }) => {
    const state = ingestState();
    const link = state.matches.find((row) => row.id === params.match_id);
    if (!link) {
      return response(404).json({
        detail: `Match ${params.match_id} not found`,
      });
    }
    restoreLinkStatuses(link);
    state.matches = state.matches.filter((row) => row.id !== link.id);
    const session = state.sessions.find((row) => row.id === link.session_id);
    return response(200).json({
      session_id: link.session_id,
      status: session?.status ?? link.previous_session_status,
      match: null,
    });
  }),
  http.post("/api/v1/sessions/{session_id}/rematch", ({ params, response }) => {
    const state = ingestState();
    const session = state.sessions.find((row) => row.id === params.session_id);
    if (!session) {
      return response(404).json({
        detail: `Session ${params.session_id} not found`,
      });
    }
    const candidates = state.planned.filter(
      (planned) =>
        Math.abs(dayDistance(planned.date, session.local_date)) <= 1 &&
        planned.discipline === session.discipline,
    ).length;
    const existing = linkForSession(session.id);
    // A link the athlete made is never revised by a re-run — that is what
    // makes "I already told you what this was" hold (WP-6.6).
    if (
      existing &&
      (existing.status === "confirmed" || existing.status === "displaced")
    ) {
      return response(200).json({
        session_id: session.id,
        status: session.status,
        match: matchSummary(existing),
        candidates,
        sticky: true,
      });
    }
    if (existing) {
      restoreLinkStatuses(existing);
      state.matches = state.matches.filter((row) => row.id !== existing.id);
    }
    const verdict = statedRematch(session.id);
    if (verdict === null || linkForPlanned(verdict.planned)) {
      session.status = "unplanned";
      return response(200).json({
        session_id: session.id,
        status: session.status,
        match: null,
        candidates,
        sticky: false,
      });
    }
    const link = linkRecord({
      sessionId: session.id,
      plannedSessionId: verdict.planned,
      status: verdict.status,
      createdBy: "system",
    });
    state.matches.unshift(link);
    applyLinkStatuses(link);
    return response(200).json({
      session_id: session.id,
      status: session.status,
      match: matchSummary(link),
      candidates,
      sticky: false,
    });
  }),
  http.post(
    "/api/v1/sessions/{session_id}/unplanned",
    ({ params, response }) => {
      const state = ingestState();
      const session = state.sessions.find(
        (row) => row.id === params.session_id,
      );
      if (!session) {
        return response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
      }
      const existing = linkForSession(session.id);
      if (
        existing &&
        (existing.status === "confirmed" || existing.status === "displaced")
      ) {
        return response(409).json({
          detail:
            `Session ${session.id} is linked to a planned session by your ` +
            "own confirmation; unlink it instead.",
        });
      }
      if (existing) {
        restoreLinkStatuses(existing);
        state.matches = state.matches.filter((row) => row.id !== existing.id);
      }
      session.status = "unplanned";
      return response(200).json({
        session_id: session.id,
        status: session.status,
        match: null,
      });
    },
  ),
  // The merge is an edit to the session and answers with it — recordings
  // re-parented, times widened, and the metrics recomputed over the joined
  // stream, which is why the route does it rather than the service.
  http.post(
    "/api/v1/sessions/{session_id}/merge",
    async ({ params, request, response }) => {
      const state = ingestState();
      const body = await request.json();
      const survivor = state.sessions.find(
        (row) => row.id === params.session_id,
      );
      const absorbed = state.sessions.find(
        (row) => row.id === body.absorbed_session_id,
      );
      if (!survivor) {
        return response(404).json({
          detail: `Session ${params.session_id} not found`,
        });
      }
      if (!absorbed) {
        return response(404).json({
          detail: `Session ${body.absorbed_session_id} not found`,
        });
      }
      if (survivor.id === absorbed.id) {
        return response(422).json({
          detail: "A session cannot be merged into itself",
        });
      }
      if (
        survivor.recordings.length === 0 ||
        absorbed.recordings.length === 0
      ) {
        return response(422).json({
          detail:
            "Merging joins two device recordings of one ride; a session " +
            "typed in by hand has no recording to merge.",
        });
      }
      if (survivor.discipline !== absorbed.discipline) {
        return response(422).json({
          detail:
            `A ${survivor.discipline} session and a ${absorbed.discipline} ` +
            "session are not two halves of one recording",
        });
      }
      const gap = Math.max(
        (Date.parse(absorbed.start_time) - Date.parse(survivor.end_time)) /
          1000,
        (Date.parse(survivor.start_time) - Date.parse(absorbed.end_time)) /
          1000,
      );
      if (gap > MAX_MERGE_GAP_S) {
        return response(422).json({
          detail:
            `These sessions are ${(gap / 3600).toFixed(1)} h apart, further ` +
            `than the ${MAX_MERGE_GAP_S / 3600} h a merge will bridge. ` +
            "Merging is for one ride recorded as two files, not for two rides.",
        });
      }
      survivor.recordings = [...survivor.recordings, ...absorbed.recordings];
      survivor.start_time =
        Date.parse(absorbed.start_time) < Date.parse(survivor.start_time)
          ? absorbed.start_time
          : survivor.start_time;
      survivor.end_time =
        Date.parse(absorbed.end_time) > Date.parse(survivor.end_time)
          ? absorbed.end_time
          : survivor.end_time;
      survivor.recording_time_s =
        (survivor.recording_time_s ?? 0) + (absorbed.recording_time_s ?? 0);
      survivor.duration_s = survivor.recording_time_s;
      survivor.metrics = {
        ...RIDE_METRICS,
        version: (survivor.metrics?.version ?? 0) + 1,
        computed_at: MATCH_NOW,
        recompute_reason: "recordings merged into one session",
      };
      survivor.load = survivor.metrics.load.training_load ?? null;
      survivor.load_basis = survivor.metrics.load.load_basis ?? null;
      survivor.updated_at = MATCH_NOW;
      state.sessions = state.sessions.filter((row) => row.id !== absorbed.id);
      return response(200).json(withMatch(survivor));
    },
  ),

  // --- scores, alignment and the athlete's verdict (WP-7) --------------------

  http.get("/api/v1/sessions/{session_id}/score", ({ params, response }) => {
    const record = scoringFor(params.session_id);
    if (!record) {
      return response(404).json({ detail: NOT_SCORED });
    }
    return response(200).json(scoreRead(record));
  }),

  http.get(
    "/api/v1/sessions/{session_id}/alignment",
    ({ params, response }) => {
      const record = scoringFor(params.session_id);
      if (!record) {
        return response(404).json({ detail: NOT_ALIGNED });
      }
      return response(200).json(alignmentRead(record));
    },
  ),

  // The offset lands, and both versions move with it. Answering with the
  // alignment it already had would let a component that sent the wrong offset
  // — or none — still pass, and the whole point of the control is that the
  // number it sends changes which effort answers which step.
  http.put(
    "/api/v1/sessions/{session_id}/alignment",
    async ({ params, request, response }) => {
      const record = scoringFor(params.session_id);
      if (!record) {
        return response(404).json({ detail: NOT_ALIGNED });
      }
      const body = await request.json();
      if (Math.abs(body.offset_s) > MAX_ALIGNMENT_OFFSET_S) {
        return response(422).json({
          detail:
            `An alignment offset of ${body.offset_s} s is further than the ` +
            `${MAX_ALIGNMENT_OFFSET_S} s a correction can plausibly be.`,
        });
      }
      // Throws for an offset nothing was generated for, rather than answering
      // with a pairing no `align` produced.
      statedScoring(
        record.session_id,
        record.planned_session_id,
        body.offset_s,
      );
      record.offset_s = body.offset_s;
      record.alignment_version += 1;
      record.score_version += 1;
      return response(200).json(alignmentRead(record));
    },
  ),

  http.get("/api/v1/sessions/{session_id}/verdict", ({ params, response }) => {
    const record = scoringFor(params.session_id);
    const declared = record ? declarationRead(record) : null;
    if (!record || !declared) {
      return response(404).json({
        detail:
          `Session ${params.session_id} has no declared verdict yet. The ` +
          "suggested one is on its score.",
      });
    }
    return response(200).json(declared);
  }),

  // Echoes the verdict and the reasons it was sent, and applies the server's
  // own rule to them: a canned reply could not fail when the form drops a
  // reason, and a handler that accepted four could not fail when the picker
  // stops counting.
  http.put(
    "/api/v1/sessions/{session_id}/verdict",
    async ({ params, request, response }) => {
      const record = scoringFor(params.session_id);
      if (!record) {
        return response(404).json({ detail: NOT_SCORED });
      }
      const body = await request.json();
      const refusal = reasonsRefusal(body.verdict, body.reasons ?? []);
      if (refusal) {
        return response(422).json({ detail: refusal });
      }
      const reasons = body.reasons ?? [];
      const note = body.note ?? null;
      record.declaration = {
        declared_verdict: body.verdict,
        declared_at: SCORING_NOW,
        suggested_at_declaration: scoreRead(record).suggested_verdict,
        // Declaring again is the athlete ruling on the machine's current
        // opinion, so it clears the flag — exactly as `declare` does.
        contested: false,
        contested_at: null,
        contested_verdict: null,
        // An `as_intended` declaration carrying nothing appends no version: a
        // reasons chain whose tip says nothing is indistinguishable from
        // silence.
        reasons:
          reasons.length > 0 || note !== null
            ? [
                {
                  version: 1,
                  recorded_at: SCORING_NOW,
                  revision_reason: null,
                  reasons: [...reasons],
                  note,
                  recorded_by: "athlete",
                },
              ]
            : [],
      };
      const declared = declarationRead(record);
      if (!declared) {
        throw new Error("a declaration was just written and is not there");
      }
      return response(200).json(declared);
    },
  ),

  // Append-only: a revision is version n+1, and what was said before stays
  // readable.
  http.put(
    "/api/v1/sessions/{session_id}/verdict/reasons",
    async ({ params, request, response }) => {
      const record = scoringFor(params.session_id);
      if (!record?.declaration) {
        return response(404).json({
          detail: `Session ${params.session_id} has no declared verdict yet.`,
        });
      }
      const body = await request.json();
      const refusal = reasonsRefusal(
        record.declaration.declared_verdict,
        body.reasons,
      );
      if (refusal) {
        return response(422).json({ detail: refusal });
      }
      const chain = record.declaration.reasons;
      const appended = {
        version: chain.length + 1,
        recorded_at: SCORING_NOW,
        revision_reason: body.revision_reason ?? null,
        reasons: [...body.reasons],
        note: body.note ?? null,
        recorded_by: "athlete",
      };
      chain.push(appended);
      return response(200).json(reasonsRead(appended));
    },
  ),

  // --- WP-8: the coach ------------------------------------------------------

  // Newest first, filtered by status the way the endpoint is — the inbox asks
  // for `pending` and must get *only* pending back, or a filter that sent the
  // wrong parameter would still look right.
  http.get("/api/v1/proposals", ({ query, response }) => {
    const status = query.get("status");
    const offset = Number(query.get("offset") ?? 0);
    const limit = Number(query.get("limit") ?? 25);
    const matching = proposalList().filter(
      (proposal) => status === null || proposal.status === status,
    );
    return response(200).json({
      items: matching.slice(offset, offset + limit),
      total: matching.length,
      offset,
      limit,
    });
  }),
  http.get("/api/v1/proposals/{proposal_id}", ({ params, response }) => {
    const proposal = proposalById(params.proposal_id);
    return proposal
      ? response(200).json(proposal)
      : response(404).json({ detail: "No such proposal." });
  }),
  // Accept moves the proposal *and* leaves it moved: a second accept is the
  // 409 the API gives, because the first one already resolved it.
  http.post(
    "/api/v1/proposals/{proposal_id}/accept",
    ({ params, response }) => {
      const proposal = proposalById(params.proposal_id);
      if (!proposal) {
        return response(404).json({ detail: "No such proposal." });
      }
      if (proposal.status !== "pending") {
        return response(409).json({
          detail: `This proposal is ${proposal.status} and cannot be accepted.`,
        });
      }
      const applied = updateProposal(params.proposal_id, {
        status: "accepted",
        resolved_at: AGENT_NOW,
      });
      if (!applied) {
        throw new Error("a proposal that was just found is not there");
      }
      return response(200).json(applied);
    },
  ),
  // The reason is echoed into the resolution note, which is where the API puts
  // it — a handler that dropped it would let a form that never sent one pass.
  http.post(
    "/api/v1/proposals/{proposal_id}/reject",
    async ({ params, request, response }) => {
      const proposal = proposalById(params.proposal_id);
      if (!proposal) {
        return response(404).json({ detail: "No such proposal." });
      }
      if (proposal.status !== "pending") {
        return response(409).json({
          detail: `This proposal is ${proposal.status} and cannot be rejected.`,
        });
      }
      const body = await request.json();
      const rejected = updateProposal(params.proposal_id, {
        status: "rejected",
        resolved_at: AGENT_NOW,
        resolution_note: body.reason ?? null,
      });
      if (!rejected) {
        throw new Error("a proposal that was just found is not there");
      }
      return response(200).json(rejected);
    },
  ),

  // Exactly one subject, or the request has no answer — the endpoint's own
  // rule, and the reason it is a 422 rather than an empty list.
  http.get("/api/v1/agent-notes", ({ query, response }) => {
    const sessionId = query.get("session_id");
    const week = query.get("week");
    if ((sessionId === null) === (week === null)) {
      return response(422).json({
        detail: "Give exactly one of session_id and week.",
      });
    }
    return response(200).json({
      items: noteList().filter((note) =>
        sessionId === null
          ? note.plan_week === week
          : note.session_id === sessionId,
      ),
    });
  }),
  http.post(
    "/api/v1/agent-notes/{note_id}/dispute",
    async ({ params, request, response }) => {
      const body = await request.json();
      const rated = rateNote(params.note_id, body.rating ?? null, AGENT_NOW);
      return rated
        ? response(200).json(rated)
        : response(404).json({ detail: "No such note." });
    },
  ),

  // --- wellness -------------------------------------------------------------
  // Stateful, because the form's contract is which fields it sends: a canned
  // day could not fail when the form drops one.
  http.get("/api/v1/wellness/inputs", ({ response }) =>
    response(200).json(wellnessInputs()),
  ),
  http.get("/api/v1/wellness/days", ({ query, response }) =>
    response(200).json(
      wellnessRange(query.get("start") ?? "", query.get("end") ?? ""),
    ),
  ),
  http.get("/api/v1/wellness/days/{local_date}", ({ params, response }) => {
    const day = wellnessDay(params.local_date);
    return day
      ? response(200).json(day)
      : response(404).json({
          detail: `No wellness was recorded for ${params.local_date}`,
        });
  }),
  http.patch(
    "/api/v1/wellness/days/{local_date}",
    async ({ params, request, response }) => {
      const result = patchWellnessDay(params.local_date, await request.json());
      return "detail" in result
        ? response(422).json({ detail: result.detail })
        : response(200).json(result.day);
    },
  ),
  http.get("/api/v1/wellness/trend", ({ query, response }) =>
    response(200).json(
      wellnessTrend(
        query.get("start") ?? "",
        query.get("end") ?? "",
        query.getAll("metric"),
      ),
    ),
  ),
  http.get("/api/v1/wellness/weight", ({ query, response }) => {
    const on = query.get("on") ?? "";
    const resolved = wellnessWeightInForce(on);
    return resolved
      ? response(200).json(resolved)
      : response(404).json({
          detail: `No weight was recorded on or before ${on}`,
        });
  }),
];

/** `app.services.scoring.MAX_ALIGNMENT_OFFSET_S` — six hours, in seconds. */
const MAX_ALIGNMENT_OFFSET_S = 6 * 60 * 60;

/** `app.api.routes.scoring.get_session_score`'s own 404 sentence. */
const NOT_SCORED =
  "This session has no score. A session is scored once it is linked to a " +
  "planned session and that link is settled; a pending proposal is a " +
  "question, not a link. A session that was scored and then unlinked keeps " +
  "its versions on its score history, but no longer answers to a " +
  "prescription.";

/** And `get_session_alignment`'s. */
const NOT_ALIGNED =
  "This session has no alignment. Only a session linked to an endurance " +
  "prescription is aligned — a strength session's sets are paired by " +
  "position, not on a timeline, and an unlinked session has no prescription " +
  "to pair against.";

/** `app.services.matching.MAX_MERGE_GAP_S` — six hours, in seconds. */
const MAX_MERGE_GAP_S = 6 * 60 * 60;

/** Whole days between two ISO dates, for the candidate window. */
function dayDistance(left: string, right: string): number {
  return Math.round(
    (Date.parse(`${left}T00:00:00Z`) - Date.parse(`${right}T00:00:00Z`)) /
      86_400_000,
  );
}

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

/** The verdicts `IngestService.reject` accepts; every other one is a 409. */
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
