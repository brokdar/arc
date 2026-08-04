# Incremental Delivery Plan — MVP → MMP → MMFs

Maps the v2 application description onto an incremental product delivery model. "Marketable" here means *worth using daily by the one athlete that matters* — you. Each increment must be independently valuable; nothing lands half-usable.

## Sequencing principles (the "why" behind the cuts)

1. **Risk first, plumbing later.** The unproven hypothesis is the intent→score→adapt loop and whether one athlete tolerates its ceremony. Analysis charts, vendor sync, and route maps are proven commodities (intervals.icu demonstrates all of them) — they carry zero product risk and can be bought for $4/mo in the interim.
2. **Schema-irreversible decisions go into the MVP even if their payoff is later.** The versioning doctrine (as-seen-then / as-known-now), planned↔actual matching as set-to-set proposals, anchor version pinning, and provenance fields shape every table. Retrofitting them means a rewrite. They are cheap to *lay down* early and ruinous to add late. Their full UX can come later; the data shapes cannot.
3. **A phase is defined by the question it answers,** not by a feature count:
   - MVP: *"Does the intent loop work, and will I feed it?"*
   - MMP: *"Can this replace intervals.icu as my daily driver?"*
   - MMFs: *"Which single capability adds the most coaching value next?"*
4. **The agent grows in authority as the deterministic substrate grows in trustworthiness.** Autonomy tiers exist from day one, but early phases keep the agent low-tier because the data underneath is thin. Safety rules precede coaching intelligence — the red-flag layer ships before scheduled inference does.
5. **Use intervals.icu as scaffolding during MVP/MMP** (ingestion aggregation, second-opinion analytics, charts). Every phase reduces the dependency; nothing ever depends on it structurally.

---

## MVP — "The coached week" (~4–6 weeks of focused work)

**Question answered:** does intent-before-session + scoring + agent proposals actually improve training, at a ceremony cost one athlete will pay?

### In scope

| Area | Content | Why now |
|---|---|---|
| Foundations | Versioning doctrine (as-of stamps, two views), provenance/trust fields, anchor version pinning, audit log | Irreversible schema decisions — principle 2. Minimal UX: just correct data shapes and a raw history view |
| Athlete model | Profile, manually entered anchors with provenance + CI + effective date, zones from declared model | Everything downstream derives from anchors; staleness *model* deferred, staleness *fields* present |
| Ingestion | Manual FIT/TCX/GPX via watched folder + upload; single-recording sessions only; quarantine for corrupt files | The durable path and the invariant all adapters resolve to. Multi-source reconciliation deferred — one device is enough to validate the loop |
| Workout creator | Structured endurance workouts (steps/repeats/ramps, %threshold + absolute targets); basic strength (exercises, sets/reps/load/RIR from a seeded catalogue); purpose vocabulary; intent + coach notes; success criteria auto-derived from purpose templates, editable | Intent capture is the product's core claim — it must be first-class on day one, for both endurance and strength since you train both |
| Calendar | Current-week concrete plan, drag/drop, week strip; plan states active/paused | The minimum planning surface. Rolling horizon shaping, phases, availability engine deferred |
| Matching | Candidate-proposal matching (±1 day, similarity-ranked, similarity floor, displaced state, unplanned first-class, manual link/swap/unlink) | Principle 2: the join at the center of the value chain. Its *semantics* must be right from the first scored session even if the matcher itself is simple |
| Scoring | Purpose-aware axes (completion, adherence, discipline, pacing; strength: sets/load), machine-suggested athlete-declared verdicts, reasons (controlled list + free text, expiring prompts → "not provided") | The loop's second half. Response and fuelling axes deferred (need baselines and fuelling intents) |
| Analysis (minimal) | Per-session time series charts, zone distribution, NP/IF/load, weekly load; structure alignment with per-step confidence | Just enough to score sessions and sanity-check against intervals.icu side-by-side. Everything else is commodity — rent it |
| Agent v0 | MCP tool surface over the app API (reads, session evaluation, plan CRUD as Tier 2/3 proposals with rationale+diff, expiry, default-on-expiry); write guardrails; deterministic red-flag safety rules; interactive use via chat client only | Guardrails and proposal lifecycle before autonomy — principle 4. No scheduled inference yet: you review the coach's work manually while calibrating trust |
| Feedback | Deterministic findings; agent-written interpretation attributed and distinguishable | The two-layer split is a founding principle, cheap at this size |

### Explicitly out and why
- **No scheduled inference** — trust isn't calibrated; manual agent sessions generate the dispute log that later tunes autonomy.
- **No vendor APIs** — export FIT files from Wahoo/Zwift/Watch manually or via Dropbox into the watched folder; adapters are pure plumbing with zero hypothesis value.
- **No PMC/power curves/CP models** — intervals.icu gives you these today; verify your loop first, port math later against its numbers.
- **No availability/constraint engine** — one athlete hand-placing a week doesn't need it; it needs the *proposal* machinery, which is in.

**Exit criterion:** four consecutive weeks where ≥80% of sessions carried pre-recorded intent, verdicts felt fair (dispute log reviewed), and at least one agent proposal per week was genuinely useful. If the ceremony is intolerable *to you*, the spec's core needs rework — better to learn that before building the substrate.

---

## MMP — "The daily driver" (replaces intervals.icu for daily use)

**Question answered:** can this be the single place you plan, review, and get coached — without opening intervals.icu?

### In scope

| Area | Content | Why now |
|---|---|---|
| Ingestion adapters | Wahoo (webhook), Zwift, HealthKit via export-app → watched folder, Strava backfill-only (agent-context exclusion enforced) | Removes daily manual file shuffling — the #1 friction after MVP. Strava's constrained role is a policy decision, cheapest enforced from the start of its adapter |
| Reconciliation | Multi-recording sessions, per-channel best-source with provenance, merge/split operations, settling window, late-data versioned recompute | You wear a watch + ride with a head unit: multi-recording is now your normal case, and it exercises the versioning pipeline laid down in MVP |
| Analysis parity | Power curve/MMP, eFTP/CP models with CIs, W′ balance, EF, decoupling, PMC (display-only authority), automatic interval detection, peak-effort search; trust-tiered comparison policy | This is what "replaces intervals.icu" means. Ported metric by metric with side-by-side verification while both systems run — the safest possible migration |
| Planning engine | Rolling horizon (next week shaped, weekly targets beyond), availability template + overrides, constraint engine v1 (safety/quality tiers, reason strings, infeasibility reporting), re-planning triggers, plan stability budget | Now that sessions flow automatically, *dynamic* planning becomes real: triggers have data to fire on. Constraint engine needs the availability model, hence both here |
| Anchors lifecycle | Staleness states + propagation flags, retest triggers, test validity preconditions, implausible-jump flagging | Needs weeks of scored history to drive evidence-of-change — data that only exists post-MVP |
| Wellness + readiness | Wellness capture (tiered inputs, graceful degradation), HRV baseline maturity gating, RPE protocol, readiness with asymmetric action | Feeds constraint engine and should-I-train. Baseline maturity clock starts at MMP launch — ship early so it matures |
| Scheduled inference | Morning brief, evening capture, session auto-evaluation, weekly review, should-I-train; run ledger; coach-offline degraded mode; interruption budget + coalescing | The MVP dispute log has calibrated trust; autonomy tiers now have data behind them. Degraded mode ships *with* scheduled inference, not after — outages are a Tuesday |
| Coach memory | Hypotheses/decision records/preferences, coach summary as default context, transparency surface (dispute/veto), directives | Scheduled inference without curated memory re-derives the athlete every run — expensive and drifting. Memory and inference are one increment |
| Goals v1 | Archetypes with measurement definitions, feasibility (honest "insufficient history" verdicts), conflict detection, closure flow | Feasibility needs anchors-with-history and load baselines — meaningless in MVP, honest at MMP |
| Calendar + weather | Forecast overlay on planned outdoor sessions; observed conditions stored per session; weather re-planning trigger under the stability budget | Your top-ten feature; trivially additive once the planning engine exists; observed conditions start accruing for later route/condition analysis |
| Daily loop UX | Today view, mobile PWA with offline today-session, notifications | The daily-driver bar: plan and review from the phone |

### Explicitly out and why
- **Route matching / Google Maps deep-dive** — valuable but standalone; zero coupling to the coaching loop; classic MMF.
- **Periodization/TID, recovery state, taper, return-to-training protocols** — they govern *months*; you need months of owned data first. Interim: agent applies these as judgment via proposals, encoded as rules in the next increment.
- **Backfill insights, pattern reports, coach scorecard** — all need accumulated scored history to be non-trivial.

**Exit criterion:** 30 consecutive days without opening intervals.icu for anything but curiosity; metric parity verified; scheduled loop running with ≤1 disputed interpretation per week.

---

## MMF sequence — post-MMP increments, one shippable feature each

Ordered by coaching value per unit of effort; each independently releasable, reorderable on observed need. Rough order:

**MMF-1: Route intelligence.** Route detection/matching across activities, Google Maps route view with chart↔map hover sync, section selection + persisted sections, named segments with condition-contextualized effort history, commute flagging. *Why first: your explicit top feature; by now months of observed-weather + GPS data make it immediately rich; fully decoupled from the plan engine so it can't destabilize coaching.*

**MMF-2: Periodization + unified recovery.** Phase semantics (intent vocabulary, target intensity distribution with tolerance bands, mandatory recovery weeks, entry/exit criteria), deliberate-overreach declaration, maintenance mode, unified recovery state as constraint input, concurrent-training rule set, overreaching watchdog. *Why second: the biggest jump in coaching quality — turns week-planning into block-planning; needs the months of load/wellness history MMP has now accumulated.*

**MMF-3: Event readiness.** Taper generation (protected from re-planning), minimum-lead-time checks, event-week handling, goal post-mortems. *Why here: only matters when a real event goal exists; slot it the increment before your next A-event rather than by calendar.*

**MMF-4: Strength depth.** Progression schemes with autoregulation from logged RIR, technique gates, strength deload cadence, e1RM trends with validity bounds, benchmark battery (defined protocols + scheduling), functional-fitness structures (EMOM/AMRAP/circuits). *Why: MVP strength logging works but doesn't coach; this makes the second discipline first-class.*

**MMF-5: Safety & lifecycle protocols.** Graduated return-to-training state machine (severity classes, staged progression, medical-clearance state), interruption anchor decay + rebuild ramps, low-energy-availability screen, masters adjustments. *Why not earlier: the deterministic red-flag layer (MVP) already covers the acute cases; these protocols encode the long-tail policies — important, but the red-flag floor was the urgent part.*

**MMF-6: Insight & evidence.** Pattern reports (limiter analysis, skip patterns, displacement patterns, condition effects), durability metrics (fatigued power curves, matched prior-work comparison), historical-backfill insight pass, coach scorecard + instance health report. *Why late: pure consumers of accumulated scored history — the more history, the better they are; they add insight, not capability.*

**MMF-7: Polish & reach.** Beginner mode as a configurable profile (you aren't one, so it waits), education layer, environment-aware prescription (heat adjustments, acclimation blocks), fuelling progression as trained capacity, optional `.zwo`/`.erg`/`.mrc`/`.fit` manual export, model-change probation machinery. *Why last: each is real but none blocks daily coaching for the primary user.*

---

## Cross-phase invariants

Present from the first commit, regardless of phase: versioning doctrine, provenance/trust fields, append-only anchors, audit logging, proposal lifecycle semantics, deterministic red-flag safety rules, original-file immutability, and the graceful-degradation rule (every input consumer defines its absent-input behavior). These are cheap as conventions and impossible as retrofits.

## Dependency snapshot

```
MVP: versioning ─ matching ─ intent/scoring ─ agent-v0(guardrails)
                     │
MMP: adapters → reconciliation → analysis parity
     availability → constraint engine → re-planning
     wellness → readiness ─┐
     coach memory ─────────┴→ scheduled inference
                     │
MMF: 1 routes │ 2 periodization+recovery │ 3 events │ 4 strength depth
     5 protocols │ 6 insight │ 7 polish
```
