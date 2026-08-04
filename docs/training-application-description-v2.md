# Training Application — Full Application Description (v2)

Revision of the original concept, incorporating the gap analysis (structural contradictions resolved, missing policies added) and the intervals.icu/OSS research. Device-push functionality is out of scope by decision. Feature level only; no implementation details.

---

## 1. Vision

A self-hosted, single-athlete training application holding a **dynamic, goal-driven training plan** across all trained disciplines — road cycling, strength, core, functional fitness — continuously adjusted to real life, operated jointly by the athlete and an LLM coaching agent.

Defining property: **every planned session carries recorded intent** (what it's for, what it should achieve, what to watch for), and **every completed session is evaluated against that intent**. A log of unlabelled activities cannot answer whether the athlete is improving or just riding more; a log of intent-linked, scored sessions can.

### Design principles

1. **Intent before execution.** Purpose, success criteria, and coach notes are recorded before the session; scoring is against that record, never reconstructed afterwards.
2. **Provenance everywhere.** Every anchor, metric, and derived value knows where it came from, what it was computed from, and how trustworthy it is.
3. **Two truths, both kept.** Derived artefacts are versioned: *as-seen-then* (what the athlete acted on, immutable) and *as-known-now* (latest recomputation, marked and diffed). Athlete testimony — verdicts, reasons — is never auto-rewritten.
4. **Proposal-first with explicit autonomy tiers.** The agent's authority is a deterministic policy table, not its own judgment.
5. **The real athlete, not the compliant one.** Every input touchpoint defines its no-response default, its expiry, and its degraded behavior. Missing data means "not assessed," never "failed."
6. **Vendor independence.** Every external source resolves to standard activity files in one ingest path; every integration is a replaceable adapter; original files are immutable and sufficient to rebuild everything.
7. **Deterministic before interpretive.** Computed findings and agent interpretation are always distinguishable; the deterministic layer wins on facts and constraints.

### What the application will not do

It will not predict injury. It will not diagnose overtraining, illness, or any medical condition. It will not prescribe body composition targets. It will not replace medical clearance after systemic illness or cardiac symptoms. It will not interpret readiness data before baselines mature — it abstains and says why. These refusals are features: the agent must not confabulate into gaps.

---

## 2. Scope

- **User:** one athlete per instance, self-hosted, full data ownership, no human coach assumed, possibly a beginner at structured training.
- **Disciplines, tiered:**
  - *Full analysis:* road cycling (power/HR), indoor cycling (Zwift/trainer).
  - *Full logging + structured prescription:* strength, core, functional fitness (sets/reps/load/RIR, exercise catalogue, progression).
  - *Basic load:* anything else that produces a file (duration, HR load, RPE) — honestly labeled as basic tier.
- **Out of scope (explicit):** push of planned workouts to head units or third-party platforms; multi-user/coaching-org features; social features; nutrition tracking beyond session fuelling; medical diagnostics.
- **Optional, not core:** manual export of a planned workout as `.zwo` / `.mrc` / `.erg` / `.fit` file for the athlete to load into Zwift or a device themselves.

---

## 3. Athlete model

- Profile: age, sex, height; body metrics (weight, composition) **tracked, never prescribed** (see §5.6 for the goal-side resolution).
- **Physiological anchors with full lifecycle:** FTP, Critical Power, W′, LTHR, max HR, zones.
  - Append-only history; every anchor carries provenance (tested / estimated / assumed / athlete-reported), protocol, effective date (test date, not entry date), and confidence interval.
  - **Staleness model:** every anchor has a validity window and a state — fresh / aging / stale — driven by elapsed time *and* evidence of change (efforts exceeding the anchor's implied limits, systematically easy/hard execution scores, training interruption, significant mass change). Staleness propagates: anything derived from a stale anchor is visibly flagged.
  - **Retest triggers,** not just schedules: phase boundary, staleness, evidence of change, before feasibility re-evaluation. The agent proposes test sessions into the plan; beginners are never expected to self-schedule tests.
  - **Test validity preconditions:** freshness requirements (low-load days before, no key session in the preceding window). Results under violated preconditions are admitted only with reduced confidence. Implausible anchor jumps are flagged, never committed silently. A beginner's first tests are low-confidence by default with a shortened validity window.
  - **Anchor precedence is explicit:** one anchor governs zone derivation per channel; FTP- and CP-derived quantities are never silently mixed. The zone model in use is declared and recorded alongside the anchor. Confidence intervals are *used*: estimated/assumed anchors widen target bands and cap the precision of scores derived from them.
- Capability profile per discipline: current volume, training age, equipment, terrain.
- **Injury, illness, and interruption records as first-class entities with lifecycle behavior** (not just records — see §5.7 return-to-training).
- Wellness series: sleep, resting HR, HRV, weight, fatigue, soreness (by body region), stress, motivation; optional menstrual cycle and symptom tracking (used as context and confounder, never for phase-based prescription).

---

## 4. Goals

- Goal = direction + proxy metric + horizon + verification method.
- Archetypes: event, performance target, distance, consistency/habit, strength. Each archetype **declares its measurement definition**: source metric, eligible-session filter (minimum trust level), sampling cadence, estimator/smoothing, and noise band — "improving beyond measurement noise" is computable, not rhetorical.
- Body-composition ambitions are accepted only as **behavior/process goals** (e.g., fuelling consistency, training consistency). No body-fat or weight targets. Rate-of-change guardrails on observed weight trends; violations trigger a stop proposal. A low-energy-availability screen (persistent fatigue, declining performance at stable load, frequent illness, cycle disturbance, sleep disruption) suppresses load progression and recommends professional input.
- Multiple concurrent goals across disciplines.
- **Feasibility as a tracked quantity:** evaluated at creation *and* re-evaluated on cadence and trigger (missed weeks, anchor change, interruption). Inputs are explicit: training age, current anchors with CIs, the athlete's own historical response where available, realistic weekly hours, horizon, interruption history, age. Verdict: feasible / tight / implausible / **insufficient history to assess** — with confidence and inputs shown, plus which lever must move (horizon, target, hours, competing goals, consistency). The app never fabricates a verdict from population averages without saying so.
- **Conflict detection with an enumerated basis:** mass-loss vs. strength/power, high-volume endurance vs. hypertrophy, two peaks too close, required hours exceeding availability, energy deficit vs. any performance goal. Output includes a priority ordering, not just a warning.
- Event goals include a minimum-lead-time check (can this event still be peaked for?) and imply a protected taper phase (§5.5).
- **Goal closure flow:** achieved, missed, or abandoned goals trigger an explicit retrospective (predicted vs. achieved, linked to the decision records that shaped the block) and a "what's next" conversation — never silence.

---

## 5. Planning

### 5.1 Structure
- **Rolling horizon:** current week concrete and dated; next week shaped as session types and durations; beyond that, weekly targets only. Plans, phases, weeks, sessions with full CRUD (athlete, UI, agent — all through the same guarded API). Templates and reusable blocks.
- **Plan states:** active / paused (reason + return date) / off-season / return-to-training. Paused means no sessions generated, no prompts, no adherence penalty. Resuming forces re-feasibility, anchor decay assessment, and a defined ramp protocol.

### 5.2 Periodization
- Each phase declares: intent from a controlled vocabulary (base, build, peak, taper, transition, maintenance), a **target intensity distribution** over the purpose vocabulary with tolerance bands, a loading pattern with **mandatory recovery weeks at a stated cadence**, entry/exit criteria, and a review cadence at which the agent must re-evaluate the phase plan.
- Weekly review reports achieved-vs-target intensity distribution as a first-class deviation.
- Deliberate overreaching blocks are declared up front (planned recovery duration, expected transient performance dip) so subsequent poor sessions are interpreted correctly instead of triggering panic re-planning.
- A **maintenance mode** exists: the minimum viable plan that holds fitness through busy periods, so the athlete has a legitimate low-effort option rather than a binary train/abandon.

### 5.3 Availability
- Recurring weekly template plus per-date overrides; overrides always win and are **inviolable ground truth** — the planner never relaxes availability.

### 5.4 Constraint engine
- Explicit, readable rules; every rule that fires produces a reason string.
- **Two-tier taxonomy:** *safety* constraints (ramp limits, recovery spacing, progression gates, red-flag suppressions) are never auto-relaxed; *quality* constraints (heavy-lower-body separation from key sessions, contiguous slots for long sessions, preferred orderings) relax in a declared priority order, each relaxation logged into the proposal rationale.
- If the plan is infeasible without breaking a safety constraint, the engine **reports infeasible with the minimal conflicting set** and proposes the least-bad drop.
- Concurrent-training rules: minimum same-day separation between strength and quality endurance work, intra-session ordering by priority quality, protected window after novel/high-eccentric loading, strength volume reduced (not eliminated) approaching events.
- Ramp limits are framed as tolerance/consistency heuristics derived from the athlete's own history — explicitly *not* injury prediction.
- **Unified recovery state:** a discipline-agnostic daily recovery estimate — fed by hard-session count and recency across all disciplines, wellness inputs, and execution outcomes, with an explicit confidence level — is an input to the constraint engine (gating key sessions, enforcing "≤N high-stress sessions per rolling 7 days across disciplines"), not merely a dashboard number. Endurance and strength load metrics remain on separate axes and are never summed.

### 5.5 Re-planning and proposals
- Triggers: availability change, missed session, weather threshold, poor execution, illness, travel, goal change, anchor change.
- **Proposal lifecycle:** every proposal carries a rationale, a diff, an expiry time, and a declared default-on-expiry (the committed plan stands). One open proposal per plan entity; new triggers amend the open proposal rather than stacking. A recorded activity that contradicts a pending proposal auto-resolves it (behavior implies choice, logged). Lapsed-unseen proposals are recorded and surfaced in the weekly review. **Scoring always runs against the committed plan, never a lapsed proposal.**
- **Plan stability policy:** a budget on automated proposals per week; weather-triggered changes require forecast persistence across consecutive forecast cycles and cannot touch sessions less than a set number of hours away except for safety; non-urgent changes batch into the weekly review. Urgent (illness, safety) and deferrable proposal classes are distinct.
- Taper phases and race weeks are **protected** from ordinary re-planning triggers.

### 5.6 Taper (event goals)
- Computed backward from the event date: volume reduced ~40–60% while intensity and frequency are maintained, duration in the ~8–14-day range by event type, reduced strength loading, activation/opener sessions. Adjustable, but present by default for every event goal.

### 5.7 Return-to-training (safety-critical)
- Illness records carry a severity class: localized/above-neck vs. systemic/febrile vs. medically diagnosed.
- Systemic illness drives a **mandatory graduated return protocol**: symptom-free-at-rest precondition, staged days with capped intensity and duration, advancement only on symptom-free completion, automatic regression on recurrence. During the protocol the agent refuses to propose key sessions and does not treat improving wellness numbers as license to skip stages.
- Cardiorespiratory red flags (chest pain, disproportionate dyspnoea, palpitations, syncope) put the instance in a "seek medical clearance" state in which no intensity is prescribed at all.
- Interruptions decay anchors: provenance downgraded, confidence intervals widened as a function of duration; the plan rebuilds from reduced load with a stated ramp, never resumes as if nothing happened; a retest is scheduled after a stated rebuild period.

---

## 6. Workout creator

- **Structured endurance workouts:** recursive steps, repeats, ramps; independent target channels for power, HR, pace, cadence; targets as % of threshold, % of power-duration curve at relevant durations (for neuromuscular/anaerobic and long-endurance work where %FTP breaks down), or absolute values. Recursive structures flatten on file export.
- **Strength / core / functional fitness as first-class:** exercise catalogue (seeded from an open catalogue, extensible), sets, reps, load, RIR, rest, tempo, supersets/circuits, EMOM/AMRAP structures for functional work, progression schemes with autoregulation (load adjustments driven by logged RIR and execution), technique gates (load progression can be gated on demonstrated movement quality, not just sets completed), and a strength-specific deload cadence.
- **Purpose classification** from a controlled vocabulary: recovery, endurance, tempo, sweet spot, threshold, VO2max, anaerobic, neuromuscular, unstructured/free, technique, test; strength: max strength, strength endurance, hypertrophy, power, core, mobility, conditioning.
- **Intent** (free text: why this session, this week) and **coach notes** (what to attend to) on every planned session.
- **Success criteria — machine-checkable, purpose-aware:** time in target band, duration floors, ceilings, allowed drift, surge limits, sets/reps/load completed, fuelling target. Auto-derived from purpose templates, editable per session. Each purpose template **declares which scoring axes apply and which are suppressed** (no time-in-band for unstructured rides or rolling outdoor terrain; work-above-threshold rather than time-in-band for VO2max; set/load-based criteria for strength). Outdoor sessions get wider bands than indoor, with the reason stated.
- **Fuelling as part of intent:** carbohydrate rate targets scaled to duration and intensity from the purpose template; long-ride fuelling progresses across the plan as a trained capacity; low-availability sessions only if explicitly intended, bounded in frequency, never adjacent to key sessions.
- **Intent freezes at execution.** Post-hoc edits create a new intent version, force a rescore through the versioning pipeline, and are flagged in the record.
- Workout library with folders, tags, and search. Plan templates and reusable blocks reference library workouts.

---

## 7. Ingestion

### 7.1 Sources (all behind replaceable adapters)

| Source | Mechanism | Role |
|---|---|---|
| **Manual FIT/TCX/GPX files** | Watched folder + UI upload | The durable, always-available path; every other source ultimately lands here as standard files |
| **Wahoo** | Cloud API with webhook | Outdoor rides from ELEMNT units — richest outdoor data |
| **Zwift** | Direct API / activity files | Indoor structured sessions |
| **Apple Watch (HealthKit)** | Third-party export app (HealthFit/RunGap-class) delivering standard files to the watched folder; optionally a thin companion shortcut later | Strength/core/functional sessions; backup HR |
| **Strava** | API (in only) + archive ZIP | **Backfill and fallback only.** Strava-sourced data is excluded from agent context by default (API terms prohibit AI/ML use); original-file sources always take precedence |
| **Historical archive** | One-time bulk export import (Strava ZIP, intervals.icu bulk FIT, generic folder) | Backfill of pre-application history |
| **Manual entry** | UI or agent | Strength sets, sessions without a device, subjective data |
| **Weather service** | Public API by timestamp + location | Forecast for planning; observed conditions per completed outdoor session |
| **Elevation reference** | Public data | Barometric drift correction |

Any adapter can disappear without breaking the application; the watched folder is the invariant.

### 7.2 Deduplication and reconciliation
- A **session** is a real-world event; a **recording** is one device's account of it. Duplicates are identified by content and temporal overlap (with bounded clock-skew tolerance); near-threshold auto-decisions are surfaced as "assumed same session — confirm?" rather than silent.
- **Per-channel best-source resolution with provenance:** where multiple recordings exist, each data channel resolves to the best available source; every resolved value records its source and trust level. A ride without a chest strap borrows HR from the watch — permanently marked as lower-quality.
- **Merge and split are first-class user operations** with provenance, re-triggering matching and recomputation.
- **Late-arriving data policy:** within a settling window (~72 h post-session) channel-resolution changes apply silently; after it, they create a new score version with a review flag — never a silent rewrite. Ingest-driven and user-driven corrections share one recomputation pipeline.
- **Quarantine flow** for corrupted, implausible, partial, or overlong recordings: flag and ask, never silently ingest; trim/repair offered as a proposal.
- Time semantics: a session's calendar date is its start time in the athlete's local timezone at start; midnight-crossing sessions belong to their start date; matching windows absorb travel edge cases.

### 7.3 Historical backfill
- Backfilled activities form a **"historical" class**: exempt from intent scoring and adherence/reason analytics; included in anchor estimation, load history, and power curves. Backfill recomputes load history through the versioning pipeline with a one-time revision summary. The coach's first deliverable on a fresh instance with history: "insights from your history."

---

## 8. Analysis

### 8.1 Session analysis
- Multi-channel time series — power, HR, cadence, speed, elevation, temperature, and niche streams where present — synchronized with the route map (**Google Maps view**): hovering the chart marks the position on the map and vice versa.
- **Section selection:** drag any range on chart or map, live aggregate statistics; promote a selection to a named, persisted section; sections may overlap.
- **Automatic interval detection:** peak efforts across standard durations, climbs, W′ depletion matches, device laps, alignment to the planned structure, and targeted search ("find 6×6 min at ~110%").
- Structure alignment tolerates recording artifacts (auto-pause, unlapped warm-ups, extra rest): each prescribed step carries an alignment confidence; low-confidence steps are excluded from adherence with a reason, not scored as violations. Strength alignment unit is the set.

### 8.2 Routes and segments
- **Route detection and matching:** recurring routes recognized automatically across activities; repeated-route efforts compared over time **under recorded conditions** (wind, temperature, position/equipment tags). Commute-type routes flaggable to exclude from training analysis.
- **Named route segments:** persistent, matched across activities, with effort history and condition context. Map rendering and route views on Google Maps.

### 8.3 Metrics
- Endurance: normalized power, intensity factor, training load, efficiency factor, aerobic decoupling, power curve / mean-maximal power (fresh state), critical power modelling (declared model, with CIs), zone distribution, fitness/fatigue/form.
- **Durability:** power-duration conditioned on prior accumulated work — fresh vs. fatigued curves, decrement tracked as a series, comparisons gated on matched prior-work bins. Available as a goal proxy with its own test protocol.
- Strength (separate axis, never summed with endurance): estimated 1RM trends (with the estimation's rep-range validity stated), volume load, session-RPE load, benchmark battery results (defined protocols, cadence, and validity conditions — jump height, holds, single-leg tests, grip).
- **Every metric carries two declared properties:** a *trust level* derived from its inputs (propagation rule: minimum of input trusts; aggregate trust = weakest material contributor, with on-demand breakdown), and a *decision-authority level* — decision-capable (may justify constraints, gates, readiness answers: time-in-zone, hard-session recency, duration, matched-effort performance, execution verdicts) or display-only (fitness/fatigue/form, W′ balance, decoupling, single-day HRV). No display-only metric ever justifies a plan decision on its own.
- **Comparison policy (three tiers):** compare freely when trust matches; compare with a stated caveat when trust differs within the same modality; refuse only when modalities are incommensurable (enumerated pairs, e.g., estimated vs. measured power), always with the reason stated.
- Environment-aware interpretation: physiological-response comparisons require matched conditions on a declared confounder list (heat, prior-day load, sleep); heat shifts prescription authority between power and HR channels and widens scoring leniency, with the adjustment stated.

### 8.4 Wellness and readiness
- Wellness trends with baselines. HRV interpretation requires a stated baseline maturity (several weeks of consistent collection); before that the app **abstains and says why**. Rolling averages against meaningful-change thresholds only — never single-day deltas. Subjective wellness weighs at least as heavily as HRV. Readiness acts asymmetrically: it readily downgrades or defers sessions, rarely upgrades.
- RPE protocol: fixed named scale with anchor descriptors shown at entry, enforced collection window (late entries marked lower-confidence), RIR kept distinct from session RPE, long-run drift detection.
- **Overreaching watchdog:** a convergent-evidence flag when several independent signals deteriorate together over a rolling window (matched-effort performance, wellness trend, motivation, resting HR, verdicts trending "under") — forces an unload proposal and, if persistent, escalates to recommending professional input. The app states it cannot diagnose overtraining.

---

## 9. Execution scoring

- Each completed session is scored against its frozen intent on independent, purpose-applicable axes: **completion, adherence** (time/work in target band per work interval), **discipline** (time above ceiling), **pacing** (fade vs. intended profile — intended fades score as correct), **response** (physiological response vs. the athlete's own matched baseline: N most recent same-purpose sessions, compatible trust, matched confounders, execution-time anchors; below a minimum pool size the axis returns "no baseline"), **fuelling** (vs. the recorded fuelling intent), and for strength: sets/reps/load/RIR vs. prescription.
- **Verdict — machine-suggested, athlete-declared:** as intended / under / over / abandoned / different session. Overrides are stored with both values; the disagreement rate is itself analyzable. Abandoned vs. under is defined by the duration-floor criterion; partial completions still score the completed portion.
- **Reasons:** 1–3 per deviation, ordered by primacy, from a controlled list (time, weather, heat, traffic, terrain, fatigue, sleep, fuelling, illness, equipment, group ride, felt good, not provided) plus free text. The agent periodically reviews free-text and "other" volume and proposes new codes. Reasons are revisable append-only. Reasons are decoupled from verdict direction. "Not provided" is a legitimate, analyzable value.
- **Planned↔actual matching is a scored candidate proposal, never a silent commitment:** ±1-day window, ranked by structural similarity; set-to-set links (one-to-many and many-to-one first-class); a similarity floor below which the link becomes *executed-instead-of*, marking the planned session **displaced** (a third completion state besides done/missed) rather than failed; unplanned activities are first-class, never force-matched, scored on absolute quality only; session context types (group ride, race, commute, event) switch the scoring rubric; manual link/swap/merge/split/mark-unplanned operations always available; confirmed links are sticky.
- Data quality gates every score; every score carries the trust level of its inputs and the anchor version it was scored against.
- Reasons and verdicts aggregate into **pattern reports**: what actually limits progress, which session types get skipped, how conditions affect performance, displacement patterns.

---

## 10. Feedback

- **Deterministic layer:** computed from criteria and axes; reproducible, auditable, works with no LLM available.
- **Interpretive layer:** written by the coaching agent — what it means, what to change, pattern vs. one-off — always visually distinguishable, attributed, and citing the deterministic facts and coach-memory hypotheses it relied on.
- Verdict vocabulary is internal; **presented copy is framed** (verdict + context + next step). Every refusal states what is missing and how to unlock it. Contextual education ("what is FTP?", "why this test?") on every metric, with depth adapted to an athlete-experience preference.

---

## 11. Coaching agent layer

### 11.1 Tool surface
- MCP tool surface over the application's own API: intent-level write tools, task-oriented composites, constrained read access. Chat (if/when present) is a UI over the same tools — no privileged side channel; free-text athlete statements parse into structured capture (wellness note, injury flag, reason) with confirmation, raw text preserved with provenance.

### 11.2 Autonomy model
- **Deterministic action-tier table** (data, not agent judgment):
  - Tier 0 — autonomous: reads, session evaluations, annotations marked as commentary.
  - Tier 1 — autonomous with notification: coach notes, should-I-train-today answers, tagging.
  - Tier 2 — proposal required: any planned-session mutation, workout structure change, weekly load change.
  - Tier 3 — proposal + explicit confirmation: goal changes, anchor updates, multi-week restructures, anything touching a taper or race week.
- Athlete-configurable per action class, with a floor: Tier 3 is never autonomous.
- **Directives:** athlete-authored hard constraints ("never propose double days") enforced by the constraint engine before any proposal is emitted. Repeated same-reason proposal rejections auto-suggest promotion to a directive.

### 11.3 Safety layer (runs before the agent)
- Deterministic red-flag rules: fever, chest-pain or injury tags, rapid weight change, consecutive very-poor wellness — hard-cap the agent's response space to rest/see-a-professional, suppress load-increase proposals until explicitly cleared, and cannot be overridden by the agent. Lives in the constraint engine with reason strings.

### 11.4 Coach memory
- Hypotheses with status and disconfirming tests; decision records (options considered and rejected); athlete preferences; audit trail.
- **Curation:** every hypothesis carries a review-by date; superseded or contradicted items are explicitly retired with links; a periodic consolidation pass is a scheduled job.
- **Coach summary:** a maintained, regenerated handoff document — current picture of the athlete, active hypotheses, standing directives, recent trajectory — the default context for routine agent runs; raw memory queried on demand. Per-task context recipes are defined (evening evaluation reads X; weekly review reads Y).
- **Transparency:** a "what the coach thinks about you" surface shows all hypotheses with evidence links; the athlete can dispute (treated as unconfirmed, and said so when relied upon) or veto (excluded entirely, logged) any item.
- **Model-change handling:** agent-authored content is tagged with model identity; a model change triggers a re-onboarding pass (regenerate summary, flag hypotheses for re-validation) and a probation window with lowered autonomy ceilings. Coach voice/tone is an athlete-set preference, not an emergent model property.

### 11.5 Scheduled inference
- The application itself evaluates new sessions, writes coach notes, prepares the morning brief and weekly review, and answers "should I train today" — without requiring a chat.
- A visible **run ledger**: when each run executed, what it read, wrote, skipped, or failed; runs idempotent per task and date; missed runs queue with a staleness cutoff and retrospective labeling.
- **Degraded operation ("coach offline"):** everything deterministic works fully without an LLM; interpretive slots render "coach unavailable"; should-I-train falls back to a labeled deterministic rules answer. Athlete-visible inference spend with a budget and a declared throttling order.

### 11.6 Write guardrails
- Append-only anchors with provenance; no agent writes to recorded activity data; soft deletes; dry-run mode; optimistic concurrency; audit logging; proposal-then-commit for multi-entity changes.

### 11.7 Coach evaluation
- One-tap rate/dispute on every interpretive item. A coach scorecard in reviews: proposal acceptance rate, lapsed-proposal rate, disputed-feedback rate, hypothesis confirm/refute ratio. Goal post-mortems compare predicted vs. achieved, linked to decision records. Deterministic/agent contradictions are surfaced (both views shown) and logged as a coach-quality signal.

---

## 12. Daily loop

- **Today view** as the primary screen: what, how long, how hard, in one sentence — plus intent, coach notes, and the training-window forecast.
- **Calendar view** of the plan with weather forecast overlay on planned outdoor sessions; week strip showing completion at a glance.
- Morning notification: today's session, intent, watch-outs, forecast. Evening prompt when a planned session has no matching activity — expiring after 48–72 h into "reason: not provided"; at most one consolidated catch-up prompt, never a stack.
- **Interruption budget:** a daily notification cap with coalescing (one morning digest bundles the should-I-train answer, pending proposals, and notes); quiet hours; timezone-aware scheduling. Prompt response rate is tracked as a health signal the coach responds to by asking less, not nagging more.
- Weekly review: plan vs. actual, metric trends, achieved-vs-target intensity distribution, deviation patterns, lapsed proposals, prioritized recommendations. State-aware templates (paused weeks get recovery framing, empty weeks get re-engagement framing, not a wall of zeros).
- Mobile-friendly (PWA-class) with offline access to today's session for gym and rural use.

---

## 13. Onboarding and lifecycle

- **Bootstrap mode:** guided intake (history, injuries, current activity level, time budget, goals, equipment, sensors) populates the athlete model with `athlete-reported` provenance; provisional anchors from population defaults or first-rides estimation, marked `estimated` with wide CIs and widened scoring tolerances; the early plan schedules benchmarks to graduate them. A visible **confidence ramp**: features unlock as data accrues, and the app says so. Conservative deterministic progression caps bind the agent hardest exactly when it knows least.
- **Beginner mode** (default-on when intake indicates it): consistency goals before performance goals; longer accumulation before intensity; RPE-anchored prescription until anchors stabilize, with a stated transition criterion; reduced visible zone count; advanced metrics suppressed until interpretable; faster anchor re-estimation; technique sessions first-class with load gated on movement quality; a minimum-viable-session fallback for compromised days; damped re-planning; interpretive feedback defaults to explaining why.
- Masters adjustments: age modifies recovery spacing, weekly hard-session ceiling, taper duration, and rebuild rate; strength becomes protected rather than optional.
- **Instance health report** (quarterly, by the coach): goal-proxy progress, proposal churn, prompt response rate, input burden (minutes/week of data entry), unmatched-activity rate — distinguishing "athlete improving" from "athlete servicing the app."

---

## 14. Data management

- Original files retained permanently, never modified; every derived artefact rebuildable from them.
- **Versioning doctrine (global):** every derived artefact carries an as-of stamp. *As-seen-then* is immutable and always retrievable; *as-known-now* is the latest recomputation, visibly marked ("recomputed on X because Y") with a diff. All correction triggers — manual fixes, anchor updates, channel-resolution flips, dedup merges/splits, backfill — flow through this one pipeline, in a defined cascade order: anchors → zones → per-session scores → aggregates → pattern reports. Verdicts, reasons, and delivered feedback are annotated, never recomputed; a recomputation that flips a verdict's numeric basis flags it "contested" and prompts (not requires) re-confirmation.
- Full export (files + database), no lock-in; backup with verified restore.
- Graceful degradation is specified per input: every manual input is tiered (required / valuable / optional), and every consumer of an input defines its behavior when the input is absent.
