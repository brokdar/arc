# MVP Plan Addenda — Reference-Informed Additions

**Status:** proposed additions to `docs/mvp-build-plan.md`. Not a replacement — every
item below is an *addition* or a *narrowing* of an existing work package. Where an item
contradicts the build plan, it says so explicitly and gives the reasoning.

**Origin:** an engineering study of intervals.icu (`docs/intervals-icu-study/`) read
against `docs/training-application-description-v2.md` and the build plan. This document
is self-contained: every formula, constant and convention a developer needs is restated
here or in Appendix A. You do not need to read the study to execute any item.

**Current position:** WP-0, WP-1, WP-2 delivered. **WP-3 in flight.** WP-4 not started.

---

## 0. How to use this document

Each item has a fixed shape:

> **ID · Title**
> **Do it in:** which work package · **Cost:** rough size · **If we skip it:** what the
> retrofit costs later
> **What** — the change, concretely.
> **Why** — one paragraph. No item is here because the reference does it; each is here
> because of what it costs us to add later.
> **Done when** — the acceptance check.

Items are ordered by *when you must decide*, not by importance. **§2 and §3 are for the
developer working on WP-3 today.** §4 is the one section where lateness is genuinely
expensive — it changes the shape of stored data, and every item in it becomes a re-ingest
of every file once real data exists.

Three cost words are used consistently:

- **Free** — a convention, a name, or a reserved enum value. No behavior, no migration.
- **Small** — under a day, self-contained, testable at the domain layer.
- **Real** — a day or more, or it touches a boundary.

---

## 1. Summary

| ID | Item | WP | Cost | Retrofit cost if skipped |
|---|---|---|---|---|
| **A3.1** | Predicted load + intensity on a planned session | WP-3 | Small | Small — pure function, no schema |
| **A3.2** | `normalized_power()` in the domain, shared by plan and actual | WP-3 | Small | Small, but two divergent implementations first |
| **A3.3** | Week aggregates: per-discipline rows + honest coverage counts | WP-3 | Small | Small |
| **A3.4** | Full 7-stop model-zone ramp; purple reserved for the coach | WP-3 | Free | Real — repalette 6 chart types |
| **A3.5** | Session sheet shows resolved absolute targets + which anchor version | WP-3 | Small | Small |
| **A3.6** | UI conventions as a rule file (routes, overlays, gating, `?` slots) | WP-3 | Free | Real — inconsistency compounds per page |
| **A3.7** | Computed numbers carry their own explanation | WP-3 | Small | Real — retrofitting a field onto every metric |
| **A2.1** | Smoothing window on `Band` and `Ceiling` | WP-2 edit | Small | Real — invalidates every stored score |
| **A4.1** | **Resample streams to a uniform 1 Hz index grid** | WP-4 | Real | **Ruinous — re-ingest everything** |
| **A4.2** | `fixed_*` streams + anomaly records | WP-4 | Small | Ruinous — repairs unrecorded are unrecoverable |
| **A4.3** | Enumerate FIT power-source candidates, record the choice | WP-4 | Small | Real — requires re-parsing originals |
| **A4.4** | `recording_time`, `recording_stops[]`, `median_time_delta` | WP-4 | Small | Real — re-parse |
| **A4.5** | Multisport files: one file may yield N sessions | WP-4 | Small | Real |
| **A5.1** | TSS duration term is recording time — stated and tested | WP-5 | Free | Real — every stored load is wrong |
| **A5.2** | Compute *both* power load and HR load; show the counterfactual | WP-5 | Small | Small |
| **A5.3** | HRSS by per-sample integration (not single-average) | WP-5 | Small | Real — reload history |
| **A5.4** | Time-in-zone + Treff polarization index | WP-5 | Small | Small |
| **A5.5** | Metric artefacts pin the zone model, not just the anchor version | WP-5 | Free | Real — silent re-derivation |
| **A7.1** | Alignment offset is a real input to scoring, not cosmetic | WP-7 | Small | Small |
| **R1–R6** | Reserved enum values and columns, no behavior | various | Free | Real — migrations |

---

## 2. Act now — WP-3

### A3.1 · Predicted load and intensity on a planned session

**Do it in:** WP-3 · **Cost:** Small · **If we skip it:** small to add later, but the
calendar, the week rail and every agent proposal are missing their most useful number
until it exists.

**What.** A pure domain function that answers "how hard is this planned session" from the
frozen intent alone.

```python
# backend/app/domain/prediction.py  (new module, pure — no I/O)

@dataclass(frozen=True, slots=True)
class PredictedLoad:
    """What a prescription is expected to cost, from the intent alone."""

    load: float                       # TSS-equivalent
    intensity_factor: float           # planned NP / FTP
    duration_s: int
    basis: Channel                    # POWER — the only basis the MVP predicts from
    anchor_version_id: uuid.UUID      # the FTP version it resolved against
    coverage: float                   # fraction of duration that carried a power target


def predict_endurance_load(
    workout: EnduranceWorkout,
    anchors: Mapping[AnchorType, AnchorVersion],
) -> PredictedLoad | None: ...


@dataclass(frozen=True, slots=True)
class PredictedVolume:
    """The strength analogue. Not a load — a different axis (see note below)."""

    volume_load_kg: float | None      # Σ sets × reps × load, when load is in kg
    total_sets: int
    coverage: float                   # fraction of sets whose load is in kg


def predict_strength_volume(workout: StrengthWorkout) -> PredictedVolume: ...
```

Algorithm for the endurance case, exactly:

1. `flatten(workout)` → flat steps.
2. Expand to a **1 Hz array of prescribed watts**. For a steady step, `n` samples at the
   target midpoint. For a ramp, linear interpolation from the start midpoint to the end
   midpoint. **Ranges reduce to their midpoint** — state this in the docstring.
3. Resolve a `PercentOfAnchor` power target against the pinned FTP anchor version.
   `AbsoluteRange` targets are used as-is.
4. Steps with **no power target** contribute their duration to the denominator and
   **zero watts** to the series. Record the covered fraction in `coverage`.
5. `np = normalized_power(series)` (A3.2), `IF = np / ftp`,
   `load = duration_s × IF² / 36`.
6. Return `None` when `coverage == 0`, when the workout has a distance-based step
   (no duration to integrate over), or when no FTP anchor is pinned.

**Why.** Three things need it and none of them can be built without it: the calendar card
and week rail cannot show planned-vs-actual load; WP-6 matching scores intensity against
"the prescribed dominant target" and currently has no single number to compare; and WP-8's
`propose_plan_change` cannot state the load consequence of its own diff, which is the
single most useful sentence a proposal can contain.

It is a **pure function of the frozen intent and its pins**, so it is computed on read and
never stored — the same reasoning as `app/domain/zones.py` ("zones are always computed,
never stored"). No migration, no versioned artefact, no cache invalidation.

**Note on strength.** `PredictedVolume` is deliberately *not* a load and must never be
added to `PredictedLoad`. Volume load (kg) and TSS are different quantities on different
axes; v2 §5.4 and §8.3 forbid summing them. Keep them in separate fields, render them in
separate columns, and never total them. See D-item 3 in §9 for the one place this rule
needs qualifying.

**Done when.**
- Unit tests: a flat 60 min at 100 % FTP → `IF == 1.0`, `load == 100.0` (±0.5).
- `4 × 4 min @ 105 % / 4 min @ 50 %` with a 15 min warm-up and cool-down produces a load
  matching a hand-computed fixture committed alongside it.
- A workout with a cadence-only step returns `coverage < 1.0` and a documented load.
- A distance-based workout returns `None`.
- Hypothesis: `predict_endurance_load` never raises on any tree `flatten` accepts.

### A3.2 · `normalized_power()` lives in the domain, and plan and actual share it

**Do it in:** WP-3 · **Cost:** Small · **If we skip it:** two implementations that disagree
by a few percent, discovered during WP-7 when a scored session disagrees with its own plan.

**What.** Create `backend/app/domain/metrics.py` now with one function:

```python
def normalized_power(watts: Sequence[float], *, sample_hz: int = 1) -> float:
    """Coggan normalized power: 30 s rolling mean, 4th power, mean, 4th root.

    The rolling window is 30 samples at 1 Hz. Leading samples use a shorter
    window rather than being dropped. Requires a *uniformly sampled* series
    (see addendum A4.1); over irregular samples the result is meaningless.
    """
```

Plain Python over a list of floats. A 4-hour ride is 14 400 samples — this does not need
polars, and pulling polars forward from WP-5 is not the point. WP-5 may re-implement the
body over a polars/NumPy frame **behind this exact signature**, and must keep passing the
same fixtures.

**Why.** A3.1 needs NP for the plan; WP-5 needs NP for the recording. If the plan-side
number is computed by a different route — a closed-form integral over step midpoints, say
— it will differ from the actual-side number for short intervals, because the 30 s rolling
mean blends work and recovery in a way an unsmoothed integral does not. That difference
shows up as a systematic bias in every adherence score for interval sessions, in the
direction of "you did less than planned", and it would be very hard to find. Feeding both
sides through one function makes the class of bug impossible.

**Done when.** Fixture tests pass for: a constant series (NP == mean), a square-wave
interval series against a hand-computed value, and a series shorter than the window.
`predict_endurance_load` calls it. Docstring names Coggan and states the window.

### A3.3 · Week aggregates: per-discipline rows and honest coverage

**Do it in:** WP-3 · **Cost:** Small · **If we skip it:** small; but the week rail is the
calendar's most valuable region and building it twice is waste.

**What.** Extend `PlanWeekRead` (`backend/app/api/schemas/plan.py`):

```python
class PlanWeekDisciplineRead(BaseModel):
    discipline: Discipline
    session_count: int
    planned_duration_s: int
    planned_load: float | None        # null when nothing in the row is predictable
    total_sets: int | None            # strength only

class PlanWeekRead(BaseModel):
    ...  # existing fields unchanged
    planned_load: float | None
    #: How many sessions contributed to `planned_load`, and how many could not.
    #: Never present a total without its coverage — a week of 6 sessions where
    #: 2 are predictable must not read as a light week.
    load_sessions_counted: int
    load_sessions_uncounted: int
    by_discipline: list[PlanWeekDisciplineRead]
```

On the frontend, build the week rail component with **optional slots already present** for
the values that arrive at WP-5 and WP-7 — completed duration, completed load, and the
per-day completion state. Do **not** add those fields to the API schema yet (the build plan
reserves *schema* fields, not response fields, and a wall of nulls is contract noise). The
component takes them as optional props; the page passes `undefined` until the data exists.

**Why.** `load_sessions_uncounted` is the item that matters. A total computed from a subset
that does not say so is the exact failure v2 §5 calls out ("missing data means not
assessed, never failed"). It is also the shape every later aggregate needs — weekly
achieved-vs-target distribution, the totals page — so establishing it once here sets the
convention.

**Done when.** A week with one endurance session (predictable), one strength session (not
load-predictable) and one endurance session with a cadence-only step returns a
`planned_load` covering exactly the sessions that had one, with `load_sessions_uncounted
== 2`. The UI renders the count, not just the total.

### A3.4 · The zone ramp becomes 7 canonical stops, and purple is reserved

**Do it in:** WP-3 · **Cost:** Free · **If we skip it:** Real — a repalette across the
session chart, zone table, histogram, calendar sparkline, week rail and workout profile.

**What.** `frontend/app/globals.css` currently declares a 5-stop *prescription intensity*
ramp (`--color-zone-rest`, `--color-zone-2` … `--color-zone-5`). WP-5 will need a **7-stop
model-zone** ramp for `coggan_7`, and the two must be the same visual language or the
calendar sparkline and the zone table will disagree about what "hard" looks like.

Make the 7 model zones canonical and alias the prescription ramp onto them:

```css
/* --- Zone ramp -------------------------------------------------------- */
/* The canonical scale is the 7-zone power model (coggan_7). Every zone
   surface in the app — profile bars, time-in-zone tables, histograms, the
   calendar sparkline, the 30 s power fill — reads from these seven and
   nothing else. Cool at the bottom, warm at the top, monotonic in
   perceived intensity.

   HR (lthr_5) has five zones and maps onto the SAME ramp so that a heart-rate
   chart and a power chart mean the same thing at a glance:
     HR Z1→zone-1, Z2→zone-2, Z3→zone-4, Z4→zone-5, Z5→zone-7.
   The rule is: first and last zone always take the ramp endpoints, the rest
   spread evenly. Any future model follows it. */
--color-zone-1: #667284; /* recovery      (was --color-zone-rest) */
--color-zone-2: #4a8fc7; /* endurance                             */
--color-zone-3: #56a36b; /* tempo                                 */
--color-zone-4: #d19a3e; /* threshold                             */
--color-zone-5: #e0603c; /* VO2max                                */
--color-zone-6: #c9414a; /* anaerobic     — NEW, needs sign-off   */
--color-zone-7: #8f2f3f; /* neuromuscular — NEW, needs sign-off   */
```

**Purple is spent and stays spent.** `--color-coach*` (#b49bff) marks agent-written text,
and `--color-status-over` (#a78bfa) marks an over-target verdict. Both are load-bearing:
invariant 7 requires interpretive content to be *visually distinguishable* everywhere it
lands. So the top training zone must **not** be purple, and when WP-5 adds fitness/fatigue
series they must not be purple either — pick from the accent-blue and a warm neutral, and
record the choice.

The two new hexes are proposals; have them checked against `--color-card` (#131519) for
contrast before they land, and against the mockup
(`docs/ui_mockups/Training App.dc.html`). The **contract** — seven canonical stops, one
ramp for all models, purple reserved — is the binding part, not the exact values.

**Why.** A 40-pixel sparkline on a calendar card conveys a session's intensity
distribution with no reading at all, but only if it inherits a colour vocabulary the
reader already learned from the zone table. That only works if there is exactly one ramp.
Establishing it costs nothing today; six chart types later it is a day of coordinated
churn plus a visual-regression sweep.

**Done when.** The workout profile preview in the WP-3 builder renders from
`--color-zone-*`. No component defines a zone colour locally. A comment in `globals.css`
states the purple reservation and the HR mapping rule.

### A3.5 · The session sheet shows resolved targets and names the anchor version

**Do it in:** WP-3 · **Cost:** Small · **If we skip it:** small, but every session sheet
built before it gets reworked.

**What.** In the session sheet, render each step's target **both** ways: as prescribed
(`88–93 % FTP`) and as resolved (`220–232 W`), with a hover or footnote naming the anchor
version it resolved against — its value, its provenance (`tested` / `estimated` /
`assumed` / `athlete_reported`) and its effective date.

**Why.** `SessionIntent.pinned_anchor_versions` already makes this exactly correct and
already refuses an intent whose prescription refers to an unpinned anchor (D49). The pin
is the product's most distinctive invariant and it is currently invisible. Showing the
resolved watts is also simply what the athlete needs on the road; showing the provenance
is what makes an `estimated` FTP legible as an estimate rather than as a fact, which is
v2 §2's "confidence intervals are *used*" made concrete at the cheapest possible point.

**Done when.** A session pinned to an `estimated` FTP renders its resolved watts and
labels the anchor as estimated with its effective date. Changing the current FTP does not
change what the sheet displays for that session.

### A3.6 · UI conventions, written down once

**Do it in:** WP-3 · **Cost:** Free · **If we skip it:** Real — divergence compounds per
page and is never worth fixing retroactively.

**What.** Create `.claude/rules/frontend-ui-conventions.md` with
`paths: frontend/**` frontmatter, stating:

1. **Deep-link what a person would bookmark; keep transient state out of the URL.**
   Sub-views that survive a reload are real routes (`/sessions/{id}`,
   `/sessions/{id}/power`), not client-side tab state. Calendar position lives in the query
   string (`/plan?week=2026-08-03`). Modal, dropdown, hover and selection state never
   touch the URL.
2. **Three overlay tiers, chosen by what the overlay is for.** An *inline panel anchored
   under its trigger* for filters and toggles; a *centred modal* for editing one record;
   a *route* for anything worth linking to. A fourth exists for later: a **floating,
   draggable window** (not a modal) for the map on the session page, because the athlete
   needs the map and the chart visible at the same time.
3. **Empty states name the missing input and the action that supplies it.** Not "No data
   yet" but "Add an FTP anchor to see power zones" with the control beside it. This is
   v2 §10's "every refusal states what is missing and how to unlock it" as a component
   rule, and it is how the confidence ramp (v2 §13) becomes visible instead of being
   documentation.
4. **Metric grids hold their positions.** A missing value renders a `?` (or `—`) in its
   fixed slot; it never collapses the grid and never reflows its neighbours. A reader who
   learned where a number lives must find it there every time. The placeholder carries the
   reason on hover — which is exactly how WP-7's `not_assessed(reason)` axis result should
   render, so the two are one component.
5. **Numerals are monospace.** Already the rule in `globals.css`; restate it here so it
   survives the next component.
6. **One dark scheme, declared** (already decided; the decision entry is still owed —
   see §9).

**Why.** Each of these is a decision that is free the first time and contested every time
after. The rule file is loaded automatically for `frontend/**` work, so it binds the next
session without anyone remembering to say so.

**Done when.** The file exists, and the WP-3 calendar, session sheet and workout builder
conform.

### A3.7 · A computed number carries its own explanation

**Do it in:** WP-3 (establish), WP-5 (apply) · **Cost:** Small · **If we skip it:** Real —
adding a field to every metric artefact after the fact, plus a UI pass.

**What.** Introduce a small, boring value object in the domain and attach it to the first
computed number the UI shows — predicted load.

```python
@dataclass(frozen=True, slots=True)
class MetricExplanation:
    """Why a number is the number. Rendered next to it, not in a wiki."""

    formula: str          # "TSS = duration_s × IF² / 36"
    inputs: Mapping[str, str]   # {"FTP": "250 W (estimated, 2026-06-01)", ...}
    assumptions: tuple[str, ...]  # ("target ranges reduced to their midpoint", ...)
    citation: str | None  # "Allen & Coggan, Training and Racing with a Power Meter"
```

Every derived value the API returns carries one. WP-5 attaches it to each metric artefact
alongside the trust level and the anchor version.

**Why.** v2 §10 already requires "contextual education on every metric" and v2 §8.3 already
requires every metric to carry a trust level and a decision-authority level. Neither has a
work package. The reference product treats in-place methodology text as its main
differentiator against proprietary competitors, and the observation worth taking is
structural rather than editorial: **the explanation is data attached to the artefact, not
copy attached to the page.** That is the only version that survives a metric being
rendered in three places, and it is the only version an MCP tool can return to the coaching
agent so that the agent cites the same facts the screen shows.

Establishing the type now costs one file. Retrofitting it means touching every artefact.

**Done when.** `GET /api/v1/plan/week` (or the session detail) returns predicted load with
its explanation, and the session sheet renders it. The pattern is documented in
`backend/app/domain/` so WP-5 follows it without being asked.

---

## 3. Small back-edits to WP-2 code — land them with WP-3

### A2.1 · Success criteria declare their smoothing window

**Do it in:** WP-2 code, now · **Cost:** Small · **If we skip it:** Real — every score
computed before it is silently non-comparable with every score after.

**What.** Add a smoothing window to the two criteria that judge a channel against a
threshold, in `backend/app/domain/criteria.py`:

```python
@dataclass(frozen=True, slots=True)
class Band:
    channel: Channel
    low: float
    high: float
    #: Seconds of trailing rolling mean applied to the channel before it is
    #: compared to the band. 0 means raw samples. Power is spiky at 1 Hz and a
    #: raw comparison scores a perfectly-executed threshold interval at ~60 %
    #: time-in-band; 30 s is the conventional window for steady work.
    smoothing_s: int = 30


@dataclass(frozen=True, slots=True)
class Ceiling:
    channel: Channel
    limit: Limit
    max_seconds_above: int
    smoothing_s: int = 0   # a ceiling is about excursions; smoothing hides them
```

Then set it **explicitly per purpose template** in
`backend/app/resources/purpose_templates.json` rather than relying on the default — the
default exists so old JSON parses, not so templates can be vague.
Suggested starting values, to be reviewed by whoever owns the training semantics:

| Purpose | `Band.smoothing_s` | Reasoning |
|---|---|---|
| `endurance`, `tempo`, `sweet_spot`, `threshold` | 30 | steady work; the conventional window |
| `vo2max` | 10 | 3–5 min efforts; 30 s eats the on-ramp |
| `anaerobic`, `neuromuscular` | 3 | efforts shorter than the window itself |
| `recovery` | 30 | paired with a `Ceiling` at `smoothing_s = 0` |

**Why.** Time-in-band is meaningless without a stated averaging window. On raw 1 Hz power,
a rider holding a perfect 250 W average oscillates ±40 W with every pedal stroke and
scores far below a rider on a smart trainer in ERG mode doing identical physiological
work. The criterion would be measuring the equipment, not the execution. The window must
live **on the criterion**, because it is part of what was promised at planning time and it
is frozen with the rest of the intent — putting it in the scoring engine instead would let
a scoring-engine change silently rewrite what old sessions were judged against, which
invariant 1 forbids.

Doing this now is a default value on two dataclasses plus a templates-JSON edit. Doing it
after WP-7 means every stored score was computed against an undeclared window and cannot be
compared with any score after.

**Done when.** `Band` and `Ceiling` round-trip `smoothing_s` through JSON; the templates
file sets it for every purpose that uses the criterion; `test_domain_templates` asserts no
template relies on the default; the docstring states the units and what 0 means.

---

## 4. WP-4 — the irreversible window

This is the section where lateness is expensive. Everything here changes the shape of what
gets written to `data/streams/` and to the session row. Once real training data exists,
each of these becomes a re-ingest of every original file — recoverable, because
`data/originals/` is immutable and complete, but a day of work plus a full recompute
cascade through every derived artefact.

**Read this section before writing the parquet writer.**

### A4.1 · Resample every stream to a uniform 1 Hz index grid

**Do it in:** WP-4 · **Cost:** Real · **If we skip it:** ruinous.

**What.** The build plan (WP-4.1) specifies the parquet schema as `t` (UTC), `power`, `hr`,
`cadence`, `speed`, `elevation`, `temp`, `lat`, `lon`. Change the contract to:

> **Every stream is resampled to a uniform 1 Hz grid before it is written.** Row `i` of
> every column describes the same instant. The frame carries `t` as the *original* device
> timestamp per row where one exists (null where a sample was interpolated), plus
> `t0` (the grid origin, UTC) in the parquet metadata. Row index is the addressing unit
> for everything downstream: intervals, laps, detected efforts and selections are all
> `[start_index, end_index)`.

Gaps: a recording pause longer than a configured threshold (30 s, see A4.4) is **not**
filled with interpolated values — the grid continues and the channel columns are null
across the gap. A gap is a hole in the data, not a period of zero watts, and the two must
never be confused. Interpolation is for sub-threshold dropouts only, and every
interpolated region is recorded (A4.2).

**Why.** Four separate downstream capabilities are either correct-and-cheap or
wrong-and-expensive depending on this one decision:

1. **NP is wrong over irregular samples.** WP-5's "30 s rolling mean" is a 30-*sample*
   window. Over a file that samples at 1 Hz while moving and 4 s while stopped, a
   30-sample window is not 30 seconds and the 4th-power weighting amplifies the error.
   Same for time-in-zone, which is a sum of `Δt`, and for any duration-based criterion.
2. **Alignment (WP-5.2) is index arithmetic on a grid** and becomes timestamp arithmetic
   with interpolation without one.
3. **Prefix-sum arrays make range queries O(1).** Mean power, work, elevation gain and
   duration over any selection are one subtraction each — which is what makes both the
   section-selection feature (v2 §8.1) and later effort detection tractable. Prefix sums
   require uniform spacing.
4. **Chart↔map linked brushing costs nothing on a grid.** Hover state is a single integer
   and both widgets are pure functions of it: no spatial join, no timestamp matching, no
   interpolation. Off a grid, every hover becomes a binary search plus interpolation
   across four series, and the map→chart direction is materially worse. Route intelligence
   with chart↔map hover sync is **MMF-1, the explicitly stated top feature** — this is the
   decision that determines whether it is a week or a fortnight.

**Done when.**
- A golden FIT file with irregular sampling and a 2-minute pause produces a frame whose
  row count equals its elapsed seconds, with nulls across the pause.
- A property test asserts every column in a written frame has identical length.
- `normalized_power()` (A3.2) is called with the resampled power column and its docstring's
  "requires uniform sampling" precondition is satisfied by construction.
- The resampling rule (nearest / linear / hold, per channel) is documented per channel —
  `lat`/`lon` and `elevation` interpolate linearly; `power`, `hr`, `cadence` hold or
  interpolate short gaps; a categorical channel holds.

### A4.2 · Keep the raw stream, write the cleaned one, record every repair

**Do it in:** WP-4 · **Cost:** Small · **If we skip it:** ruinous — an unrecorded repair
is indistinguishable from a measurement.

**What.** For every channel that gets cleaned, write **both** the raw column and the
cleaned column (`power` / `power_fixed`, `hr` / `hr_fixed`, `elevation` /
`elevation_fixed`). All analysis consumes the `_fixed` columns. Alongside, store an
**anomaly record** for every region that was substituted:

```
anomaly(recording_id, channel, start_index, end_index, kind, substituted_value, at)
  kind ∈ {gap_interpolated, spike_clipped, dropout_held, resampled_only}
```

**Why.** WP-4 as planned is all-or-nothing: a file either validates and is ingested, or it
is quarantined. But real files are almost never cleanly one or the other — a 3-second power
spike to 1 900 W from a dropped magnet, a 40-second HR dropout, a barometric step change
through a tunnel. Ingest either passes those through (corrupting every derived value
silently) or repairs them (corrupting the audit trail silently). The anomaly record is the
third option, and it is exactly v2's "provenance everywhere" applied one level down: a
repaired sample is a derived value, and derived values record what they came from.

This also converts a class of support question — "why does this ride say 1 900 W" — into a
visible fact on the chart. It costs one small table and a few lines in the cleaner.

**Done when.** A synthetic recording with a known spike and a known dropout produces two
anomaly rows with correct index ranges; the raw column still contains the spike; the
`_fixed` column does not; the session detail page can render the repaired regions.

### A4.3 · Enumerate the power-source candidates, record which one was used

**Do it in:** WP-4 · **Cost:** Small · **If we skip it:** Real — needs a re-parse of
originals, which we can do, but the ambiguity is invisible until someone notices two
different numbers for the same ride.

**What.** A FIT file frequently carries more than one plausible power source: a crank meter,
pedal meter, a smart trainer, and a device-estimated field. Record on the recording row:

```
power_source_candidates: list[str]   # every plausible field found in the file
power_source: str                    # the one that produced the `power` column
power_source_rule: str               # why — "device_info priority", "only candidate"
```

Do the same for HR when a file carries both a strap and a wrist source.

**Why.** v2 §7.2 specifies per-channel best-source resolution with provenance, but scopes it
to *multiple recordings* of one session. This is the same ambiguity **inside a single
recording**, and it is more common than the multi-recording case — an indoor ride with a
power meter and a smart trainer produces two power traces that can differ by 15 %. Choosing
silently makes a number unexplainable. This is parse-time metadata, so it is nearly free at
WP-4 and requires re-parsing every original later.

**Done when.** A golden FIT file with two power fields records both candidates and the rule
that chose between them; the session detail header can show which meter the numbers came
from.

### A4.4 · Store recording time, recording stops and sample regularity

**Do it in:** WP-4 · **Cost:** Small · **If we skip it:** Real — WP-5's load numbers depend
on it, so a re-parse plus a rescore.

**What.** On the session (or recording) row:

- `elapsed_time_s` — last timestamp minus first.
- `recording_time_s` — **elapsed minus every gap longer than 30 s.** This is the duration
  term for load (A5.1).
- `recording_stops` — the list of `[start_index, end_index)` gaps that were subtracted.
- `median_time_delta_s` — the median spacing of the original samples. A one-number answer
  to "how irregular was this file", which is the first thing you want when a derived value
  looks wrong.
- `moving_time_s` — retained for display, **not** used for load.

**Why.** These four are cheap at parse time and impossible to reconstruct from a resampled
frame afterwards. `recording_time_s` in particular is load-bearing: see A5.1.

**Done when.** A recording with a 10-minute coffee stop reports `elapsed > recording_time`
by ~600 s, with one entry in `recording_stops`.

### A4.5 · One file may produce more than one session

**Do it in:** WP-4 · **Cost:** Small · **If we skip it:** Real — the ingest pipeline's
cardinality is baked into the dedup logic and the quarantine flow.

**What.** WP-4.2 assumes one file → one session. Make the parse step return a **list** of
sessions and record `file_sport_index` (the ordinal of the sport within the file) on each.
Behavior for the MVP can stay trivial — a single-sport file yields a one-element list — but
the pipeline's shape, the dedup key (`sha256 + file_sport_index`) and the quarantine record
must accommodate N.

**Why.** Multisport FIT files (a brick session, a multisport activity mode, any
triathlon-capable head unit set to multisport) put several sessions in one file. We train
cycling and strength, so this is not urgent — but the cardinality assumption reaches into
the hash-based duplicate check and the `originals/` naming, and those are the two things
hardest to change once files exist. Making the pipeline `1 → N` while it is `1 → 1` in
practice costs almost nothing.

**Done when.** The parser signature returns a sequence; the dedup key includes the sport
index; a single-sport file produces one session and an existing test still passes.

---

## 5. WP-5 — metrics and session analysis

### A5.1 · The duration term in TSS is recording time — say so, and test it

**Do it in:** WP-5 · **Cost:** Free · **If we skip it:** Real — every stored load is wrong
by the length of your coffee stops, and cross-checking against any other platform fails.

**What.** The build plan gives `TSS = (dur_s × NP × IF)/(FTP×3600)×100` without saying what
`dur_s` is. Fix it: **`dur_s` is `recording_time_s`** (A4.4) — elapsed minus gaps over 30 s.
Not moving time, and not elapsed-minus-coasting. Put it in the docstring with the reasoning
and pin it with a fixture test.

**Why.** This was verified numerically against the reference platform: a 1:35:37 ride at
NP 141 W with FTP 200 W gives IF 0.705 and TSS 79.2 using elapsed/recording time, matching
the displayed 79 exactly; using elapsed-minus-coasting gives 73.9, which does not match. The
distinction is not cosmetic — coasting on a descent is part of the ride's physiological
cost in this model, and a paused recording is not.

It also matters for a strategy the delivery plan depends on: MVP and MMP run side by side
with intervals.icu, verifying our numbers against theirs before we cut over. That
verification only works if we match their conventions deliberately. Appendix A lists the
full set.

**Done when.** The formula's docstring names the duration term and why; a fixture test uses
the worked example above and asserts 79 ± 0.5.

### A5.2 · Compute both power load and HR load; show the counterfactual

**Do it in:** WP-5 · **Cost:** Small · **If we skip it:** Small.

**What.** For every session with both power and HR, compute **both** load values and store
both, plus which one was selected and why:

```
power_load: float | None
hr_load: float | None
training_load: float          # the selected one
load_basis: "power" | "hr"
load_basis_rule: str          # "power available and preferred for cycling"
```

Render it on the session page as a sentence: *"Load 79, from power. Had power been
unavailable, the HR model would have given 75."*

**Why.** Three things fall out of it and none is available otherwise.

It **calibrates the HR model**. Strength sessions, rides with a dead power meter, and any
"basic tier" activity (v2 §2) will only ever have HR load. The only way to know whether that
number can be trusted is to watch it track power on the days both exist. Storing only the
selected value throws away the comparison permanently.

It is **our trust doctrine made concrete**. v2 §8.3 requires every metric to carry a trust
level and a decision-authority level; a load computed from HR is materially less trustworthy
than one computed from power, and this is the cheapest possible place to make that visible
rather than documentary.

And it is **the auditability principle at its clearest** — showing what we used *and* what
the alternative would have said. That is a differentiator we have already committed to in
v2 §10 and have not yet scheduled anywhere.

**Done when.** A session with power and HR stores both loads; the session page renders the
counterfactual; a session with HR only stores `power_load = null` and selects HR with a
stated rule.

### A5.3 · HRSS by per-sample integration

**Do it in:** WP-5 · **Cost:** Small · **If we skip it:** Real — reload and rescore history.

**What.** Implement HR load as HRSS with a **per-sample integration**, not a
single-average-HR form. Exact formulas and constants in Appendix A.

**Why.** There are two HRSS variants in circulation, and the difference is not academic. The
widely-copied current form computes TRIMP once from the *average* HR of the whole session.
By Jensen's inequality, `e^(k·x̄) ≤ mean(e^(k·xᵢ))` — so the single-average form
**systematically under-reports variable-intensity sessions**, which is precisely the class
of session where HR load matters most (intervals, and every strength session, where HR
swings between sets). The per-sample integration is the physiologically defensible one and
is what "time spent at each HR value" actually means.

On a 1 Hz grid (A4.1) the per-sample form is a one-line sum, so there is no cost argument
for the wrong one.

Note the asymmetry in Appendix A: the activity-level HR reserve is clamped at zero, the
threshold-level one conventionally is not. If `LTHR < resting HR` the result flips sign.
Guard it and return `not_assessed` rather than a negative load.

**Done when.** The worked example in Appendix A reproduces to 0.1; a square-wave HR series
and a constant-HR series with the same mean produce **different** HRSS, with the
square wave higher; `LTHR <= resting_hr` returns `not_assessed` with a reason.

### A5.4 · Time-in-zone and the polarization index

**Do it in:** WP-5 · **Cost:** Small · **If we skip it:** Small.

**What.** Beyond per-zone time (already in WP-5.1), add the three-zone collapse and the
Treff polarization index:

```
Z_easy     = time in zones 1–2
Z_moderate = time in zones 3–4
Z_hard     = time in zones 5–7
PI = log10( (Z_easy / Z_moderate) × Z_hard × 100 )   # fractions of total time
```

Compute it per session and per week. `PI > 2.0` is the conventional threshold for
"polarized"; a typical 80/5/15 split gives 2.38, a pyramidal 80/15/5 gives 1.43.

**One rule that must not be got wrong:** when totalling time-in-zone across sessions, use
**exactly one channel per session** (power *or* HR), chosen by a stated priority. Summing a
session's power zones and its HR zones double-counts its duration. State the rule where the
aggregation happens.

**Why.** It is a dozen lines over data WP-5 already computes, and it is the first number in
the product that describes *training quality* rather than training quantity — which is what
v2 §5.2's "achieved-vs-target intensity distribution as a first-class deviation" needs, and
what makes the week rail worth looking at. The formula is verified: observed 72.6 / 19.4 /
8.0 gives 1.4762 → 1.48, matching the reference exactly.

**Done when.** The verification case above reproduces to 2 decimal places; a weekly
aggregate over mixed power/HR sessions counts each session's duration exactly once.

### A5.5 · A metric artefact pins the zone model, not just the anchor version

**Do it in:** WP-5 · **Cost:** Free · **If we skip it:** Real — silent re-derivation of
history.

**What.** WP-5.1 says each metric artefact records its `anchor_version_id` inputs. For any
zone-derived metric (time-in-zone, zone distribution, PI) that is not sufficient: record
**`zone_model`** too.

**Why.** `app/domain/zones.py` correctly computes zones rather than storing them, and
`DEFAULT_ZONE_MODEL` is a constant map today, so `(anchor version) → zones` is currently
deterministic. It stops being deterministic the moment we add a second power model, custom
zone boundaries, or a per-athlete model preference — all of which v2 §3 anticipates ("the
zone model in use is declared and recorded alongside the anchor"). At that point every
historical time-in-zone silently re-derives against the new boundaries, which invariant 1
forbids and which no test would catch.

The pin belongs on the *metric*, not on `SessionIntent` — prescriptions target percentages
of anchors, not zones, so the intent has nothing to pin today. If a future target or
criterion ever names a zone (`Z2`, or `% of the power-duration curve` — see R2), the intent
must pin the model at that point too.

**Done when.** Every zone-derived artefact stores `zone_model`; recomputing under a
different model produces a new version rather than mutating the old one.

---

## 6. WP-6 and WP-7

### A7.1 · The alignment offset is a real input, not a cosmetic slider

**Do it in:** WP-7 (design at WP-5) · **Cost:** Small · **If we skip it:** Small.

**What.** When the planned structure is aligned to a recording (WP-5.2), expose a
**time-offset control** that slides the planned trace along the recording's time axis, and
feed the chosen offset back into alignment and therefore into the adherence and pacing axes.
Store it as part of the alignment artefact.

**Why.** The single most common alignment failure is a constant offset: the athlete started
recording three minutes before starting the workout, or the warm-up ran long. Without a
correction, the whole session mis-aligns and every work step scores badly for a reason that
has nothing to do with execution — and WP-5.2's confidence gate will exclude the steps
rather than score them, which is better but still wrong.

Worth knowing: the reference platform has exactly this slider and it is **purely cosmetic**
— it cannot affect their compliance number, because their compliance is a scalar load ratio
with no notion of steps. Ours is a per-step time-in-band computation, so the same control
becomes functional at no extra cost. This is one of the places where our more expensive
design pays for itself, and it is worth taking.

**Done when.** A recording with a 3-minute lead-in aligns correctly once the offset is
applied; the offset is stored with the alignment version; changing it creates a new
alignment version and triggers a rescore through the normal path.

### A6.1 · Matching semantics — confirmations, not changes

No change is proposed to WP-6. Recording it here so nobody re-opens it: the reference
matches on same-day + same-sport only, ignores time of day, allows one planned workout per
activity, uses a loose undocumented tolerance, treats indoor and outdoor cycling as the same
sport, and does not exclude commutes — the last two being long-standing user complaints.
WP-6's ±1-day window, similarity scoring with a stated weighting, similarity floor,
`displaced` state, first-class unplanned activities and context-switched rubrics are
strictly better on every one of those axes. Build it as specified.

---

## 7. Reserve only — no behavior

Enum members and columns to add now so that later work is code rather than a migration.
**None of these gets an implementation in the MVP.** Each needs a one-line comment saying
it is reserved and which increment fills it.

| ID | Reservation | Where | Filled by |
|---|---|---|---|
| **R1** | `Channel.PACE`, `AnchorType.THRESHOLD_PACE` | `domain/workout.py`, `domain/anchors.py` | if running is ever added |
| **R2** | A third `Target` variant: `PercentOfCurve(duration_s, pct_low, pct_high)` | `domain/workout.py` | MMP — v2 §6 requires it ("% of power-duration curve where %FTP breaks down"); the union and its JSON codec should accommodate it now |
| **R3** | `weight_kg` + its provenance snapshot on the session | WP-4 row | whenever w/kg, VO2max or power profile appear — all of them depend on the weight *at the time*, and it must be pinned like an anchor |
| **R4** | `external_id`, `source` on the recording | WP-4 row | MMP vendor adapters; also the "a richer file supersedes an earlier lower-fidelity import of the same ride" merge case |
| **R5** | `session_context` enum: `training \| commute \| group_ride \| race \| event` | WP-4/WP-6 | WP-6 already switches rubric on it; reserve the values even if only `training` is produced |
| **R6** | `hr_load_model` on the athlete (`hrss` only, for now) | WP-1/WP-5 | MMP, when a second model or a per-discipline setting appears |

**A note on what is *not* reserved: per-discipline anchors.** The reference groups settings
by sport (one FTP, one zone set, one load model per sport group), and separately supports an
indoor-vs-outdoor FTP. Our `AnchorType` is global. This is the right call for cycling +
strength, and adding a discipline dimension now would complicate every WP-1 and WP-2 call
site for no MVP benefit. But it is the most likely structural change in the MMP, so:
**do not add code that assumes an `AnchorType` has exactly one current version, full stop**
— always go through the existing resolution helpers, never index a dict of anchors by type
directly at a call site.

---

## 8. Explicitly out of the MVP — do not start these

Named because the study makes them look tractable and they are still out of scope. Each is
sequenced in `docs/training-application-delivery-plan.md`.

- **PMC (fitness / fatigue / form), CTL/ATL/TSB.** MMP. Also see §9 D-item 3 — there is an
  open design question about it that should be settled before it is built, not during.
- **Power-duration curves, eFTP, critical power, W′ balance, VO2max estimates.** MMP.
  When they come: eFTP is a **percentile-curve-bank lookup, not a model fit**, which means
  it has no meaningful confidence interval and no rider-type axis — everyone who can hold
  330 W for 10 minutes gets the same number. Use it as a cheap always-available *secondary*
  estimate labelled `estimated` with a wide CI, and keep a constrained model fit as the real
  estimator. Our provenance model handles that distinction natively; the reference's cannot.
- **Automatic interval detection.** MMP, and it is gated on knowing CP, W′ and Pmax — so it
  cannot precede the power-curve work regardless.
- **Routes, maps, chart↔map brushing, segments.** MMF-1. A4.1 is the only thing that needs
  doing now, and it is being done for other reasons anyway.
- **Wellness, HRV, readiness, RPE protocol.** MMP. Two things to carry forward when it
  arrives: use **one polarity convention across every subjective scale** (the reference's
  are inconsistent between fields — mood 1 = excellent, soreness 0 = none — and it is a
  documented wart), and extend v2 §7.2's per-channel source resolution to **wellness
  fields**, because integrations rewrite roughly the trailing week on every sync and
  last-writer-wins will silently overwrite corrections.
- **Weather, availability, constraint engine, scheduled inference.** MMP.
- **Coach/multi-athlete, groups, chat, user-authored server-side scripting.** Out
  permanently. The MCP agent surface is our answer to extensibility and it is a better one
  for a single-user instance.

---

## 9. Decision-log entries to write

`docs/decisions.md` is append-only and currently ends at **D58**. Note that
`frontend/app/globals.css` already cites **D59** for the dark-only theme, but that entry has
not been written — **write it first**, then continue from D60.

Entries owed, each stating what was chosen, what it displaced, and why:

1. **The dark-only theme** (claimed as D59, unwritten). Charts dominate every screen and
   multi-series lines hold contrast better on dark neutrals; this is a charting decision,
   not a fashion one. State that there is no light theme and no toggle.
2. **Predicted load is computed, never stored** (A3.1) — same reasoning as zones. What it
   displaced: a `predicted_load` column maintained by a trigger or a service hook.
3. **How endurance and strength load relate** — the one item in this document that is a
   genuine open question rather than a task, and it should be settled deliberately.

   v2 §5.4 and §8.3 say endurance and strength load "remain on separate axes and are never
   summed". v2 §5.4 *also* requires a "unified recovery state — a discipline-agnostic daily
   recovery estimate ... an input to the constraint engine". Those two requirements are in
   tension: a discipline-agnostic recovery number is, by construction, something that
   combines disciplines.

   The reference resolves it with a mechanism worth knowing about: per-activity-type
   `ctlFactor` / `atlFactor` multipliers, which feed the *fitness* and *fatigue* averages
   **different daily inputs**. Weight training is configured at 0 % fitness / 100 % fatigue
   — so a hard gym session makes you tired without making you aerobically fitter, which is
   both true and exactly the distinction our "never summed" rule was written to protect.
   It does not sum the two onto one axis; it weights them separately per axis.

   Our current design instead builds unified recovery from hard-session counting and
   recency. That is defensible and avoids depending on a display-only metric for a
   decision — but it rejects the one mechanism with a track record in favour of an
   unvalidated heuristic, and the spec currently asserts both positions without reconciling
   them.

   **Recommendation:** scope the rule rather than dropping it. "Never summed" should bind
   the *fitness and performance* axes, where mixing genuinely destroys meaning. The
   *fatigue and recovery* axis is where a per-discipline weighted contribution is both
   defensible and needed. Write the decision either way — but write it before MMP builds
   the constraint engine on top of the ambiguity.
4. **Scoring criteria declare their smoothing window** (A2.1) — what it displaced: a
   smoothing constant in the scoring engine, rejected because a scoring-engine change would
   then silently rewrite what old sessions were judged against.
5. **The 1 Hz index grid as the stream storage contract** (A4.1) — what it displaced:
   storing device timestamps as recorded. Note the four capabilities that depend on it.
6. **Raw and cleaned streams both stored; repairs recorded as anomalies** (A4.2).
7. **The duration term in load is recording time** (A5.1) — with the worked example.

---

## Appendix A — Formulas, constants and conventions

Self-contained. Everything a developer needs to implement §5 without reading the study.
Put each formula in the docstring of the function that implements it, with its citation.

### A.1 Normalized power, intensity factor, training load

```
NP  = ( mean( rolling_mean_30s(P)^4 ) )^(1/4)
IF  = NP / FTP
TSS = (duration_s × NP × IF) / (FTP × 3600) × 100
    = duration_s × IF² / 36
```

- The rolling window is **30 samples at 1 Hz**. Leading samples use a shorter window rather
  than being dropped.
- `duration_s` is **recording time** = elapsed − gaps > 30 s. Not moving time.
- By construction one hour at FTP = 100 TSS.
- Rationale for the shape: ~30 s approximates the cardiovascular response time constant,
  and the 4th power weights hard efforts in proportion to their non-linear physiological
  cost.
- Citation: Allen & Coggan, *Training and Racing with a Power Meter*.

**Verification fixture (commit this):** duration 1:35:37 (5 737 s), NP 141 W, FTP 200 W
→ IF = 0.705, TSS = **79.2**. Reference platform displays 79.

Related, and worth computing because they are nearly free:

```
variability_index = NP / average_power
efficiency_factor = NP / average_HR
average_power     = total_joules / recording_time_s
coasting_time     = time moving ≥ 1 km/h while producing ≤ 10 W
work_kJ           = Σ P × Δt / 1000
work_above_ftp_kJ = Σ max(0, P − FTP) × Δt / 1000
```

Note `average_power` is **total work ÷ recording time**, which is *not* the average the
head unit displays. Document the difference where it is rendered or it will be reported as
a bug.

### A.2 HRSS — heart-rate training load

Banister TRIMP is the basis; HRSS rescales it so one hour at threshold HR = 100, making it
directly comparable to TSS.

```
HRr(t) = max( (HR(t) − HR_rest) / (HR_max − HR_rest), 0 )

k = 1.92 (male)   |   1.67 (female)
c = 0.64

# per-sample integration — use THIS form (A5.3)
TRIMP_activity = Σᵢ (Δtᵢ / 60) × HRrᵢ × c × e^(k × HRrᵢ)

# normalisation: TRIMP of one hour at threshold HR
HRr_LT      = (LTHR − HR_rest) / (HR_max − HR_rest)      # conventionally unclamped
TRIMP_LT_1h = 60 × HRr_LT × c × e^(k × HRr_LT)

HRSS = 100 × TRIMP_activity / TRIMP_LT_1h
```

- On a 1 Hz grid, `Δtᵢ = 1`, so the sum is one pass over the HR column.
- **Do not** substitute the session's average HR for `HRrᵢ`. See A5.3 for why.
- **Guard:** if `LTHR <= HR_rest`, `HRr_LT <= 0` and HRSS flips sign or divides by zero.
  Return `not_assessed("threshold HR is not above resting HR")`.
- The `k` coefficient appears in numerator and denominator but does not cancel, because the
  exponential is non-linear — HRSS is genuinely sex-dependent.

**Verification fixture:** male, HR_max 190, HR_rest 65, LTHR 171.25 → `HRr_LT = 0.85`,
`TRIMP_LT_1h = 166.924`. One hour at a constant `HRr = 0.70` → TRIMP 103.067 →
HRSS **61.7**.

### A.3 Time in zone and the polarization index

```
Z_easy     = Σ time in zones 1–2      (as a fraction of total)
Z_moderate = Σ time in zones 3–4
Z_hard     = Σ time in zones 5–7

PI = log10( (Z_easy / Z_moderate) × Z_hard × 100 )
```

- `PI > 2.0` is the conventional "polarized" threshold (Treff et al.).
- Reference points: 80/5/15 → 2.38; 80/15/5 → 1.43.
- **Verification fixture:** 72.6 % / 19.4 % / 8.0 % → `log10(0.726/0.194 × 0.080 × 100)`
  = 1.4762 → displays **1.48**.
- **Aggregation rule:** across sessions, use exactly one channel per session (power or HR),
  chosen by a stated priority. Never sum a session's power zones and its HR zones.

### A.4 Aerobic decoupling — conventions for when WP-5+ implements it

Not in the MVP, but the conventions are cheap to get right and impossible to compare
against anything if they are wrong:

```
ratio      = power / HR
decoupling = (ratio_first_half − ratio_second_half) / ratio_first_half × 100
```

- **Lag-shift HR by ~15 s** before pairing it with power — HR trails power changes.
- The baseline is the first half **or the first hour, whichever is shorter**.
- The over-time chart plots a **10-minute moving average** of the ratio against that
  baseline.
- **Negative decoupling is not displayed.**
- **Minutes with under 30 s of moving time are excluded** from both charts.
- Under 5 % is the conventional threshold for good aerobic durability.

### A.5 Parity checklist for running side by side with intervals.icu

The delivery plan verifies our numbers against theirs before cutting over. That only works
if we match these conventions deliberately. Each is a line in a docstring, not a task.

| Quantity | Convention |
|---|---|
| Load duration term | recording time = elapsed − gaps > 30 s |
| Average power | total joules ÷ recording time (not the device average) |
| Coasting | moving ≥ 1 km/h at ≤ 10 W |
| Recording gap threshold | 30 s |
| Decoupling HR lag | ~15 s |
| Decoupling baseline | first half, or first hour if shorter |
| Decoupling exclusions | negative values hidden; minutes with < 30 s moving time dropped |
| Zone totals across sessions | one channel per session, never the sum of all |
| Stream grid | 1 Hz, index-aligned |

---

## Appendix B — Additions to the MVP acceptance checklist

Append to `docs/mvp-build-plan.md` §3, "MVP acceptance checklist":

- [ ] A planned week shows predicted load per session and per discipline, and states how
      many sessions could not be predicted
- [ ] The session sheet shows each target both as prescribed (`88–93 % FTP`) and as
      resolved (`220–232 W`), naming the anchor version and its provenance
- [ ] Every stream frame written by ingest has identical column lengths and one row per
      elapsed second; a paused recording has nulls, not zeros, across the pause
- [ ] A file with a known power spike ingests with the spike present in the raw column,
      absent from the `_fixed` column, and recorded as an anomaly row
- [ ] A session with both power and HR reports both loads and states which was used and why
- [ ] `normalized_power()` reproduces its committed fixtures, and is the same function used
      for both planned and actual load
- [ ] The polarization-index fixture (72.6 / 19.4 / 8.0 → 1.48) passes
- [ ] No component defines a zone colour locally; every zone surface reads the shared ramp
- [ ] Every computed value the API returns carries its formula, inputs and assumptions
