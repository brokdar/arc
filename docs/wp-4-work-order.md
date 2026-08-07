# WP-4 Work Order — ingestion: watched folder, FIT parsing, sessions & streams

**For:** the developer on `feat/wp-4-ingestion`.
**Scope:** build-plan §WP-4 **plus** every §4 addendum (A4.1–A4.5) and the R3–R5
reservations from `docs/mvp-plan-addenda.md`. The addenda are not optional garnish —
§4 is "the irreversible window": everything there changes the shape of what gets
written to `data/streams/` and to the session row, and skipping any of it means
re-ingesting every file later.

**Ground rule:** if an item here contradicts `docs/mvp-build-plan.md`, this document
wins and says so. Everything else in the build plan stands. Where this document says
`DECIDE (default: …)`, take the default and append the decision to `docs/decisions.md`
(append-only, continue after the last entry).

**Phases.** The work lands in three reviewed phases, each ending with tests green and
`just check` clean:

- **Phase A** — domain + persistence: the pure stream/session model, resampling,
  cleaning, validation, classification, timezone rules; ORM models + migration.
- **Phase B** — the ingest pipeline + services + API: parsers, the per-file pipeline,
  the inbox scheduler job, upload, manual entry, quarantine actions, session list.
- **Phase C** — frontend: inbox/quarantine page, session list page.

---

## 0. Resolved decisions (write these into `docs/decisions.md`)

1. **pyarrow arrives in WP-4, polars stays WP-5.** The build plan's §1 lists
   polars + pyarrow under WP-5, but WP-4.1 writes parquet — a contradiction the plan's
   own rule resolves ("each arrives with the work package that first needs it").
   Parquet writing needs pyarrow now; nothing in WP-4 needs polars (a 4 h ride is
   14 400 rows — plain Python lists are fine, exactly as D-argued for `normalized_power`).
2. **The 1 Hz index grid is the stream storage contract** (addenda A4.1, decision
   entry per addenda §9.5). What it displaced: storing device timestamps as recorded.
3. **Raw and cleaned streams both stored; every repair recorded as an anomaly row**
   (A4.2, addenda §9.6).
4. **Golden FIT files are synthetic, generated in-repo.** The operator has provided no
   real files. Per build-plan §4 guidance: generate deterministic synthetic FIT files
   (dev-dependency `fit-tool`, or a committed generator script + committed binaries)
   covering: outdoor ride (GPS + power + HR, irregular sampling, a >30 s pause),
   indoor trainer ride (no GPS, smooth power), strength-watch recording (short, HR
   only, no GPS/power). Mark real-file parse tests operator-pending in a comment; do
   **not** skip pipeline tests.
5. **Timezone on the session is best-effort from the file.** FIT carries UTC plus (when
   the device writes it) a local offset. `DECIDE (default:)` store IANA name when the
   source provides one (it usually does not), else a fixed-offset string
   (`"UTC+02:00"`) derived from the FIT `local_timestamp`; else `"UTC"`. The column is
   the athlete-local truth used for the session date; it is athlete-overridable later
   (the override re-derives the date).

---

## 1. Dependencies

Runtime (`[project].dependencies`): `garmin-fit-sdk`, `fitdecode`, `gpxpy`,
`tcxreader`, `pyarrow`. Dev: `fit-tool` (synthetic golden files) if used.

Every new runtime dependency must be classified for the domain-purity contract:
parsers and pyarrow are I/O — add each to `forbidden_modules` in
`backend/pyproject.toml` (they may be imported only from `app.ingest`), and satisfy
`test_domain_purity_contract`. The domain never sees a FIT record or an arrow table;
it sees plain Python sequences and dataclasses.

---

## 2. Phase A — domain + persistence

### A-1 · Parsed-activity value objects (`app/domain/streams.py`, new, pure)

The interchange shape between parsers (ingest) and everything else:

```python
Channel sample values are plain floats; time is seconds.

@dataclass(frozen=True, slots=True)
class RawSample:
    t: datetime            # aware UTC, as recorded
    values: Mapping[StreamChannel, float]   # only channels present in this sample

class StreamChannel(StrEnum):
    POWER = "power"; HR = "hr"; CADENCE = "cadence"; SPEED = "speed"
    ELEVATION = "elevation"; TEMP = "temp"; LAT = "lat"; LON = "lon"

@dataclass(frozen=True, slots=True)
class ParsedActivity:
    """One sport within one file (A4.5: a file yields a *list* of these)."""
    file_sport_index: int
    sport: str | None              # raw sport string from the file
    start_time: datetime           # aware UTC
    local_offset: timedelta | None # from FIT local_timestamp, when present
    samples: Sequence[RawSample]   # sorted, may be irregular
    laps: Sequence[tuple[datetime, datetime]]
    power_source_candidates: Sequence[str]
    power_source: str | None
    power_source_rule: str | None
    hr_source_candidates: Sequence[str]
    hr_source: str | None
    hr_source_rule: str | None
```

### A-2 · The 1 Hz grid (A4.1) — `resample()` in `app/domain/streams.py`

```python
@dataclass(frozen=True, slots=True)
class StreamFrame:
    """Uniform 1 Hz grid. Row i of every column describes t0 + i seconds."""
    t0: datetime                       # grid origin, aware UTC
    device_t: list[datetime | None]    # original timestamp where a real sample landed
    columns: Mapping[StreamChannel, list[float | None]]  # identical lengths

def resample(samples: Sequence[RawSample], *, gap_threshold_s: int = 30) -> ResampleResult
```

Rules — implement exactly:

- One row per elapsed second from first to last sample. **Every column has identical
  length** (property-test this).
- A gap **longer than `gap_threshold_s` (30 s)** is a recording stop: the grid
  continues, all channels are **null** across it (a hole is not zero watts), and the
  gap is returned as a `[start_index, end_index)` entry in `recording_stops`.
- Sub-threshold irregularity per channel: `lat`/`lon`/`elevation` interpolate
  linearly; `power`/`hr`/`cadence`/`speed`/`temp` hold the previous value up to the
  threshold. Document the per-channel rule in the docstring.
- `ResampleResult` also carries A4.4's numbers, computed here because only the
  pre-resample samples can answer them: `elapsed_time_s`, `recording_time_s`
  (elapsed − Σ gaps > 30 s — **the load duration term, A5.1**), `recording_stops`,
  `median_time_delta_s`, `moving_time_s` (speed ≥ 1 km/h; display only).

### A-3 · Cleaning + anomalies (A4.2) — `clean()` in `app/domain/streams.py`

Consumes a `StreamFrame`, returns per-channel `*_fixed` columns plus anomaly records:

```python
class AnomalyKind(StrEnum):
    GAP_INTERPOLATED = "gap_interpolated"; SPIKE_CLIPPED = "spike_clipped"
    DROPOUT_HELD = "dropout_held"; RESAMPLED_ONLY = "resampled_only"

@dataclass(frozen=True, slots=True)
class Anomaly:
    channel: StreamChannel
    start_index: int; end_index: int   # [start, end)
    kind: AnomalyKind
    substituted_value: float | None
```

`DECIDE (default:)` cleaning rules, per channel, plausible ranges from the build plan
(power 0–2500 W, HR 25–230, speed < 35 m/s):

- An out-of-range excursion **≤ 3 s** → clip to the last in-range value
  (`spike_clipped`).
- An out-of-range or missing run **> 3 s and ≤ 30 s** → hold previous value
  (`dropout_held`) for power/hr/cadence/speed; interpolate for elevation
  (`gap_interpolated`).
- Runs > 30 s are recording stops (A-2) and stay null — never invented.
- The **raw column keeps the spike**; analysis reads `*_fixed` only.

### A-4 · Validation (quarantine triggers) — `validate()` in `app/domain/streams.py`

A file (per parsed activity) is **quarantined**, not repaired, when:

- timestamps are non-monotonic after a stable sort attempt;
- total elapsed duration < 2 min;
- more than `DECIDE (default: 10 %)` of a present channel's samples are implausible
  (that is systemic garbage, not a spike);
- the parser yielded no samples at all.

Return a machine-readable reason (enum + human detail string) — the quarantine record
and UI both show it.

### A-5 · Discipline classification — pure function

FIT sport field maps first (`cycling → ride`, `training`/`strength_training` →
`strength`); fallback heuristics: has power or speed → `ride`; short (< 90 min) + no
GPS + no power → `strength` candidate; else `other`. Returns
`(discipline, classification_source: "sport_field" | "heuristic")`; always
athlete-overridable (B-6). Reserve `Discipline` values you need; do not invent new
ones beyond `ride | strength | other`.

### A-6 · Session date + timezone (build-plan WP-4.4)

Pure helper: `session_date(start_utc, tz) -> date` — start time in athlete-local tz;
midnight-crossers belong to the start date. Property-test with hypothesis across
offsets and midnight-straddling starts.

### A-7 · Persistence (`app/persistence/…` + one Alembic migration)

Tables (all with the WP-1 conventions: `UtcDateTime`, `JSONColumn`, `enum_column`,
uuid7 ids, named constraints; every mutation audited):

- **`session`** — start_time (UTC), end_time, timezone (str), local_date (date),
  discipline, classification_source, discipline_overridden (bool),
  status (reserve `unmatched` for now; WP-6 owns the lifecycle),
  recording_kind (`device | manual`), notes.
  Reserved, nullable, no behavior: `weight_kg` + `weight_provenance` (R3),
  `session_context` enum `training|commute|group_ride|race|event`, only `training`
  produced (R5).
- **`recording`** — FK session (1:1 now, schema permits N), file_hash (sha256 hex),
  file_sport_index (A4.5), original_path, original_ext, elapsed_time_s,
  recording_time_s, recording_stops (JSON), median_time_delta_s, moving_time_s,
  power_source_candidates/power_source/power_source_rule + hr_* (A4.3),
  channel list actually present. Reserved: `external_id`, `source` (R4).
  **Unique constraint on `(file_hash, file_sport_index)`** — the dedup key.
- **`stream_anomaly`** — recording FK, channel, start_index, end_index, kind,
  substituted_value, created_at.
- **`quarantine_record`** — original filename, file_hash, reason (enum), detail,
  quarantined_path, status (`pending | confirmed_discarded | rejected_ingested`),
  suspected_session_id (nullable FK — the duplicate case), created_at, resolved_at.
- **`ingest_event`** — append-only log: filename, file_hash, outcome
  (`ingested | duplicate_file | quarantined | error`), detail, session_id nullable,
  at. The UI's "ingest log list" reads this.
- **`logged_set`** — session FK, exercise FK (nullable — free-text name fallback),
  set_index, reps, load_kg (nullable), rir (nullable), notes. Manual strength entry
  (B-6) writes these; WP-6/7 strength alignment reads them.

**Tests, Phase A:** hypothesis property tests on `resample` (equal column lengths,
row count == elapsed seconds + 1 or == elapsed, pinned; nulls across a synthetic
pause; recording_time arithmetic), `clean` (spike in raw, absent in fixed, anomaly
indices correct), `session_date` midnight/timezone properties, classification table,
migration round-trip via the existing integration harness.

---

## 3. Phase B — pipeline, services, API

### B-1 · Parsers (`app/ingest/parsers/…`)

`parse(path) -> Sequence[ParsedActivity]` (A4.5 — always a sequence). FIT via
`garmin-fit-sdk`, falling back to `fitdecode` when the SDK raises; GPX via `gpxpy`;
TCX via `tcxreader`. Power/HR source candidates (A4.3): enumerate device_info-derived
candidates in FIT; single-candidate files record `"only candidate"` as the rule.
Parsers are the **only** modules importing these libraries.

### B-2 · The pipeline (`app/ingest/pipeline.py`), per file

hash (sha256) → duplicate check (`(hash, sport_index)` against recordings **and**
pending quarantine) → parse → per activity: validate (A-4; failure → move file to
`data/quarantine/`, quarantine_record + ingest_event) → **overlap dedup**: time range
overlapping an existing session > 70 % → quarantine as `suspected_duplicate`
(with `suspected_session_id`) → otherwise: move original to
`data/originals/YYYY/MM/<hash>.<ext>` (one file, N activities → one original; never
modified after), create session + recording rows, resample + clean, write parquet.

**Parquet contract** (`data/streams/<recording_id>.parquet`, pyarrow):
columns `t` (original device timestamp, null where interpolated), raw channels,
`*_fixed` channels for cleaned ones; file metadata: `t0` (ISO), per-channel source
label. Everything downstream addresses rows by index (A4.1).

The pipeline is idempotent: re-seeing a known hash is a no-op `duplicate_file`
ingest_event, not an error. Failures mid-pipeline must not lose the file — quarantine
is the catch-all. **Never delete anything under `data/originals/`.**

### B-3 · Scheduler job + upload

APScheduler job, 30 s interval, scanning `data/inbox/` (skip dotfiles and files still
growing — size-stable check between two scans, or ignore files modified < 2 s ago).
Registered from the lifespan next to `create_scheduler()`; uses `session_scope()`.
`POST /api/v1/ingest/upload` (multipart) writes into the inbox and runs the pipeline
for that file synchronously, returning the outcome (session ids, quarantine, or
duplicate).

### B-4 · Quarantine actions + ingest log API

- `GET /api/v1/ingest/quarantine` (pending first), `GET /api/v1/ingest/events`
  (paginated, bounded like the WP-3 lists).
- `POST /api/v1/ingest/quarantine/{id}/confirm` — duplicate confirmed: discard the
  quarantined file (log; the *original* of the already-ingested twin stays),
  status `confirmed_discarded`.
- `POST /api/v1/ingest/quarantine/{id}/reject` — not a duplicate: ingest the file as
  a separate session through the normal pipeline, status `rejected_ingested`.
  Reject on a non-duplicate quarantine (corrupt file) is a 409 — there is nothing
  safe to ingest; `DECIDE (default:)` corrupt-file records offer only confirm
  (= discard) and re-drop after fixing.

### B-5 · Session list + detail API

`GET /api/v1/sessions?from=&to=` — paginated, bounded; rows: id, local_date,
discipline, recording_kind, duration (recording_time_s), matched-badge placeholder
(`unmatched` constant until WP-6). `GET /api/v1/sessions/{id}` — session + recording
metadata (sources, stops, anomaly count) — **not** the streams; stream endpoints are
WP-5. `PATCH /api/v1/sessions/{id}` — discipline override (sets
`discipline_overridden`), timezone override (re-derives local_date). Audited.

### B-6 · Manual session entry

`POST /api/v1/sessions/manual` — discipline (default `strength`), start_time + tz (or
local date + time), duration, RPE (nullable), notes, logged sets
(exercise_id | free-text name, reps, load_kg?, rir?). Creates session with
`recording_kind=manual`, no recording row, logged_set rows. Services commit; every
write takes `actor: Actor`.

Run `just api-sync` after every schema change; commit the result.

**Tests, Phase B:** golden synthetic FIT files → snapshot parsed summaries (sample
count, channels, sources, duration numbers); quarantine paths (corrupt, absurd
values, duplicate-by-hash, duplicate-by-overlap with confirm and reject flows);
upload endpoint through HTTP; inbox job unit-tested by invoking its function against
a tmp inbox (no sleeping); manual entry; the A4.4 coffee-stop case
(elapsed − recording ≈ 600 s, one stop). Parquet round-trip: write then read, assert
grid invariants.

---

## 4. Phase C — frontend

Follow `.claude/rules/frontend-ui-conventions.md` (routes for bookmarkable things,
empty states name the missing input, metric grids hold positions, mono numerals) and
the existing design system — no new colors outside `globals.css` (the
no-literal-colours test enforces it). The mockup does not draw these pages (D87);
build them from the design system's own vocabulary (Panel, SectionLabel, StatusDot,
purpose/discipline icons).

- **`/inbox`** — pending duplicates and quarantined files: reason, detail, filename,
  date, suspected-session link when present; confirm/reject actions with the
  `Confirm` component; below, the ingest event log (paginated). Empty state: "Drop
  FIT/TCX/GPX files into the inbox folder or upload here" + an upload control posting
  to `/api/v1/ingest/upload`.
- **`/sessions`** — session list: local date, discipline icon, duration,
  recording-kind marker, matched/unmatched badge (all unmatched until WP-6 — the
  badge component takes the state as a prop). Load column is an **optional slot**
  rendering `—` until WP-5 (conventions rule 4). Row links to `/sessions/{id}` —
  a minimal detail page now (metadata, sources, stops, anomaly count) that WP-5's
  charts will grow into.
- Sidebar nav gains Inbox and Sessions entries (keep the nav-truth test green).
- Tests: typed MSW handlers + honest fixtures (a fixture must be a payload the real
  API could produce — derive numbers, don't type them in); component tests for both
  pages incl. confirm/reject mutations echoing what they were sent; e2e spec for the
  inbox flow against mocks.

---

## 5. Definition of done (whole WP)

- Drop a synthetic FIT into `data/inbox/` on a running stack → session appears within
  60 s (verified by the smoke path or a manual note in the PR).
- Addenda Appendix B items that land here hold: every stream frame has identical
  column lengths and one row per elapsed second; pauses are nulls, not zeros; a known
  spike is present raw, absent fixed, recorded as an anomaly; dedup key includes the
  sport index.
- `just check` green; `just test-int` green (migration chain + Postgres dialect);
  frontend tests green; `just api-sync` drift-free.
- Decisions from §0 appended to `docs/decisions.md`; CHANGELOG entries drafted.
- No feature creep: no vendor adapters, no merging of multi-recording sessions, no
  metrics/NP over actual streams (WP-5), no matching (WP-6).
