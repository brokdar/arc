# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Metric accuracy against the head unit (D197–D203)

Three numbers computed differently from the athlete's own device, checked
against one real Wahoo ELEMNT BOLT V2 ride and the same ride on Strava and
intervals.icu.

- **Distance comes from the device's odometer.** FIT and TCX carry a
  cumulative distance field — the head unit's own, integrated internally far
  faster than the once-a-second speed it writes out. It is now a stream channel
  of its own, and `distance_km` differences it end to end: 40.95 km on the
  reference ride, matching the device, Strava and intervals.icu, where
  integrating the speed column gave 40.32. It is differenced **per recording**
  and summed, because every device counts from its own zero and a merged
  session lays several on one grid (D202). Files with no odometer, an odometer
  that resets, one that covers less than 90 % of its recording, or a stream
  stored before this existed fall back to integrating speed — per recording —
  and say so on the number. Average speed keeps its definition — the whole
  ride's distance over moving time — and now states that asymmetry, in the
  direction that is actually true of the divisor it got.
- **Average cadence excludes coasting.** 356 freewheeling seconds were dragging
  the reference ride's 82.8 rpm down to 77.7. The zero share travels with the
  number, so the exclusion is visible rather than assumed.
- **Elevation gain smooths before it thresholds.** The altitude trace is
  averaged over a centred 15 s window — reflected at the two ends, so a ride
  that starts or finishes mid-climb keeps those metres (D203) — and climbs are
  banked whole once they clear 3 m (GoldenCheetah's default). 82.5 m on the
  reference ride, between the device's 78 and intervals.icu's 84, where the old
  2 m band counted barometric noise up to 88.6.
- **`just rebuild-streams`** re-parses the original files, rewrites their
  stream stores and recording rows, and recomputes the metrics of every session
  that changed. Recomputation alone reads the stored parquet, so an
  already-ingested session can only gain a new channel this way. Originals are
  read-only; the regeneration is audited. Deploy the new image first and
  rebuild second: an older image cannot read a channel it predates, so a
  rollback after a rebuild is an outage until it is undone.

### Settings — the anchors page (D193)

The anchors API has been complete since WP-1 with no screen behind it: until
now the athlete could not enter an FTP through the UI at all. `/settings` is
that screen, and the Settings nav row is live.

- **In force now** — every writable anchor (FTP, LTHR, max HR, resting HR) with
  its value, provenance, effective date, confidence interval and protocol, in
  fixed slots. One that has never been entered keeps its place and carries the
  button that fills it.
- **Append a version** — anchor, value, provenance, protocol (required in front
  of the athlete when the provenance is `tested`), an effective date that
  defaults to today and may be back-dated, and optional CI bounds. Everything
  else the domain refuses is printed as the sentence the service sent.
- **History** — every version ever appended, newest first, filtered through the
  API's own `anchor_type` parameter and paged. A version dated ahead of today
  is marked "not in force yet" (D195), so the row that sorts to the top never
  reads as the current value. There is no edit and no delete anywhere: the API
  answers 405 by design, and the page says so.
- **Zones in force** — the Coggan 7 power zones and the 5-zone heart-rate
  scheme, computed by the API from the anchors in force and never in the
  client; when there is no anchor, the panel names the one it is waiting for.
- **Profile and illness** — name, date of birth, sex and height, and the
  illness/injury flag, which mounts Today's control rather than a second form.
  A failed background refetch keeps the fields — and whatever was typed into
  them — and says the values are the ones that last loaded.

The session log and the proposal inbox page through the same `Pager` the anchor
history and the ingest queue use: one range, one pair of buttons named for what
they page, and each page's filter on the same line as its range.

### WP-8 — agent layer: MCP tools, proposals, guardrails

The coaching agent can now act — through thirteen MCP tools that delegate to
the same services the API uses, with every write guarded in the service layer,
never in the adapter. Decisions D166–D183.

**Plan-change proposals (`plan_proposals`, migration `0009`)**

- `propose_plan_change` records rationale, the changes as a tagged union
  (create/update/move/delete — `status` is not proposable), the computed
  per-entity diff, and an expiry. Accepting applies every change in **one**
  transaction through `PlannedSessionService`'s new `stage_*` verbs; rejecting
  takes a free-text reason; expiry lapses the proposal and the committed plan
  stands. A new proposal about a session with an open one supersedes it
  (linked both ways); a recorded activity on a proposed day and discipline
  resolves it by reality, from ingest and manual entry alike.
- Guardrails, all in services: a dry-run flag on every write (returns the
  diff, writes nothing — not even audit); optimistic concurrency on the
  intent-chain version, re-checked at accept (stale → 409, proposal stays
  pending); a trailing-hour agent write cap (`MCP__WRITE_CAP_PER_HOUR`,
  default 60) counted over the audit log; and the red-flag rule — while the
  athlete's illness/injury flag is up, proposals that add or intensify are
  refused with the reason, and every read carries the flag so the agent can
  never claim ignorance.

**MCP tool surface (`app/mcp/tools.py`)**

- Reads: `get_athlete`, `get_anchors`, `get_plan_week`, `get_session_detail`,
  `list_sessions`, `get_workout_library`, `search_history` (honest partial
  weeks, refuses rather than truncates past its caps). Writes: `append_anchor`
  (provenance required, `tested` demands a protocol), `create_workout`,
  `propose_plan_change`, `write_session_evaluation`, `annotate`. No tool
  touches recordings, streams, declared verdicts or reasons — the surface is
  pinned by an exhaustive test. Refusals name the offending argument;
  `docs/agent-setup.md` shows how to connect Claude.

**Coach notes and the UI**

- `agent_notes` (migration `0010`): interpretive text attributed to a model
  and key label, citing artefact ids, one subject (session or plan week),
  with an athlete-only 👍/👎 dispute. Rendered strictly in the reserved coach
  purple on session detail and the calendar week.
- New `/proposals` inbox: per-change diff view, accept/reject with reason, a
  409 rendered as "the plan moved underneath this proposal", and a pending
  count on the nav. The red flag gets a shell-wide banner and a control on
  Today (severity required while active; lowering clears note and severity).

A matched session is now scored against its frozen intent, given a suggested
verdict the athlete rules on, and asked for reasons when it deviated.
Decisions D149–D158.

**The axes (`backend/app/domain/scoring.py`)**

- Five computed axes — `completion`, `adherence` (time-in-band per aligned
  work step, criterion-weighted, each band judged through its **frozen**
  `smoothing_s`), `discipline` (ceilings), `pacing` (last-rep NP over
  first-rep NP across repeat blocks), `sets_load` (strength) — plus
  `response`/`fuelling` as `not_assessed(deferred)`. Every axis returns a
  score in [0, 1] or `not_assessed(reason)` with per-criterion pass/fail
  detail and a `MetricExplanation`; scoring is total and never raises out of
  the ingest path. Steps the alignment excluded are reported as
  `alignment_low_confidence`, not scored as violations.
- The verdict table is nine ordered, documented rows (displaced →
  `different_session`; completion < 0.5 → `abandoned`; execution ≥ 0.8 with
  ceilings held → `as_intended`; off-target by side → `over`/`under`; …) —
  deterministic, exhaustively tested, and only ever a suggestion.

**Testimony and versions (persistence, services)**

- Scores and alignments are versioned artefacts pinning anchor versions,
  intent version and alignment version; rescores (post-hoc intent edit through
  the WP-2 seam, manual recompute, offset change) append version n+1, never
  overwrite (migration `0008_scoring`).
- The verdict declaration is athlete-only — the service refuses any other
  actor (403) — and is never auto-rewritten: a rescore whose new suggestion
  contradicts both the declaration and what was suggested at declaration time
  sets `contested` (D150); re-declaring clears it. Reasons (1–3, ordered by
  primacy, controlled list + free text) are append-only revisions. Evening
  prompts expire after 72 h into the auto-reason `not_provided` via a
  scheduler job.
- Alignment's time offset (A7.1) is functional: `PUT …/alignment` re-pairs
  steps to efforts and rescores in one transaction.

**The surfaces**

- Session detail gains the judgement panel (axis grid with explanations,
  suggested verdict + rationale, one-tap confirm, override + reason picker,
  contested banner) and the alignment panel with the offset control.
- The calendar week now carries a `completion_state` per session and day
  (`as_intended`/`under`/`over`/`abandoned`/`different_session`/`missed`/
  `displaced`/`unplanned`/…), rendered as day strips and card badges from
  shared `--color-status-*` tokens; `displaced` moved off the purple reserved
  for the coach and the over verdict (A3.4).

### WP-6 — matching engine

A completed session now finds the plan entry it answers — as a proposal, never
a silent commitment. Decisions D138–D147.

**The similarity score (`backend/app/domain/matching.py`)**

- Three components with the build plan's constant weights — duration 0.4,
  intensity 0.3, structure 0.3 — each a symmetric `min/max` agreement ratio.
  Intensity compares recorded NP (or average HR as the stated fallback) against
  the prescription's **frozen** pins via `predict_endurance_load`, never
  against today's anchors (invariant 4). Structure counts detected work
  intervals against prescribed work steps — or logged sets against prescribed
  sets for strength, whose structure is its set list (D139).
- **A component with no inputs is renormalised away, never defaulted** (D138):
  1.0 invents agreement, 0.0 invents disagreement, so the remaining weights
  rescale and the stored breakdown carries both what was and was not assessed,
  with reasons. Nothing assessable at all scores `None` and becomes a question
  for the athlete, not a refusal. One prescribed work unit is not a structure
  (D139), so a steady ride is not punished for detecting no intervals.

**The lifecycle (link table, service, sweep)**

- Similarity ≥ 0.75 auto-links (`auto_high`, revocable); 0.4–0.75 raises a
  `pending` proposal that **moves neither side's status until answered**
  (D140); below 0.4 the activity stands `unplanned`. Candidates are same
  discipline within ±1 day, athlete-local dates.
- Links live in a link table, not an FK — the one-to-one uniques are exactly
  what a set-to-set increment later drops (D141). Manual operations always
  available: link, link-as-`displaced` (executed-instead-of; the planned
  session is neither missed nor completed), unlink, swap, mark-unplanned, and
  merge of two device recordings into one session over one joined 1 Hz grid
  (D143). Confirmed and displaced links are **sticky**: re-matching never
  touches them, and automatic matching runs exactly once per session — a
  re-match is an explicit override (D142). Unlink restores both sides to
  exactly the statuses the link recorded; history lives in `audit_log`, and
  every mutation writes an audit row.
- An hourly idempotent sweep marks a planned session `missed` at the end of
  day + 1 in the athlete's timezone (`MATCHING__TIMEZONE`, D144) and raises the
  evening-prompt record WP-7 consumes; a paused plan is never swept.

**The surface (`frontend/components/sessions/match-panel.tsx`)**

- The session page shows the link with its full breakdown — per-component
  score, weight, prescribed and recorded values, and the unassessed components
  in their fixed slots with the domain's reason — plus confirm/reject on a
  proposal, the swap and manual-link pickers with both link kinds explained in
  plain language, a standing "record it as done instead" offer on a
  low-similarity confirmed link (D146), and a merge dialog that states what
  merging does. Session list and calendar cards carry the match state; a
  pending proposal is a visible "Proposed", not a premature "Completed".
- Mock similarities are generated by the backend domain over the same fixtures
  (`just matching-fixture`), so a hand-typed breakdown that the domain cannot
  produce fails the build instead of passing the tests.

### WP-5 — metrics, session analysis and stream charts

A recorded session now has numbers, and every one of them says where it came
from. Decisions D112–D133.

**The metric set (`backend/app/domain/`)**

- The rest of the **Coggan chain** beside the NP/IF/TSS WP-3 already shared
  with the planned side: average power as *total work over recording time*
  (Appendix A.1's convention, and deliberately not the average a head unit
  shows), variability index, efficiency factor, work, work above FTP, coasting
  (moving ≥ 1 km/h at ≤ 10 W, display only), per-channel average and maximum,
  and elevation gain against a 2 m hysteresis band (D120). The duration term in
  training load is **recording time** — elapsed minus every stop over 30 s
  (A5.1) — stated at the call site, in the docstring, and in the number's own
  explanation.
- **HRSS by per-sample integration** (A5.3), not the widely copied
  single-average form: by Jensen's inequality that one systematically
  under-reports exactly the variable-intensity sessions HR load exists for. A
  square-wave HR series and a constant one with the same mean produce different
  numbers, and the square wave is higher. Every guard names the input it is
  missing. Resting heart rate becomes an **anchor** for it — with provenance,
  an effective date and append-only history — rather than a profile field
  (D114).
- **Both load models are computed and both are stored**, with the one selected,
  the rule that chose it, and enough to render A5.2's counterfactual (*"Load
  79, from power. Had power been unavailable, the heart-rate model would have
  given 75."*). Storing only the winner throws the comparison away permanently,
  and the comparison is the only way to learn whether an HR-only day can be
  trusted.
- **Time in zone** per channel with the three-zone collapse and Treff's
  polarization index (A5.4). A degenerate split is `not_assessed`, not `-inf`.
  The five-zone HR model collapses Z1–2 / Z3 / Z4–5, which is where the
  boundary physiologically sits rather than where the integers line up (D121).
- **Interval detection and structure alignment** (`app/domain/alignment.py`):
  threshold crossing over a 10 s centred smoothing, then an order-preserving
  dynamic-programming assignment of detected intervals to planned work steps.
  Confidence is duration and intensity agreement; pairs below 0.5 are excluded
  with `alignment_low_confidence`. `offset_s` is a **real input** from day one
  (A7.1): it moves the assignment through a proximity term while confidence
  keeps measuring agreement alone (D123). Nothing persists it yet — an
  alignment describes a match, and matches arrive with WP-6 (D116).
- `NotAssessed(reason)` is the shape a metric answers with when it cannot: the
  reason, never `None` and never a zero standing in for a missing channel.
  WP-7's scoring axes will reuse it.

**The artefact (`session_metrics`, migration 0006)**

- One session's numbers are a **versioned artefact**. A recomputation appends
  version *n+1* and supersedes *n*; nothing is updated in place, and the old
  version stays readable with the pins it was computed against (invariant 1).
  Appending a new FTP and recomputing changes the new version's pin and leaves
  every earlier one exactly as it was.
- The **pins are columns** — FTP, LTHR, max HR, resting HR, plus the zone model
  per channel (A5.5) — because "recompute everything that used this FTP
  version" cannot be a JSON scan. The numbers are one JSON payload, each value
  beside its rendered explanation and each absence beside its reason.
- Metrics are computed **after** the ingest transaction commits, per session,
  inside a `try` (D125): a metric failure leaves an ingested ride with no
  numbers rather than un-ingesting the file, and the file is the irreplaceable
  half. A manual strength session takes the stream-free path on create and on
  correction.

**API**

- `GET /api/v1/sessions/{id}/streams` — the chart payload, its own resource
  because it is 1–2 MB for a long ride: per-channel cleaned columns with their
  nulls intact, the recording stops, the repaired regions, per-channel sources.
  404 with a reason-naming detail for a session that was typed in by hand.
- `POST /api/v1/sessions/{id}/metrics/recompute` — audited, returns the new
  version.
- Session detail carries `metrics`; list rows carry `load` and `load_basis`.
  The plan week carries **completed** duration and load per day and per
  discipline with their own coverage pairs, plus a weekly polarization index
  counting exactly one channel per session and stating which rule chose it
  (A5.4, D127). Planned and completed stay in separate columns and are never
  summed.

**The session analysis page (`/sessions/{id}`)**

- The header metric row, the stacked **uPlot** stream charts (power, HR,
  cadence, elevation) with a synced cursor, zoom, drag-selection statistics, an
  FTP reference line from the artefact's own pin, and recording stops drawn as
  breaks rather than as zero watts. uPlot is the only chart dependency added —
  the zone bar is SVG from the shared ramp (D113).
- The detected-intervals table, the strength card for a session with no stream,
  and a "not computed yet" state that offers the action rather than describing
  the absence.
- Every number renders its explanation through **one** shared affordance, and
  every absent number renders `NotAssessed` with its reason in the slot it
  would have occupied. The planned-band overlay on the power chart is a prop
  that renders nothing until WP-6 fills it.
- The frontend's metric and stream fixtures are **generated** by running the
  real domain over a synthetic stream (`just metrics-fixture`, D132), so the
  numbers in a test agree with the trace beneath them.

### WP-4 — ingestion: watched folder, FIT parsing, sessions & streams

Device files become sessions. Decisions D89–D100.

**Streams and the session model (`backend/app/domain/`, `app/persistence/`)**

- Every stream is resampled to a uniform **1 Hz grid** before it is stored, and
  row `i` of every column describes the same instant (A4.1). Row index — not
  timestamp — is the addressing unit for everything downstream. A recording
  pause longer than 30 s is a hole: the grid continues, every channel is
  **null** across it, and the range is reported so its duration can be
  subtracted from `recording_time_s`, which is the duration training load is
  computed over (A4.4, A5.1). Sub-threshold gaps are filled per channel —
  latitude, longitude and elevation interpolate; power, HR, cadence, speed and
  temperature hold.
- Raw and cleaned columns are **both** stored, and every repair is recorded
  (A4.2). The raw column keeps the spike from a dropped magnet, a
  parallel `*_fixed` column is what analysis reads, and each substituted region
  becomes a `stream_anomalies` row naming the rows, the kind of repair and the
  value put there. A `_fixed` column never holds a value outside the channel's
  plausible range. A channel that needed nothing stores a `resampled_only` row,
  so "clean" is distinguishable from "not checked".
- Systemic garbage is **quarantined, not repaired**: no samples, more than
  10 % of samples repeating a timestamp (a handful collapse by last-wins,
  D91), under two minutes of elapsed *or recorded* time, or more than 10 % of
  a channel implausible.
- New tables: `sessions`, `recordings`, `stream_anomalies`,
  `quarantine_records`, `ingest_events`, `logged_sets`. The **dedup key is
  `(file_hash, file_sport_index)`** — one file may hold more than one sport
  (A4.5). A session stores its athlete-local timezone (IANA name, `UTC+02:00`,
  or `UTC`) and the `local_date` derived from it, so a midnight-crosser belongs
  to the day it began (D93).

**Ingestion (`backend/app/ingest/`)**

- Parsers for **FIT** (Garmin SDK, falling back to `fitdecode` on a file the
  strict reader gives up on), **GPX** (`gpxpy`, including the Garmin
  TrackPointExtension sensors) and **TCX** (`tcxreader`). Each file yields one
  activity per sport. Power and HR **source candidates are enumerated** from
  FIT `device_info` and the rule that chose one is recorded verbatim, including
  when it is only a tie-break (A4.3, D96).
- A per-file pipeline: sha256 → dedup by hash against ingested recordings *and*
  unresolved quarantine → parse → validate → overlap dedup (>70 % of the longer
  range, D98) → file the original under `data/originals/YYYY/MM/<hash>.<ext>` →
  session and recording rows → resample, clean, and write
  `data/streams/<recording_id>.parquet`. Re-seeing a file is a `duplicate_file`
  log line, never a second session. **Nothing under `data/originals/` is ever
  deleted**, and any unanticipated failure still ends with the file kept and a
  record saying what happened.
- A **watched folder**: an APScheduler job sweeps `data/inbox/` every 30 s,
  skipping dotfiles and files that are still arriving (recently modified, or
  changed size since the last sweep). Configured by `INGEST__SCAN_INTERVAL_SECONDS`,
  `INGEST__SETTLE_SECONDS` and `INGEST__OVERLAP_THRESHOLD`.

**API (`backend/app/api/`)**

- `POST /api/v1/ingest/upload` (multipart) writes into the inbox and runs the
  pipeline synchronously, answering with the outcome: sessions created,
  quarantine records raised, or the sessions the file already existed as. A
  file it cannot use is a **200 with a reason**, not an error (D97).
- `GET /api/v1/ingest/quarantine` (pending first) and
  `POST /api/v1/ingest/quarantine/{id}/confirm` | `/reject`. Confirm discards
  the quarantined copy and never an original; reject overrules the verdict it
  disagrees with (D107): a suspected duplicate re-ingests as its own session,
  an implausible-channel refusal ingests with the broken channel nulled and
  its anomalies recorded. Rejecting anything else is a 409 — a corrupt
  file has nothing safe to ingest. `GET /api/v1/ingest/events` is the
  append-only log of every file the pipeline looked at.
- `GET /api/v1/sessions?start=&end=&discipline=` (paginated, newest first) and
  `GET /api/v1/sessions/{id}` — the session plus its recordings' metadata:
  sources, stops, sample regularity and the repair count. **Not** the streams;
  those are WP-5's. `PATCH /api/v1/sessions/{id}` corrects the discipline
  (recorded as an override) or the timezone (which re-derives `local_date`).
- `POST /api/v1/manual-sessions` records a session performed without a device
  file — a gym session — with its logged sets (D99).

**Frontend (`frontend/`)**

- **`/inbox`** — the queue of everything the watched folder could not decide on
  its own. Each row names the verdict in English, repeats the API's own detail,
  says what to do about it, and links a suspected duplicate to the session it
  looks like. "Discard this copy" takes two clicks; "Not a duplicate" is
  offered only where there is something safe to ingest, which is the one reason
  the API accepts a reject. Below the queue, the paginated ingest log. An
  upload control posts a file and reports the outcome — a quarantined file
  included, because that is a 200 with a reason and the page branches on the
  outcome rather than the status (D97, D100).
- **`/sessions`** — the log, newest first, filtered by discipline on the
  server. Each row: local date, discipline, duration, recording kind, the
  match badge (taken as a prop, not assumed), and a **load column that holds
  its position** with a placeholder naming WP-5 as the reason it is empty.
- **`/sessions/{id}`** — the session's metadata and the recording's account of
  it: start and end in the session's own timezone, elapsed against recording
  time with the paused total derived from the stop ranges, sample regularity,
  repair count, and which meter produced each channel *with the rule that chose
  it*. Logged sets for a hand-entered session. Two small correction forms for
  the discipline and the timezone, the second re-deriving the session's date.
  Every absent value keeps its slot and says why it is absent.
- The sidebar gains **Sessions** and **Inbox**; eight sections now, and only
  three of them still dimmed (D100).

**Dependencies**

- Added `garmin-fit-sdk`, `fitdecode`, `gpxpy`, `tcxreader` and `pyarrow`, all
  forbidden in `app/domain` (D95), plus the dev-only `fit-tool` that builds the
  committed synthetic golden FIT files (D94).

### WP-3 — calendar & plan API, design system, week UI

The plan becomes something you can look at, rearrange, and fill. Decisions
D55–D88.

**API (`backend/app/`)**

- Added `GET /api/v1/plan/week?start=` — seven day objects, empty ones
  included, each carrying flat session cards (discipline, purpose, status,
  title, planned duration, step count, sets, the one-line intent and its
  version). `start` is taken literally so a client can page by a day; omitted,
  it defaults to the Monday of the current week (D55). Everything a card cannot
  show stays behind `GET /planned-sessions/{id}`.
- `WeekSessionRead` carries `predicted_load_coverage` beside its
  `predicted_load` — the same fraction `PredictedLoadRead.coverage` reports on
  the session itself, passed through from the one prediction rather than
  recomputed, and null exactly when the load is (D88). Without it a client
  rendering card-level loads cannot tell a fully covered prediction from a
  40 %-covered one, which is the rule D78 set for week totals applied one level
  down. Nothing renders it yet; card-level load arrives with WP-5's week strip.
- Added `POST /planned-sessions/{id}/move` and `/copy`: the calendar's two
  gestures get their own verbs and their own audit actions rather than being
  folded into the PATCH that can already change a date (D56). A copy is a *new*
  session planned now — fresh intent chain, anchors pinned at what is in force
  today, criteria carried over as they stand (D57).
- Added `athlete.plan_state` (`active | paused`), read and written through the
  existing athlete endpoints rather than a plan table and two verbs (D58).
  Paused means missed-session marking stops; ingestion and scoring carry on.
- Success criteria declare their own **smoothing window**: `Band.smoothing_s`
  (default 30 s) and `Ceiling.smoothing_s` (default 0 — raw) say how long a
  trailing rolling mean is applied before the channel is compared (D73). The
  window freezes with the intent instead of living in WP-7's scorer, so a
  scoring change cannot rewrite what an already-scored session was judged
  against. Every band and ceiling in `purpose_templates.json` states its own
  (30 s for steady work, 10 s for VO₂max, 3 s for anaerobic; ceilings raw),
  and a test refuses a template that relies on the default. Criteria are
  tagged-union JSON and the decoder tolerates the key being absent, so no
  migration was needed.
- `GET /plan/week` now carries **predicted load and its coverage**: per card
  `predicted_load`, `predicted_intensity_factor` and `predicted_volume_load_kg`;
  per week `planned_load` (null, never 0, when nothing is predictable),
  `load_sessions_counted` / `load_sessions_uncounted`, and a `by_discipline`
  row (sessions, duration, load, sets). TSS and kilograms stay in separate
  columns and are never summed. Everything is computed on read from the frozen
  intent and its pins — no column, no migration, no cache (D72) — with the
  week's pins loaded in one query. The prediction resolves against
  `PinnedAnchor` pairs — an anchor version together with the id it was pinned
  by — rather than bare versions, so the number can name what it resolved
  against without an id being pushed onto the domain's id-free
  `AnchorVersion` (D64); and it refuses to expand a prescription longer than
  a day (`MAX_PREDICTABLE_DURATION_S`), because the workout model bounds steps
  and step counts but not their product, and a legal tree can describe 43
  million seconds of 1 Hz series on a read path (D65).
- **Every week total says what it covers, and a total nothing contributed to
  is null.** `planned_duration_s` is nullable — week-wide and per discipline —
  and travels with `duration_sessions_counted` / `duration_sessions_uncounted`,
  the way `planned_load` already travelled with its own pair; each
  `by_discipline` row now carries **both** pairs, so a row explains its own
  missing number instead of leaving a client to invent a reason (D78). A week
  of two distance rides and a lift used to total `0` seconds and read as rest.
  `session_count` reports the repository's true total, and any session past the
  `MAX_WEEK_SESSIONS` render cap counts as uncounted on both axes rather than
  vanishing from a total that then claims to be whole.
- A planned session now **resolves its own pins**. `GET /planned-sessions/{id}`
  and every write route add `resolved_steps` (each flattened step's targets
  said both ways: `88–93 % FTP` *and* 220–232.5 W, with the anchor version that
  resolved them), `pinned_anchors` (type, version id, value, unit, provenance,
  effective date), `predicted_load` with a `MetricExplanation` — formula,
  inputs naming the *version's* value and provenance, assumptions, citation
  (D74) — and `predicted_volume` for a strength session (volume load in
  kilograms, total sets, coverage), the other axis, never summed with the
  first. Appending a new FTP anchor changes nothing on a session already
  planned, which is invariant 4 finally made visible.
- **`GET /planned-sessions` answers with a lighter row** than the session it
  names: no `resolved_steps` and no predicted fields at all — absent from the
  shape, not null in it — because a page of 200 sessions carrying them is
  ≈ 19 MB of body and ~3.8 s of synchronous CPU per request (D79, superseding
  that half of D74). The pins stay, since the whole page's pins are one query.
- A success criterion's smoothing window is now bounded **above** as well as
  below: `MAX_SMOOTHING_S` is an hour, longer than any window that could mean
  something, and the API answers 422 past it (D80). The field previously took
  any non-negative integer the JSON carried.
- A prediction's explanation no longer rounds its coverage to a flat `100%` or
  `0%`: full coverage is said in words, and a partial one renders to one
  decimal, clamped to `>99.9%` / `<0.1%` at the edges. One uncovered second in
  half an hour used to print "100% of the duration carried a power target"
  beside an assumption saying the opposite.
- A target whose two bounds *render* identically collapses to a point —
  `88 % FTP`, not `88–88 % FTP`. The collapse used to test the floats, which
  binary floating point makes a different question from what a reader sees.
- Enum columns are documented as what they compile to. `enum_column` emits a
  plain `VARCHAR(n)` with **no** `CHECK` constraint on either dialect; the
  docstrings claiming one — in `persistence/types.py` and in the `0004`
  migration — now describe the emitted DDL and the consequence it was hiding:
  a future member with a longer value widens the column and needs a batch
  `ALTER COLUMN` (D81).

**Web — design system (`frontend/`)**

- The application is **dark-only** (D59). `app/globals.css` holds one `@theme`
  block of semantically named tokens — surfaces, hairlines, ink, accent,
  session status, the coach/intent tint, the zone ramp, radii and a
  dense type scale — with the shadcn vocabulary aliased onto them so the
  vendored components keep working without a second palette. Inter and
  JetBrains Mono come from `next/font`; every numeral, duration, date and
  percentage in the app is set in the mono face.
- Added the reusable pieces later pages assemble from: `AppShell` + `Toolbar` +
  `PageBody`, `SidebarNav`, `Panel`, `SectionLabel`, `PurposeBadge` (every
  value of the purpose enum, coloured), `StatusDot`, `WorkoutProfileBars`, a
  Base UI `Sheet`, and a hand-drawn inline-SVG icon set including the two
  discipline glyphs.
- The **eighteen purpose colours** live in a typed table in TypeScript
  (`PURPOSE_TONES` in `lib/purpose.ts`: edge, foreground, tint per purpose),
  applied through `style` rather than as fifty-four `--color-purpose-*` custom
  properties — Tailwind cannot emit a utility whose class name is assembled at
  runtime, so CSS tokens would have needed a safelist anyway (D63). Keyed by
  the generated `Purpose` union, the table cannot miss a purpose without
  failing the type-check, and its test reads the vocabulary out of the
  committed `openapi.json`. The *semantic* palette stays in `@theme`.
- Added the pure helpers behind them in `lib/`: duration and date formatting,
  ISO-week arithmetic on date strings (no timestamps, so a DST boundary cannot
  shift a session), the step-tree → bar-profile flattening with its zone ramp,
  the criteria-to-English translation, and the optimistic week mutation.
- The **zone ramp has seven stops**, `--color-zone-1` … `--color-zone-7`, and
  `lib/workout-profile.ts` buckets a %-of-FTP fraction through the backend's
  own `coggan_7` boundaries (`0.55 / 0.75 / 0.90 / 1.05 / 1.20 / 1.50`) rather
  than the display-only ones the mockup's five colours implied (D75). A test
  fails if either table grows a stop the other has not. Heart rate maps its
  five zones onto the same ramp, so a power chart and an HR chart will mean the
  same thing at a glance; the top two stops are crimson and berry, never
  purple, which stays reserved for agent-written text and over-target verdicts
  (invariant 7) — and WP-5's fitness/fatigue series are bound by that too.
- Added `components/design/not-assessed.tsx`: the `—` that holds a metric's
  slot when there is no number for it, carrying *why* on hover and in its
  accessible name. Missing data means "not assessed", never zero, and the grid
  never reflows around a gap. WP-7's `not_assessed(reason)` axes render through
  the same component.

**Web — calendar week page**

- Added `/calendar`: a Mon–Sun grid of session cards with prev / this week /
  next navigation, today's column and card in the accent treatment, and a
  purpose-coloured left edge on every card. `/` now redirects there — there is
  no separate home page (D60).
- **The week you are looking at is part of the address**: `/calendar?week=2026-08-03`
  (D77). The param is an ISO date taken literally, the way the endpoint takes
  `start` (D55); an unreadable one and an absent one both mean this week, so a
  bare `/calendar` is the evergreen bookmark and is where "This week" returns
  to. Stepping replaces the history entry rather than pushing one, so the back
  button still means "leave the calendar" after a minute of paging. The page
  gained a `<Suspense>` boundary, which is what keeps it prerendered as static
  now that it reads `useSearchParams`.
- **Drag to move**: native HTML5 drag-and-drop from card to day column, with an
  optimistic cache update, rollback on failure and invalidation on settle. The
  session sheet offers the same move as a date picker for anyone not using a
  mouse.
- **Session sheet**: the full prescription behind a card — the larger bar
  profile, the flattened step list (or the grouped strength lines), intent,
  coach notes, the success criteria rendered as sentences, and move / copy /
  delete / edit actions.
- The sheet now says each step's target **both ways** — `114–122 % FTP` with
  `285–305 W` beside it in secondary ink — and names the pin once per sheet:
  *"Resolved against FTP 250 W · estimated · effective 01.06.2026"*, with the
  three non-tested provenances marked differently from `tested`, because an
  estimate should read as an estimate. Predicted load renders with its coverage
  and, behind a quiet disclosure, the `MetricExplanation` the API attaches to
  it: formula, inputs naming the anchor *version*, assumptions, citation (D76).
  A session with no predictable load gets the not-assessed placeholder and the
  honest reason — no FTP pinned, no power target, or prescribed by distance —
  never a zero.
- Every criteria list in the app now states each band's and ceiling's
  **smoothing window** ("…, 30 s average", "…, raw samples"), because a
  criterion that hides its window is not one the athlete can hold anyone to.
- Added the **week rail**, left of the seven-day grid so the totals stay beside
  the days that produced them while paging weeks: planned time, planned load
  *always* with its coverage ("3 of 5 sessions"), and a per-discipline row
  whose TSS and set columns never merge. A week with nothing predictable reads
  as not assessed rather than as a light week. The rail already declares the
  props WP-4/WP-5 will fill — completed time and load, fitness, fatigue, form,
  ramp — and renders nothing for the ones that are undefined, so it is laid out
  at its final density once rather than twice; none of them is on the API
  schema, because a wall of nulls in the contract is noise until something can
  fill them.
- A paused plan shows a banner with a resume action, and the toolbar carries an
  unobtrusive pause control.
- Sections whose pages have not landed yet are listed dimmed rather than linked
  to a 404 (D61); calendar cards carry no bar profile, because the week payload
  deliberately carries no step trees (D62).

**Web — workout library and creator**

- Added `/workouts`: the library as a grid of cards — discipline glyph, the
  prescription's own measure (minutes for a ride, sets for a lift), the bar
  profile, description and the folder/tag labels — with a search box and
  folder/tag filters that go to the server (`q`, `folder`, `tag`) rather than
  filtering one fetched page. A card carries **no purpose badge**: a workout has
  no purpose, because purpose is a property of planning a session, not of the
  prescription (D66). The empty state names the remedy and carries the control.
- Added `/workouts/new` and `/workouts/{id}` — one route, one form, two verbs
  (POST / PATCH) — with name, folder (autocompleting from labels in use), tags,
  description, and a discipline switch that is fixed once a workout is saved.
- **Endurance builder**: a step-tree editor that mirrors the recursive model —
  steady steps, ramps and repeat blocks, with children rendered *inside* the
  block that repeats them, per-step role and duration-or-distance, reordering
  and removal, and nesting stopped at the domain's `MAX_NESTING_DEPTH`.
- **Per-channel targets** with a %-of-anchor / absolute toggle that switches the
  *document*, not the formatting: percentage targets carry an anchor and two
  fractions, absolute ones a unit and two numbers. Cadence is offered in
  absolute form only, because no anchor derives it.
- **Live profile preview**, drawn from the unsaved draft through the same
  `profileBars` the calendar uses, so a repeat block's expansion is visible
  while it is being typed.
- **Strength builder**: rows of movement × sets × reps × load (kg / %e1RM / RPE
  / bodyweight) × RIR × rest, grouped — and a group holding more than one row
  *is* a superset, with no flag to keep in step with the count. The movement
  picker resolves real names from `GET /exercises`, which the calendar sheet now
  uses too instead of prettifying a slug.
- The builder's state is a string-typed client draft with client-side node ids,
  translated to and from the API's structure document by three pure functions
  (D70). It mirrors the domain's plausibility bounds so an obvious mistake is
  caught without a round trip, and renders the API's 422 verbatim when the
  server refuses something the browser could not have known about (D68).

**Web — planning a session**

- Added the plan-a-session form, reached from the calendar toolbar, from a
  per-day `+` on any column (pre-filled with that date), and from Today. It
  takes a date, a purpose (grouped by discipline), a prescription from **either**
  the library — with a picker that previews the profile — **or** the inline
  builder, an intent line, notes to self, and the success criteria.
- **Success criteria follow the purpose's template until the athlete touches
  them** (D67): the template is loaded from `GET /purposes/{purpose}` and
  re-derived whenever the purpose changes, until the first edit; a reset action
  puts the list back under the template's control. Criteria are shown and edited
  as the English sentences `describeCriterion` produces, and only the kinds the
  discipline can be judged by are on offer.
- **Editing** a planned session opens the same form pre-filled and PATCHes only
  the fields that changed, so a note fixed in place does not re-pin anchors and
  a body never carries both `workout_id` and `structure`. The session sheet's
  Edit now edits the *session*; the library workout behind it is a separate,
  quieter link (D69).

**Web — Today**

- Added `/today`: the purpose badge and date, a **one-sentence headline composed
  from the plan** ("3h10 endurance ride — steady Z2", "1h09 VO₂max ride — 5×4′
  at Z5", "10 sets of max strength — 3 movements, one superset") by a pure
  helper, the intent line, the large bar profile with a zone legend, a Targets
  panel giving each channel's band across the whole prescription, the success
  criteria, and a "This week" list linking to the calendar.
- Percentage targets are resolved into watts and bpm **only when the anchor they
  name is in force**, and the resolved figure always shows the percentage it
  came from; with no anchor entered, the panel stays in percentages rather than
  inventing a number.
- The athlete's own notes render on a neutral surface: the design system's
  violet stays reserved for agent-written text (D71).
- A day with nothing planned is a deliberate rest-day state with a way out of
  it, and a day with two sessions renders both, the one still to do first.
- Weather, readiness/HRV/TSB, RPE logging, load numbers and coach proposals are
  in the mockup and deliberately absent here — they belong to work packages that
  do not exist yet.
- Today and Workouts are no longer dimmed in the sidebar, and a nested route
  (`/workouts/new`) marks the section it belongs to.

**Web — fixed before the week UI shipped**

- **Today resolved percentages against the anchor in force, not the session's
  pins.** It fetched `/anchors/current` and multiplied; the page therefore
  restated every planned watt the moment a new FTP was appended, and labelled a
  guess with the current version's provenance. Targets, the zone legend and a
  new flattened step list now render from the session's own `resolved_steps`,
  and the provenance line (`ProvenanceMark` / `AnchorProvenance`, extracted to
  `components/design/` and shared with the calendar sheet) says whose FTP they
  came from. The `/anchors/current` resolution path is gone; there is no
  function left in `lib/targets.ts` that can multiply a percentage by an
  anchor.
- **`widen()` unioned percentage bands across different anchors**, turning
  `85 % LTHR` and `75 % max HR` into "75–85 % of LTHR" — a band the plan never
  states. Bands now union only within one channel *and* one reference; two
  anchors are two rows.
- **The criteria editor could post a criterion of the wrong discipline.** The
  selected kind was remembered across a purpose change, so a touched list moved
  from a ride to a lift left `time_in_band` selected behind a strength menu.
  The selection is derived from the discipline; a kind both disciplines offer
  survives the change.
- **Planning could freeze a session with no success criteria.** Submitting
  before `GET /purposes/{purpose}` answered posted `success_criteria: []`,
  which is indistinguishable afterwards from having chosen that. Save is
  disabled with a visible "loading this purpose's criteria template…" while the
  template is in flight and the list is still the template's; a template that
  *fails* says so and lets the athlete proceed deliberately.
- **Dirty dialogs no longer discard silently.** An outside press, Escape or the
  close control on the plan form or the session sheet now raises an inline
  "Discard?" prompt when there is unsaved work, and closes instantly when there
  is not (`useDirtyClose`, reading Base UI's `onOpenChange` reason). The
  workout editor guards its "← Library" link the same way and asks the browser
  to warn on unload.
- **Calendar mutations no longer fail invisibly.** A refused move rolls back
  *and* raises a dismissible strip on the page; delete closes the sheet only on
  success and shows the refusal in the sheet otherwise; copy reports where it
  landed, or why it did not.
- **Delete asks first**, in the session sheet and the workout editor — a
  two-step button in the control's own slot, not a browser `confirm()`.
- **The week rail tells the truth about missing numbers.** Planned time renders
  not-assessed when it is null and carries its own coverage note whenever a
  session contributed none, exactly as load does; discipline rows consume their
  own four coverage fields; and the hard-coded "Strength volume is measured in
  kilograms, not TSS" is derived per row, so a *cycling* row with no TSS says
  "No prediction for 2 of 3 sessions" instead of a confident falsehood.
- **A strength session's predicted volume renders** in the sheet — kilograms,
  sets and the share of sets those kilograms came from — with the honest reason
  in its place when the loads are prescribed as %e1RM, RPE or bodyweight.
- **The optimistic move recomputes every aggregate**, not just the duration:
  load, both coverage pairs and the whole `by_discipline` block follow the
  cards that remain, so dragging a ride out of the week cannot leave a TSS on
  the rail that no session in the grid contributes to.
- Paging a week keeps the week you were reading on screen, dimmed, instead of
  blanking the page (`keepPreviousData`); the toolbar's "Plan a session"
  pre-fills a day inside the week on screen; a card dropped back on its own day
  is a no-op rather than a request; the week param is written without
  discarding the rest of the query string; the library's search waits 250 ms
  for the typing to stop; and `ProvenanceMark` gained the `NotAssessed`
  treatment so its note reaches a screen reader.
- **The open session sheet lives in the address too** —
  `/calendar?session=<uuid>`, beside `?week=` (D88). It was
  `useState<WeekSession | null>`: a sheet nobody could reload, bookmark or send
  to their coach, which is the gap D77's wording claimed the week param had
  closed. Opening a card *pushes* a history entry so the browser's Back gesture
  closes the sheet; closing *replaces*, so paging through a dozen cards does
  not bury the page the athlete arrived from. Open-state is derived from
  `useSearchParams`, never duplicated in state. The param is checked for uuid
  shape before it is spent on a request (`lib/ids.ts`): garbage is treated as
  absent and swept out of the URL, while a well-formed id that names no session
  gets the sheet's error state rather than a silent close. The sheet no longer
  needs a card at all — a link to a session outside the week on screen reads
  the session itself and asks the library for the workout's name. Not a
  `/sessions/{id}` route: that section arrives with WP-4/5 and would be built
  twice.
- Each page renders exactly one `h1`: Today's belongs to the page and its
  session headlines are `h2`s, and the library and workout editor gained one.
- Collision-prone `JSON.stringify(criterion)` and
  `` `${exercise_id}-${sets}-${reps}` `` keys are gone; these lists are
  replaced wholesale, so the index is the identity and says so in a comment.

**Web — design-system corrections (D84–D87)**

- **The strength purposes leave purple.** `max_strength` was byte-identical to
  `--color-status-over` and `--color-coach`, and four neighbours were the same
  violet family; all five move to a cyan-through-azure "steel" family (D84).
  Distance from the reserved coach/verdict tones goes from ΔE00 0.00 to 11.92
  across the whole eighteen-tone palette, and the strength family's own floor
  from 2.16 to 8.72. `lib/purpose.test.ts` now *measures* the reservation, so
  the next purpose cannot re-spend purple; a companion test checks every tone's
  badge contrast. Figures are CIEDE2000 — D75's are CIE76, which flatters
  saturated colours by roughly a factor of two.
- **Every ink is WCAG AA on every surface it lands on** (D85).
  `--color-ink-faint` was 3.01:1 on a card while carrying every uppercase
  label, the provenance line and the coverage notes; it is now `#7d848f`,
  4.54:1 at its worst. `--color-ink-disabled` stays below AA and is narrowed to
  inactive controls, which WCAG 1.4.3 exempts — a missed session's struck-out
  duration is content and moves to `ink-muted`, which is what the mockup uses
  for it. `tests/ink-contrast.test.ts` parses the palette and enforces both.
- **D59's "no colour outside `globals.css`" is now a test.** Ten inline hex and
  `rgb()` literals are gone, replaced by the tokens they were re-encoding —
  missed surface and border, danger surface and border, warn surface and
  border, accent wash, accent surface hover, chrome active (D86).
  `tests/no-literal-colours.test.ts` fails any colour literal under
  `components/` or `app/`, with one documented allowlist entry.
- Added the two stops the mockup uses and the palette had skipped:
  `--color-hairline-card` (.07, the mockup's most common border — cards,
  panels, the week shell) and `--color-hairline-faint` (.05, chart wells),
  repointed occurrence by occurrence rather than wholesale; and `--text-label`
  (10px), which is the size every uppercase micro-heading is drawn at.
  `SectionLabel` was rendering at 9.5px, the metric-caption size.
- **The sidebar shows all seven sections the mockup previews**, not the three
  that exist: Sessions, Analysis, Coach and Settings render dimmed and inert
  with a tooltip naming the work package each waits for (WP-4, WP-5, WP-8,
  after the MVP) instead of the stale "arrives with the next slice of WP-3".
  The active nav item gets its own `--color-chrome-active` token so it no
  longer shares a colour with hover.
- **D75's cross-language guard is real.** `lib/workout-profile.test.ts` reads
  `backend/app/domain/zones.py` off disk and compares the extracted `coggan_7`
  boundaries against `COGGAN_7_LOWER`; it used to assert the frontend table
  had seven entries against itself.
- Today's header carries the planned load (`planned 1:15 · 78 TSS`) when the
  session has one, and the detail workout profile gained the mockup's time axis
  beneath the bars — both absent rather than invented when the prescription
  does not support them. The mockup modules WP-3 deliberately does not build
  are listed in D87.

**Web — testing**

- `tests/mocks/fixtures.ts` was rewritten so that **every payload is one the
  real API could produce**: a card's `title` is non-null exactly when its
  `workout_id` is, strength cards carry no duration, and step counts, sets,
  durations, predicted load, intensity factor, coverage and volume load are all
  derived from the prescriptions — recomputed by running `app.domain.prediction`
  over those documents at FTP 250 W (the VO₂ session is 3 420 s, 78.3 TSS,
  IF 0.908, 82.5 % coverage; the long ride 11 400 s, 134.4 TSS, IF 0.652; the
  lift 1 920 kg over 3 of 10 sets). An intent pins exactly the anchors its
  prescription refers to, `artefact_id` is the session's own id, and no
  `tested` anchor is left without a protocol. The pinned FTP (250 W, estimated)
  and the one in force (265 W, tested) now differ in value *and* provenance, so
  a page that resolves against the wrong one is visibly wrong.
- The mutating MSW handlers **honour the request**: `move` applies the date it
  is given, `copy` answers with a new id at version 1, and `POST` / `PATCH`
  echo the submitted intent — so a form that drops a field fails its test
  instead of passing against a canned reply.
- The `next/navigation` test double **subscribes** to the address bar
  (`pushState` / `replaceState` / `popstate` through `useSyncExternalStore`,
  the way Next itself syncs), so `window.history.back()` closing the session
  sheet is a real assertion in a component test rather than a simulated one.
  The `afterUrlChange()` rerender helper it replaces is gone.
- The Playwright fake serves session detail by id and hands out ids that are
  real uuids, so the plan-a-week flow now ends by reloading the page with the
  sheet open; its cards report no predicted load, which is what a fake with no
  anchor in force would actually produce.

### WP-2 — workout model, library, purpose templates, planned sessions

The prescription half of the loop: what a session *is*, what it is *for*, and
the machinery that freezes both at planning time (build-plan invariant 4).
Decisions D42–D54.

**Domain (`backend/app/domain/`, pure)**

- Added the **structured workout** (`workout.py`): a recursive step tree of
  `SteadyStep` / `RampStep` / `RepeatBlock`, with per-channel targets (power,
  hr, cadence) that are either a percentage range of an anchor or an absolute
  range in the channel's own unit. `flatten()` expands repeat blocks — each
  flat step remembering which iteration of which block it came from — and
  leaves ramps whole, carrying a start and an end target set (D42). Rules are
  enforced at construction: exactly one of duration or distance, a channel may
  only be a percentage of an anchor it derives from, an absolute target must
  use the channel's unit and lie in a plausible range, and a ramp's two ends
  must be the same kind of target — no interpolating from a fraction of an
  unresolved anchor to an absolute number (D53).
- The nesting bound is enforced *while* a step tree is decoded, not once it is
  built: decoding is recursive, so a deep enough document exhausted the
  interpreter stack before there was a workout to check.
- A `ceiling` criterion's absolute limit is held to the channel's plausibility
  bounds, as an absolute target always was — a 1e300 W cap is a criterion
  every session passes, not a rule.
- Added the **strength model** (`strength.py`): a catalogue `Exercise`
  (slug, name, category, unilateral flag) and a `StrengthSet` prescription
  (exercise, sets, reps, load as kg / %e1RM / RPE / bodyweight, RIR, rest,
  tempo), grouped by `StrengthGroup` — a group of more than one item *is* a
  superset, with no flag to keep in step with the count.
- Added the **purpose vocabulary** (`purpose.py`): eleven endurance and seven
  strength purposes, each paired with exactly one discipline.
- Added **success criteria** (`criteria.py`): the MVP five — `time_in_band`,
  `duration_floor`, `ceiling`, `sets_completed`, `load_within` — as a
  tagged-union value set with (de)serialization. A band is a tolerance around
  the step's *own* prescribed target rather than an absolute range (D44), which
  is what lets a purpose template state one at all. Evaluation is WP-7.
- Added **purpose templates** (`templates.py`) and the `ScoringAxis`
  vocabulary WP-7 will compute (`completion, adherence, discipline, pacing,
  sets_load`, with `response`/`fuelling` reserved and deliberately unclaimed).
- Added the **planned-session intent** (`sessions.py`): purpose, prescription
  snapshot, criteria, pinned anchor versions, and the rule that every anchor a
  prescription refers to must be pinned.
- Percentages are fractions everywhere, matching the zone model (D43), and
  domain JSON is decoded by shared helpers that refuse unknown fields and
  locate every error in the document (D52).

**Data in the repository**

- Added `backend/app/resources/purpose_templates.json`: per purpose, the
  scoring axes that apply and the success criteria a session starts with.
  Loaded and validated at startup, so a file that omits a purpose, names an
  unknown axis, or carries a criterion that purpose could never evaluate stops
  the boot rather than surfacing at scoring time (D45).
- Added `backend/app/resources/exercise_catalogue.json`: 98 hand-curated
  movements across nine families (the plan's `DECIDE:` default). The
  `exercises` table is keyed by slug and seeded from it **lazily and
  idempotently on first access** — not by a migration, which a truncating test
  fixture or a restore would defeat, and not by the lifespan, which would make
  a successful boot depend on a writable database (D46).

**Persistence**

- Added the `exercises`, `workouts`, `workout_tags`, `planned_sessions` and
  `planned_session_intents` tables with migration `0003`. Prescriptions are one
  JSON document; tags get a table because "which workouts are tagged X" is a
  query and array containment is dialect-specific; folders stay a column (D50).
- Intent versions are append-only and carry WP-1's versioning vocabulary
  verbatim, so `app.domain.versioning`'s chain helpers work on the ORM rows
  unchanged.

**API**

- Added `GET/POST /api/v1/workouts`, `GET/PATCH/DELETE /api/v1/workouts/{id}`
  with search (ILIKE, wildcards escaped), folder/tag/discipline filters, and
  `GET /api/v1/workout-labels` for the folder and tag lists in use.
- Added the read-only `GET /api/v1/exercises`(`/{id}`) and
  `GET /api/v1/purposes`(`/{purpose}`), the latter returning each purpose's
  axes and default criteria so the planning UI pre-fills from the same
  templates the server derives from.
- Added `GET/POST /api/v1/planned-sessions`,
  `GET/PATCH/DELETE /api/v1/planned-sessions/{id}` and the intent history at
  `/{id}/intents` and `/{id}/intents/{version}`. Creating a session derives its
  criteria from the purpose template and pins the anchor versions in force; a
  prescription referring to an anchor with none in force is refused with a 422
  that names which half of it — the targets, the criteria or both — asked for
  the anchor, and what to do about it (D49).
- `PATCH /api/v1/planned-sessions/{id}` refuses an explicit `null` for
  `purpose`, `date` or `status` with a 422, and refuses an empty body rather
  than answering 200 with an audit row saying nothing changed. A patch that
  moves the session *and* edits its intent now leaves both audit rows.
- Implemented the freeze rule (D47): an intent edit before a match exists
  writes a new version and re-pins; an edit after a match exists writes a new
  version flagged `edited_post_hoc`, **keeps** the pins the athlete executed
  against, and triggers a rescore. Editing only a session's date or status
  versions nothing. Matching and rescoring do not exist yet, so both are
  explicit, tested seams the later work packages replace with one function
  each (D48).
- The whole step tree and criterion set are typed end to end: recursive
  discriminated unions in the API schemas, regenerated into
  `frontend/generated/api/`.
- Schemathesis found that `/workouts/folders` and `/workouts/tags` were
  shadowed by `/workouts/{workout_id}`, so an undocumented method on them
  answered 422 about uuid syntax instead of 405. The facet moved to
  `/workout-labels`, outside the id namespace (D50); the four new write
  operations that refuse schema-valid input by domain rule are narrowed per
  operation in `backend/schemathesis.toml`.

**Testing**

- Added property tests over random step trees (hypothesis): flattening and
  expansion round-trip, indices are execution order, duration is conserved,
  and the serialized form round-trips.
- Added template-derivation tests for every purpose in the vocabulary, plus
  the malformed-file cases that must stop the boot.
- Added freeze-semantics tests driving the match/rescore seams: post-match
  edit produces a new version, the flag, kept pins, a rescore call and a
  retrievable original; pre-match edit produces a new version, no flag, and
  re-pinned anchors.
- The unit suite now turns SQLite's foreign keys on (D51), so `ON DELETE
  CASCADE`/`SET NULL` behave there as they do on Postgres — the divergence
  that hid a real failure until the pragma went in.

### WP-1 — domain core: athlete, anchors, zones, versioning primitives

The first real entities, and the first code the build plan's invariants are
enforced by rather than described in. Decisions D31–D37.

**Domain (`backend/app/domain/`, pure)**

- Added **versioning primitives** (`versioning.py`): the `VersionRecord`
  protocol and the `Versioned[T]` envelope fixing the vocabulary every derived
  artefact will carry — `artefact_id`, `version`, `as_of`, `superseded_by`,
  `recompute_reason` — plus `current_version`, `version_as_seen_at` and
  `next_version`. Recomputation returns both halves of the change (the closed
  old version and the new tip), so a chain cannot be left with a dangling link.
  Nothing in WP-1 is versioned yet; scores, metrics and alignment are, from
  WP-5.
- Added the **athlete profile** (`athlete.py`): name, date of birth, sex,
  height, and a free-form per-discipline capability stub. Every field is
  optional — an empty profile is a legal state, not an error.
- Added **anchors** (`anchors.py`): `AnchorType` (FTP, LTHR, MAX_HR, with CP
  and W′ reserved and unused), `Provenance`, `AnchorSource`, `AnchorUnit`,
  `StalenessState` (hardcoded `fresh`; `aging`/`stale` reserved), and the
  immutable `AnchorVersion`. Legality is enforced where it belongs (D35): the
  unit must be the anchor type's own, values must be plausible per type,
  `tested` provenance requires a protocol, and a confidence interval must
  bracket its value. `anchor_as_of` computes which version was in force at a
  moment — effective date *and* creation time — so a back-dated correction
  changes the present without rewriting the past.
- Added **zones** (`zones.py`): `zones_for(anchor_version, model)`, with
  `coggan_7` (%FTP) and `lthr_5` (%LTHR) boundary tables documented in D32.
  Zones are always computed, never stored. Bands are half-open and contiguous,
  the top zone is open-ended, and a model may only be applied to the anchor
  type it derives from.
- Domain values are frozen dataclasses rather than pydantic models (D31);
  `app.core.exceptions.domain_rules()` translates their `ValueError`s into the
  documented 422 envelope.

**Persistence**

- Added the `athlete`, `anchor_versions` and `audit_log` tables with their
  repositories, and migration `0002` creating them. The athlete row is a
  singleton with a fixed primary key, bootstrapped on first access rather than
  seeded by the migration (D33). Neither the anchor nor the audit repository
  offers an update or a delete.
- `enum_column` now stores the enum member's **value** (`max_hr`), not
  SQLAlchemy's default of its name (`MAX_HR`), so the database, the API and the
  generated frontend types share one vocabulary (D34). WP-1 is its first user,
  so nothing needed migrating.

**API**

- Added `GET`/`PATCH /api/v1/athlete`, `GET`/`POST /api/v1/anchors`,
  `GET /api/v1/anchors/current` and `GET /api/v1/anchors/{id}`, all on the
  guarded router.
- Added zones as two endpoints, each addressed by what it derives from (D38):
  `GET /api/v1/zones?anchor_type=…` uses the version in force,
  `GET /api/v1/anchors/{id}/zones` uses one pinned version. The zone model is
  derived from the anchor type or named explicitly.
- `PUT`, `PATCH` and `DELETE` on an anchor version return **405 with an
  explanation** and an `Allow` header, because FastAPI answers an undefined
  method+path with 404 — which reads as "wrong id" (D36).
- The reserved anchor types `cp` and `w_prime` cannot be appended (D40): the
  create contract only offers the MVP three, and the service refuses them for
  callers that bypass the schema.
- The singleton athlete bootstrap is race-tolerant and never a side effect of
  a rejected write (D41): a lost first-access race returns the winner's row
  instead of a 409, and a 422'd first-ever `PATCH` leaves the database
  untouched — bootstrap and update happen in one transaction, both audited.
- Hardened the new surface against what Schemathesis found (D39): the 422
  contract now admits both shapes the status really has
  (`ValidationErrorDetail`), the append-only refusals answer 405 for any id
  rather than 422 for a malformed one, and free-form `capabilities` JSON is
  validated for driver-safe text at every depth, not just at the top level.
  `backend/schemathesis.toml` narrows the `positive_data_acceptance` check for
  the handful of operations that refuse schema-valid input by design, instead
  of switching it off everywhere.

**Audit log**

- Every mutating service path now appends an `audit_log` row — actor (`athlete`
  / `agent:<label>` / `system`), action, entity type and id, JSON payload, and
  timestamp — in the same transaction as the write it describes, so a rejected
  write leaves no trail and no write escapes one.

**Removed**

- Deleted WP-0's `items` worked example end to end: backend persistence,
  service, schemas, routes and tests; the frontend page, component and MSW
  handler; and the table itself, dropped by migration `0002` rather than by
  rewriting the already-shipped `0001` (whose `downgrade` recreates it, so the
  chain still round-trips).

**Testing**

- Added `hypothesis` (dev-only, D37) and the repo's first property tests: zone
  schemes must partition, stay ordered, scale linearly with the anchor, and
  keep percentages and absolute bounds in agreement — properties that hold for
  any scheme added later, not just the two shipped.

### WP-0 — scaffold + infrastructure

The scaffold was built by adapting the full-stack template rather than
scaffolding the build plan's `apps/`+`packages/` workspace monorepo; the
reasoning for this and every other departure is in `docs/decisions.md`
(D1–D19).

**Backend architecture**

- Restructured the backend from `app/domains/<domain>/` into layered modules:
  `app/domain` (pure business rules, filled in by WP-1), `app/persistence`
  (db, ORM models, repositories, Alembic), `app/services`, `app/ingest` and
  `app/mcp` skeletons, and `app/api` (routes, schemas, pagination,
  validation), with `app/core` reduced to genuinely cross-cutting code.
  Boundaries — domain purity, `api`/`mcp` independence, and the layer stack
  `api|mcp → ingest → services → persistence → domain` — are enforced by
  import-linter contracts (`uv run lint-imports`) wired into CI, `just lint`
  and pre-push. The OpenAPI contract is unchanged.
- Upgraded the backend from Python 3.13 to 3.14 (D4) across `pyproject.toml`,
  `.python-version`, `pyrefly.toml`, both `Dockerfile` stages and the
  devcontainer image, and relocked `uv.lock`.
- Removed the ARQ worker and its Redis service (D5), replacing them with an
  in-process APScheduler started by the API lifespan
  (`backend/app/core/scheduler.py`); dropped the `redis` and `worker` compose
  services, the `dev-worker` recipe and the `REDIS__URL` setting.

**Authentication**

- Added single-user session-cookie authentication end to end (D6). The
  credential store is one setting, `AUTH__PASSWORD_HASH` (a bcrypt hash —
  there is no user table); `POST /api/v1/auth/login` swaps it for a signed
  session cookie issued by Starlette's `SessionMiddleware` (`arc_session`,
  `SameSite=Lax`, 14 days, `Secure` once `AUTH__SESSION__HTTPS_ONLY` is on),
  with `POST /api/v1/auth/logout` and an always-open
  `GET /api/v1/auth/session` alongside it. Everything else under `/api/v1` is
  mounted on a router carrying `Depends(require_session)` and a declared 401,
  so new routers are protected by default (D12); `/health` stays open. Failed
  logins sleep ~0.3s to blunt guessing, and production refuses to boot without
  `AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY`. The unused
  `AUTH__JWT__*` shell is gone.
- On the frontend, the API client sends credentials, a `/login` page posts the
  password, and an `AuthGuard` client component bounces unauthenticated
  visitors off the protected pages.
- Schemathesis found an undocumented 400 on login (unparseable body); the
  contract now declares it and a unit test pins it. The fuzz job supplies a
  session cookie and excludes the `ignored_auth` check, which cannot strip a
  raw header (D13).

**MCP server**

- Added the MCP server skeleton (`backend/app/mcp/`): a FastMCP 3 server run
  from the backend image as the `mcp` compose service
  (`python -m app.mcp.main`, streamable HTTP on :8001, behind Caddy's
  `/mcp*`). Every request must present a bearer key from `MCP__API_KEYS`
  (`label:scope:key,...`, scope `read` or `write`); keys are parsed by the
  framework-free `app/mcp/auth.py` and compared with `secrets.compare_digest`
  in a `TokenVerifier` subclass (D10), which puts the caller's label and scope
  on the request identity for per-tool scope checks in WP-8. The server
  refuses to start (exit 1) with no keys, so `MCP__API_KEYS` is required for
  `docker compose up`. The surface is one `ping` tool plus an unauthenticated
  `/health` route for the container healthcheck.

**Infrastructure**

- Added a Caddy reverse proxy (`caddy/Caddyfile`, `caddy` service on :80/:443)
  fronting the whole stack from one origin: `/api/*` and `/health` to the API,
  `/mcp*` to the MCP server, everything else to the frontend.
  `CADDY_SITE_ADDRESS` defaults to `:80` (plain HTTP); set a hostname for
  automatic HTTPS. The frontend is built with an empty
  `NEXT_PUBLIC_API_BASE_URL`, so the browser calls the API same-origin through
  the proxy, and the `@fullstack` smoke suite runs against `http://localhost`.
  Caddy deliberately does not depend on `mcp`, so a missing MCP key set cannot
  take the site down (D9).
- Upgraded Postgres from 17 to 18 (dev stack and the integration-test
  database). Postgres 18 moved the image's `VOLUME` to `/var/lib/postgresql`
  (`PGDATA` is now `/var/lib/postgresql/18/docker`), so the `postgres-data`
  volume and the test tmpfs mount that path instead of `.../data`. **Existing
  local volumes hold a 17 cluster and must be recreated**
  (`docker compose down -v`).
- Added the runtime data tree: `DATA__ROOT` (default `data`) with `inbox/`,
  `originals/`, `streams/`, `quarantine/` created on API startup and
  bind-mounted into the api container at `/app/data`; a one-shot `data-init`
  service hands the root-owned bind mount to the api's non-root user first
  (D8).

**Developer workflow**

- Made `just init` real (`scripts/bootstrap-env.sh`): it copies `.env.example`
  to a mode-600 `.env`, generates `POSTGRES__PASSWORD`,
  `AUTH__SESSION__SECRET_KEY` and both `MCP__API_KEYS`, and prompts (hidden,
  twice) for the login password it bcrypt-hashes into `AUTH__PASSWORD_HASH`.
  Values are substituted with Python rather than `sed`, so the `$` and `/` in
  bcrypt hashes survive; the script is idempotent and degrades to a
  placeholder hash plus instructions when there is no terminal to prompt on
  (D15). `just hash-password` prints a ready-to-paste single-quoted hash for
  rotating the password later.
- `just check` now also runs `api-check`, so API-contract drift is part of the
  one local gate, and the devcontainer installs `just` itself
  (`uv tool install rust-just`, D16) — the whole workflow depended on a tool
  that was not in the image.
- The Playwright `@fullstack` suite logs in once in a `setup` project and
  replays the session via `storageState` (D14).
- Repo hygiene: tracked `docs/`, removed orphaned build artifacts
  (`packages/`, root `node_modules/`), ignored `/data/` and `.schemathesis/`,
  and bumped the `ruff-pre-commit` hook to v0.16.1 to match the backend
  lockfile.

- Added changelog tooling (D18). `just changelog` runs git-cliff (`cliff.toml`)
  over the conventional commits since the last tag and prints a **draft** —
  commit bodies included, grouped under Keep a Changelog headings — whose
  entries are edited down by hand into the `## [Unreleased]` section above;
  `just changelog-range main..HEAD` does the same for a branch. Nothing writes
  to `CHANGELOG.md`. Conventional Commit format is now enforced rather than
  documented, in two deliberately unequal layers: a `commit-msg` hook
  (`conventional-pre-commit`, plus the `commit-msg` prek shim) rejects branch
  subjects it cannot parse, and `.github/workflows/pr-title.yml` additionally
  requires the PR title to start lowercase and not end in a period. The title
  is the one that matters — the `protect-main` ruleset allows only squash
  merges, so it becomes the commit subject on `main`, where
  `filter_unconventional` would otherwise drop an unparseable subject from
  every draft with no error. git-cliff installs in the devcontainer via
  `uv tool install git-cliff`.
- Switched the repository's squash-merge settings to `PR_TITLE` + `PR_BODY`
  (D19), so a merged PR's description — not a bullet dump of its commits —
  becomes the commit body on `main` and the raw material for a changelog entry.
  The `protect-main` ruleset gained a `required_status_checks` rule naming the
  `pr-title` check, so a non-conventional title now blocks the merge instead of
  only annotating it.
- Replaced the `commit-commands` plugin with project skills (`.claude/skills/`:
  `commit`, `commit-push-pr`, `clean-gone`), disabling the plugin in the repo's
  `.claude/settings.json` so the swap travels with the checkout. The plugin's
  generic "create a commit with an appropriate message" knew nothing of this
  repo's conventional format, work-package scopes, hooks that rewrite files
  mid-commit, or the squash-only PR title rule; its `clean_gone` also grepped
  `git branch -v` for `[gone]`, which that command never prints (only `-vv`
  does), so it matched nothing.

**Documentation**

- Seeded `CHANGELOG.md` and the decision log `docs/decisions.md` (D1–D18).
- Aligned `docs/mvp-build-plan.md` (stack, repository layout, WP-0),
  `docs/tech-stack.md`, `README.md`, `AGENTS.md`, `backend/README.md` and
  `frontend/README.md` with what was actually built, and corrected the stale
  references in WP-1…WP-9 (`packages/*` paths, `make` targets) so later work
  packages execute against the real repository.
- Folded `AGENTS.md` and `frontend/AGENTS.md` into `CLAUDE.md` and
  `frontend/CLAUDE.md`, which previously only `@`-included them, and dropped the
  `AGENTS.md` files — this project is worked on with Claude Code only (D17).
- Re-verified the WP-0 scaffold against the running stack and corrected the
  drift it exposed. `scripts/setup-repo.sh` did not reproduce the repository
  configuration D19 describes — it left `squash_merge_commit_title`/`_message`
  at their defaults and created a `protect-main` ruleset with no
  `required_status_checks` rule, so a fresh clone of this template got neither
  `PR_TITLE`+`PR_BODY` squash commits nor a blocking `pr-title` check; both are
  now applied, with a note that an existing ruleset is skipped rather than
  updated. In the docs: the Python pin is the *minor* (`3.14`, patch floats —
  3.14.4 in the devcontainer, 3.14.6 in the runtime image), not the "3.14.6"
  the plan and tech stack claimed; the release publishes three images
  (`api`, `mcp`, `frontend`), not two; WP-0's CI summary described the
  integration job's throwaway Postgres as a service container and the
  full-stack smoke job as "Docker Compose validation"; and WP-0 gained the
  repo-governance/dev-workflow item (devcontainer, prek hooks, squash-only
  ruleset, changelog tooling) that D16–D19 recorded but the plan never listed.
  Documented the first-run trap that `just init` + a pre-existing
  `postgres-data` volume produces: a new random `POSTGRES__PASSWORD` that
  Postgres ignores, surfacing as an api crash-loop on `InvalidPasswordError`.
