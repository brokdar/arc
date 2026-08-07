# WP-3 Work Order — additions to land before WP-3 closes

**For:** the developer currently on `feat/wp-3-calendar-week-ui`.
**Scope:** twelve items. Nine are under half a day each. None changes what WP-3 delivers —
they change the *shape* of what it delivers so WP-4 through WP-7 don't have to undo it.

**Rationale for each item** is in `docs/mvp-plan-addenda.md` under the ID in brackets.
This document is the executable version: what to change, where, and how you know it works.
You do not need to read anything else to do this work.

**Ground rule:** if an item here contradicts `docs/mvp-build-plan.md`, this document wins
and the contradiction is called out. Everything else in the build plan stands.

---

## Sequencing

```
B1 ──► B2 ──► B4 ──► F3          B3, B6, F1, F4 are independent — start anywhere
        └──► B5                  F2 needs B6
```

Suggested order: **F1, F4** (free, unblock nothing but cost nothing) → **B1, B2, B5** →
**B3** → **B6, B4** → **F2, F3** → **F5** → docs.

Run `just api-sync` after **every** backend schema change and commit the result — CI fails
on drift.

---

## Backend

### B1 · `normalized_power()` in the domain  [A3.2]

**New file:** `backend/app/domain/metrics.py`

```python
def normalized_power(watts: Sequence[float], *, sample_hz: int = 1) -> float:
    """Coggan normalized power: 30 s rolling mean, 4th power, mean, 4th root.

    NP = ( mean( rolling_mean_30s(P)^4 ) )^(1/4)

    The window is ``30 * sample_hz`` samples. Leading samples use a shorter
    window rather than being dropped. Requires a **uniformly sampled** series;
    over irregular samples the result is meaningless (WP-4 guarantees the grid).

    Reference: Allen & Coggan, *Training and Racing with a Power Meter*.
    """
```

Plain Python over a list of floats. **Do not** pull in polars or numpy — they arrive with
WP-5, and 7 200 floats does not need them. WP-5 may re-implement the body over a frame
**behind this exact signature**, and must keep passing the fixtures you write here.

Also add, since B2 and WP-5 both want them and they are two lines each:

```python
def intensity_factor(np_watts: float, ftp_watts: float) -> float: ...   # NP / FTP
def training_load(duration_s: int, intensity_factor: float) -> float:
    """TSS = duration_s × IF² / 36. One hour at FTP = 100."""
```

**Tests** (`tests/unit/test_domain_metrics.py`):
- constant series → `NP == mean` (within 1e-9)
- series shorter than the window → no crash, uses what it has
- empty series → raises or returns 0.0, your call, but pin it
- **committed fixture:** duration 5 737 s, NP 141 W, FTP 200 W → `IF == 0.705` (±0.001),
  `load == 79.2` (±0.5). This is a verified real-world case; it is the anchor for every
  later load number, so give it its own test with the numbers in the docstring.
- a square-wave series and a constant series with the same *mean* produce **different** NP,
  with the square wave higher

---

### B2 · Predicted load and intensity for a planned session  [A3.1]

**New file:** `backend/app/domain/prediction.py` — pure, no I/O, no ORM.

```python
@dataclass(frozen=True, slots=True)
class PredictedLoad:
    load: float                    # TSS-equivalent
    intensity_factor: float
    duration_s: int
    anchor_version_id: uuid.UUID   # the FTP version it resolved against
    coverage: float                # fraction of duration carrying a power target


def predict_endurance_load(
    workout: EnduranceWorkout,
    anchors: Mapping[AnchorType, AnchorVersion],
) -> PredictedLoad | None: ...


@dataclass(frozen=True, slots=True)
class PredictedVolume:
    volume_load_kg: float | None   # Σ sets × reps × load, when load is in kg
    total_sets: int
    coverage: float


def predict_strength_volume(workout: StrengthWorkout) -> PredictedVolume: ...
```

**Algorithm — implement exactly this:**

1. `flatten(workout)` → flat steps.
2. Build a **1 Hz list of prescribed watts**. A steady step contributes `duration_s`
   samples at its power-target midpoint; a ramp contributes `duration_s` samples linearly
   interpolated from its start midpoint to its end midpoint.
3. Resolve a `PercentOfAnchor` power target against `anchors[AnchorType.FTP]`.
   `AbsoluteRange` targets are used as given.
4. **Ranges reduce to their midpoint.** Say so in the docstring.
5. A step with **no power target** contributes its duration as **zero watts** and does not
   count toward `coverage`.
6. `np = normalized_power(series)` → `IF` → `load` via B1.
7. Return `None` when: `coverage == 0`, **or** any step is distance-based (nothing to
   integrate over), **or** no FTP anchor is pinned.

**Why the 1 Hz expansion rather than a closed-form integral:** it makes the planned number
come out of the *same function* as the actual number will at WP-5. A closed-form integral
over step midpoints skips the 30 s rolling mean, which for short intervals inflates NP —
a systematic "you did less than planned" bias in every interval session's adherence score,
and very hard to find later.

**Strength is not a load.** `PredictedVolume.volume_load_kg` is kilograms; `PredictedLoad.load`
is TSS. They are different axes and must never be added, totalled, or rendered in the same
column. This is a spec invariant (v2 §5.4, §8.3), not a preference.

**Tests** (`tests/unit/test_domain_prediction.py`):
- 60 min steady at 100 % FTP → `IF == 1.0`, `load == 100.0` (±0.5)
- 15 min warm-up @ 55 %, `4 × (4 min @ 105 % / 4 min @ 50 %)`, 10 min cool-down @ 50 %,
  FTP 250 → commit the hand-computed expected load as a fixture
- a workout with a cadence-only step → `coverage < 1.0`, load still returned
- a distance-based workout → `None`
- no FTP pinned → `None`
- **hypothesis:** `predict_endurance_load` never raises for any tree `flatten` accepts

---

### B3 · Success criteria declare their smoothing window  [A2.1]

**Edit:** `backend/app/domain/criteria.py`

```python
@dataclass(frozen=True, slots=True)
class Band:
    channel: Channel
    low: float
    high: float
    #: Seconds of trailing rolling mean applied to the channel before it is
    #: compared to the band. 0 means raw samples. Power is spiky at 1 Hz: a raw
    #: comparison scores a perfectly-executed threshold interval far below a
    #: smart trainer doing identical physiological work, so an undeclared
    #: window means the criterion measures the equipment, not the execution.
    smoothing_s: int = 30


@dataclass(frozen=True, slots=True)
class Ceiling:
    channel: Channel
    limit: Limit
    max_seconds_above: int
    #: A ceiling is about excursions; smoothing hides them. Default raw.
    smoothing_s: int = 0
```

Validate `smoothing_s >= 0` in `__post_init__`, and round-trip it through
`band_to_json` / `band_from_json` and `criterion_to_json` / `criterion_from_json`.
**The decoder must tolerate the key being absent** and apply the default — existing rows in
dev databases must still parse. No Alembic migration is needed (criteria are stored as
tagged-union JSON).

Then set it **explicitly for every purpose** in
`backend/app/resources/purpose_templates.json`. Starting values — take these unless you
have a reason, and say so in the commit if you change one:

| Purpose | `Band.smoothing_s` | Reasoning |
|---|---|---|
| `endurance`, `tempo`, `sweet_spot`, `threshold` | 30 | steady work; the conventional window |
| `vo2max` | 10 | 3–5 min efforts; 30 s eats the on-ramp |
| `anaerobic`, `neuromuscular` | 3 | efforts shorter than the window itself |
| `recovery` | 30 | pair with its `Ceiling` at `smoothing_s = 0` |

**Why it lives on the criterion and not in the scoring engine:** the window is part of what
was promised at planning time and freezes with the rest of the intent. If it lived in the
scoring engine, a scoring-engine change would silently rewrite what already-scored sessions
were judged against — which invariant 1 forbids.

**Tests:**
- JSON round-trip with and without the key present
- `test_domain_templates`: **assert no template relies on the default** — every band and
  ceiling in the templates file states its window explicitly
- negative window rejected

---

### B4 · Week aggregates: predicted load, per-discipline rows, honest coverage  [A3.3]

**Edit:** `backend/app/services/plan.py` and `backend/app/api/schemas/plan.py`.

Add to `WeekSession` / `WeekSessionRead`:

```python
predicted_load: float | None          # null when not predictable
predicted_intensity_factor: float | None
predicted_volume_load_kg: float | None   # strength only
```

Add to `PlanWeek` / `PlanWeekRead`:

```python
planned_load: float | None
#: How many sessions contributed to `planned_load`, and how many could not.
#: Never render a total without its coverage: a week of 6 sessions where only
#: 2 are predictable must not read as a light week.
load_sessions_counted: int
load_sessions_uncounted: int
by_discipline: list[PlanWeekDisciplineRead]


class PlanWeekDisciplineRead(BaseModel):
    discipline: Discipline
    session_count: int
    planned_duration_s: int
    planned_load: float | None
    total_sets: int | None       # strength only
```

The service loads the pinned anchor versions each intent references (it already has the
ids in `pinned_anchor_versions`) and calls B2. Predicted load is **computed on read and
never stored** — it is a pure function of the frozen intent and its pins, exactly like
zones. No column, no migration, no cache to invalidate.

**Do not** add fields for completed duration, completed load, or per-day completion state.
Nothing is ingested until WP-4 and a wall of nulls is contract noise. F3 handles the UI
side of that.

**Tests** (`tests/unit/test_plan_week_api.py`):
- a week with one predictable ride, one strength session and one ride with a cadence-only
  step returns `planned_load` covering only what was predictable, with
  `load_sessions_uncounted == 2`
- `by_discipline` totals reconcile with the flat totals
- a week with nothing predictable returns `planned_load: null`, not `0`

---

### B5 · A computed number carries its explanation  [A3.7]

**Add to** `backend/app/domain/metrics.py`:

```python
@dataclass(frozen=True, slots=True)
class MetricExplanation:
    """Why a number is the number. Travels with it; not page copy."""

    formula: str                    # "TSS = duration_s × IF² / 36"
    inputs: Mapping[str, str]       # {"FTP": "250 W (estimated, 2026-06-01)"}
    assumptions: tuple[str, ...]    # ("target ranges reduced to their midpoint",)
    citation: str | None            # "Allen & Coggan, Training and Racing…"
```

Attach one to predicted load and return it from the API (`PredictedLoadRead.explanation`).
The session sheet renders it (F2).

**Why now, on this one number:** v2 §10 requires contextual education on every metric and
v2 §8.3 requires every metric to carry a trust level — neither has a work package, and both
only work if the explanation is **data attached to the artefact**, not copy attached to a
page. That is the only version that survives the number being rendered in three places, and
the only version an MCP tool can hand to the coaching agent so the agent cites the same
facts the screen shows. Establishing the type costs one dataclass now; retrofitting it means
touching every artefact WP-5 creates.

`inputs` must name the **anchor version's** value, provenance and effective date — not the
athlete's current FTP.

---

### B6 · Session detail returns resolved targets and anchor provenance  [A3.5]

**Edit:** the planned-session detail response (`app/api/schemas/planned_sessions.py`).

For each flattened step, return the target **both ways**:

```python
class ResolvedTargetRead(BaseModel):
    channel: Channel
    prescribed: str        # "88–93 % FTP"  — render-ready
    resolved_low: float | None    # 220.0
    resolved_high: float | None   # 232.0
    unit: ChannelUnit
    anchor_version_id: uuid.UUID | None
```

And once per session, the pins it resolved against:

```python
class PinnedAnchorRead(BaseModel):
    anchor_type: AnchorType
    anchor_version_id: uuid.UUID
    value: float
    unit: AnchorUnit
    provenance: Provenance        # tested | estimated | assumed | athlete_reported
    effective_date: dt.date
```

**Why:** `SessionIntent.pinned_anchor_versions` already makes this exactly correct and
already refuses an intent that references an unpinned anchor (D49). The pin is the product's
most distinctive invariant and it is currently invisible. Resolved watts are also just what
the athlete needs on the road, and showing the provenance is what makes an `estimated` FTP
read as an estimate rather than a fact.

**Test:** a session pinned to an old anchor version renders that version's watts. Appending
a **new** FTP anchor does not change the session's resolved targets.

---

## Frontend

### F1 · Seven canonical zone stops, aligned with the backend's `coggan_7`  [A3.4]

Two things are currently misaligned and both are cheap to fix now, expensive after WP-5
adds a zone table, a histogram, a sparkline and a stream fill.

**(a) Extend the ramp to seven stops.** `frontend/app/globals.css` declares five
(`--color-zone-rest`, `--color-zone-2` … `--color-zone-5`). WP-5 needs seven for
`coggan_7`. Rename to a positional scale and add two:

```css
/* --- Zone ramp -------------------------------------------------------- */
/* The canonical scale is the backend's 7-zone power model (coggan_7). Every
   zone surface — profile bars, zone tables, histograms, the calendar
   sparkline, the 30 s power fill — reads these seven and nothing else.

   HR (lthr_5) has five zones and maps onto the SAME ramp, so a heart-rate
   chart and a power chart mean the same thing at a glance:
     HR Z1→zone-1, Z2→zone-2, Z3→zone-4, Z4→zone-5, Z5→zone-7.
   Rule: first and last zone take the ramp endpoints, the rest spread evenly. */
--color-zone-1: #667284; /* recovery       (was --color-zone-rest) */
--color-zone-2: #4a8fc7; /* endurance                              */
--color-zone-3: #56a36b; /* tempo                                  */
--color-zone-4: #d19a3e; /* threshold                              */
--color-zone-5: #e0603c; /* VO2max                                 */
--color-zone-6: #c9414a; /* anaerobic      — NEW, needs sign-off   */
--color-zone-7: #8f2f3f; /* neuromuscular  — NEW, needs sign-off   */
```

Update the `--color-chart-*` aliases below it to match.

> **Purple is spent and stays spent.** `--color-coach` (#b49bff) marks agent-written text
> and `--color-status-over` (#a78bfa) marks an over-target verdict; both are load-bearing,
> because invariant 7 requires interpretive content to be visually distinguishable wherever
> it lands. So the top training zone must not be purple — and when WP-5 adds fitness/fatigue
> series, they must not be purple either. Note this in the CSS comment.

The two new hexes are proposals. Check them against `--color-card` (#131519) and against
`docs/ui_mockups/Training App.dc.html` before landing. The **contract** — seven stops, one
ramp for every model, purple reserved — is the binding part, not the exact values.

**(b) Use the real Coggan boundaries.** `frontend/lib/workout-profile.ts` currently maps
intensity to a tone with `0.45 / 0.75 / 0.88 / 1.05`, and its own comment says these are
"the Coggan-ish ones the mockup's colours imply, not the backend's zone model". The
backend's `coggan_7` (`backend/app/domain/zones.py`) uses `0.55 / 0.75 / 0.90 / 1.05 /
1.20 / 1.50`. So a step at 52 % FTP paints as rest on a card but is Z1 Recovery in the
backend, and Z6/Z7 don't exist on the card at all.

The comment's reasoning — "deriving it from the athlete's anchors would mean fetching them
per card" — is right about *absolute* targets and wrong about *relative* ones: a
`PercentOfAnchor` target is **already a fraction of FTP**, so mapping it through %FTP
boundaries needs no anchor at all. Fix:

```ts
export type ZoneTone = "z1" | "z2" | "z3" | "z4" | "z5" | "z6" | "z7";

/** Coggan 7 lower bounds as fractions of FTP. Mirrors
 *  `_ZONE_SCHEMES[ZoneModel.COGGAN_7]` in backend/app/domain/zones.py —
 *  if you change one, change both. */
const COGGAN_7_LOWER: readonly number[] = [0, 0.55, 0.75, 0.9, 1.05, 1.2, 1.5];

export function zoneToneFor(fraction: number): ZoneTone { /* ... */ }
```

Keep the existing absolute-target scaling path exactly as it is — it is correct and its
rationale is sound.

**Tests:** extend `lib/workout-profile.test.ts` with a boundary case per zone
(0.54→z1, 0.56→z2, 0.89→z3, 0.91→z4, 1.06→z5, 1.21→z6, 1.51→z7). Add a test asserting
`COGGAN_7_LOWER` has 7 entries, so adding a zone backend-side fails here loudly.

---

### F2 · Session sheet shows prescribed *and* resolved, and names the anchor  [A3.5]

**Edit:** `frontend/components/calendar/session-sheet.tsx`

Per step: `88–93 % FTP` with `220–232 W` beside it (secondary ink). Once per sheet: a small
provenance line — *"Resolved against FTP 250 W · estimated · effective 2026-06-01"* — using
the `PinnedAnchorRead` data from B6. Render `estimated` and `assumed` provenance visibly
differently from `tested`; an estimate should read as an estimate.

Also render B5's `MetricExplanation` for predicted load: formula, inputs, assumptions,
citation. A disclosure or a footnote is fine; it does not need to be loud, it needs to be
**there**.

Extend `frontend/lib/criteria.ts` to surface each criterion's `smoothing_s` (B3) in the
criteria list — *"≥ 80 % of work time within ±5 % of target, 30 s average"*. A criterion
that hides its window is not a criterion the athlete can hold you to.

---

### F3 · Week rail  [A3.3]

**Edit:** `frontend/components/calendar/` — a new `week-rail.tsx` beside `week-grid.tsx`.

Renders, from B4: total planned duration, total planned load **with its coverage**
(`4 of 6 sessions`), and a per-discipline row (sessions, duration, load, sets).

Build the component with **optional props already present** for what arrives at WP-5/WP-7 —
`completedDurationS?`, `completedLoad?`, `fitness?`, `fatigue?`, `form?`, `ramp?` — and
render nothing for the ones that are `undefined`. Do **not** add those to the API schema
(B4 says why). This is so the layout is designed for its final density now rather than
being re-laid-out twice.

Place it to the **left of** the 7-day grid, not above it: scrolling through weeks keeps the
aggregates adjacent to the days they summarise.

**Never render a total without its coverage.** A partial total presented as complete is the
exact failure v2 §5 names — missing data means "not assessed", never "zero".

---

### F4 · UI conventions as a rule file  [A3.6]

**New file:** `.claude/rules/frontend-ui-conventions.md`, with frontmatter
`paths: frontend/**` so it loads automatically for frontend work.

Content — six rules, each one sentence of what plus one of why:

1. **Deep-link what a person would bookmark; keep transient state out of the URL.**
   Sub-views that survive a reload are real routes (`/sessions/{id}`, `/sessions/{id}/power`),
   not client-side tab state. Calendar position lives in the query string
   (`/calendar?week=2026-08-03`). Modal, dropdown, hover and selection state never do.
2. **Three overlay tiers, chosen by purpose.** Inline panel anchored under its trigger for
   filters and toggles; centred modal for editing one record; a route for anything worth
   linking to. A fourth is reserved for WP-5: a *floating, draggable window* — not a modal —
   for the map, because the athlete needs map and chart visible at once.
3. **Empty states name the missing input and the action that supplies it.** Not "No data
   yet" but "Add an FTP anchor to see power zones", with the control beside it.
4. **Metric grids hold their positions.** A missing value renders a placeholder in its fixed
   slot; it never collapses the grid or reflows its neighbours. See F5.
5. **Numerals are monospace** (already in `globals.css`; restate so it survives the next
   component).
6. **One dark scheme, declared.** No light theme, no toggle.

---

### F5 · The `not-assessed` placeholder component  [A3.6]

**New file:** `frontend/components/design/not-assessed.tsx`

A `—` (or `?`) in the slot where a value would be, carrying its reason on hover:
*"No power data"*, *"No FTP anchor pinned"*, *"Alignment confidence too low"*.

**Why it is worth its own component:** WP-7's scoring axes return
`not_assessed(reason)` as a first-class result, and this is how that renders. Building it
here means the calendar's missing predicted loads and WP-7's unscored axes are the same
component with the same affordance, instead of two conventions that drifted.

---

## Docs, before the PR

1. **`docs/decisions.md` is append-only and ends at D58 — but `frontend/app/globals.css`
   already cites D59 for the dark-only theme, and that entry was never written.** Write it
   first, then continue from D60.
2. New entries owed for this work order (what was chosen, what it displaced, why):
   - **Predicted load is computed, never stored** — same reasoning as zones. Displaced: a
     stored column maintained by a service hook.
   - **Criteria declare their smoothing window** — displaced: a constant in the scoring
     engine, rejected because it would let a scoring-engine change silently rewrite what
     already-scored sessions were judged against.
   - **One zone ramp, seven stops, mirrored front and back; purple reserved for
     interpretive content.**
   - **Explanations travel with the number, not the page.**
3. **CHANGELOG.md** — the usual WP-3 entry, plus these under the same heading.
4. If you deviate from anything in this document, append a decision entry rather than
   editing this file.

---

## Out of scope for WP-3 — do not start these

Named because they are adjacent and tempting:

- Anything requiring **completed** or ingested data — nothing is ingested until WP-4.
  No completion state, no actual-vs-planned, no adherence.
- **Stream charts, zone tables, histograms, sparklines.** WP-5. F1 exists so they are cheap
  when they arrive, not so they arrive now.
- **PMC / fitness / fatigue / form / ramp.** MMP. F3 reserves the slots and stops there.
- **Power curves, eFTP, CP, W′, VO2max.** MMP.
- **Maps, routes, chart↔map linking.** MMF-1.
- **Drag-to-move polish beyond WP-3.2's requirement**, weather, availability, wellness.

---

## Definition of done

- [ ] `just check` green; `just test-int` green
- [ ] `just api-sync` run and the generated types committed
- [ ] B1's TSS fixture (5 737 s, NP 141, FTP 200 → IF 0.705, load 79.2) passes
- [ ] B2's hand-computed interval-workout fixture passes
- [ ] A week mixing predictable and unpredictable sessions reports a load **and** the count
      it was computed from
- [ ] The session sheet shows prescribed *and* resolved targets, and names the anchor
      version with its provenance and effective date
- [ ] Appending a new FTP anchor does not change any existing session's resolved targets
- [ ] No template relies on the default `smoothing_s`
- [ ] No component defines a zone colour locally; `COGGAN_7_LOWER` matches
      `backend/app/domain/zones.py`
- [ ] `.claude/rules/frontend-ui-conventions.md` exists and the WP-3 pages conform
- [ ] D59 written; new decision entries appended
