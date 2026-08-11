import type { components } from "@/generated/api/schema";
import { addDays, mondayOf, todayIsoDate } from "@/lib/dates";
import { MATCH_BREAKDOWNS } from "./generated-matching";
import { RIDE_METRICS, RIDE_STREAMS } from "./generated-metrics";
import { SCORED_FTP_VERSION_ID, SCORED_PAIRS } from "./generated-scoring";

type Schemas = components["schemas"];

/**
 * A realistic week — realistic in the strict sense that **the real API could
 * have produced every byte of it**.
 *
 * That is not decoration. A fixture is the only specification a component test
 * has of what it will be handed in production, and one that states an
 * impossible payload makes the test agree with the fixture rather than with
 * the application. So the rules the backend holds to hold here:
 *
 * * a card's `title` is the *library workout's* name, and is therefore
 *   non-null exactly when `workout_id` is (`app.services.plan._card`);
 * * a strength card has no `planned_duration_s` and an endurance card no
 *   `total_sets` (`WorkoutSummary`);
 * * `step_count`, `total_sets`, durations, predicted load, intensity factor,
 *   coverage and volume load are all **derived from the structure below**, not
 *   chosen — the numbers here were computed by running `app.domain.prediction`
 *   over these exact documents at FTP 250 W, and a card's
 *   `predicted_load_coverage` is the very field its session's
 *   `predicted_load.coverage` carries, because on the real API it is;
 * * an intent pins **exactly** the anchors its prescription and its criteria
 *   refer to, no more and no fewer (`SessionIntent.__post_init__`), so the
 *   sessions with no percentage-of-anchor target pin nothing;
 * * a `tested` anchor version carries a protocol, because the domain refuses
 *   one that does not.
 *
 * Built *from* the requested start date so tests never have to freeze the
 * clock — the calendar asks for the Monday of the current week and gets a week
 * back with the same shape whichever Monday that is.
 */
export const SESSION_IDS = {
  strength: "0199a000-0000-7000-8000-000000000001",
  vo2: "0199a000-0000-7000-8000-000000000002",
  recovery: "0199a000-0000-7000-8000-000000000003",
  missed: "0199a000-0000-7000-8000-000000000004",
  long: "0199a000-0000-7000-8000-000000000005",
  /** The id `POST /planned-sessions/{id}/copy` answers with. */
  copy: "0199a000-0000-7000-8000-000000000006",
} as const;

export const WORKOUT_IDS = {
  vo2: "0199a000-0000-7000-8000-0000000000aa",
  long: "0199a000-0000-7000-8000-0000000000bb",
  lower: "0199a000-0000-7000-8000-0000000000cc",
} as const;

/** The FTP version every cycling fixture's percentages resolve against. */
const FTP_VERSION_ID = "0199a000-0000-7000-8000-0000000000f1";
const FTP_WATTS = 250;

/**
 * The FTP a session **pinned**, deliberately different from the one in force.
 *
 * 250 W and merely *estimated*, where `anchorVersionFixture` hands out a
 * tested 265 W: any screen that resolves a planned session against "now"
 * instead of against its pins renders visibly different numbers, and says
 * "tested" about a value that was guessed.
 */
const PINNED_FTP: Schemas["PinnedAnchorRead"] = {
  anchor_type: "ftp",
  anchor_version_id: FTP_VERSION_ID,
  value: FTP_WATTS,
  unit: "W",
  provenance: "estimated",
  effective_date: "2026-06-01",
};

// --- prescriptions -----------------------------------------------------------

const VO2_STRUCTURE: Schemas["EnduranceStructureSchema-Output"] = {
  discipline: "cycling",
  steps: [
    {
      kind: "steady",
      role: "warmup",
      name: "Warm-up",
      duration_s: 720,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.5,
          pct_high: 0.6,
        },
      },
      distance_m: null,
    },
    {
      kind: "repeat",
      times: 5,
      children: [
        {
          kind: "steady",
          role: "work",
          name: "VO₂ block",
          duration_s: 240,
          targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 1.14,
              pct_high: 1.22,
            },
          },
          distance_m: null,
        },
        {
          kind: "steady",
          role: "rest",
          name: "Spin",
          duration_s: 180,
          targets: {
            power: {
              kind: "percent_of_anchor",
              anchor_type: "ftp",
              pct_low: 0.4,
              pct_high: 0.5,
            },
          },
          distance_m: null,
        },
      ],
    },
    {
      kind: "steady",
      role: "cooldown",
      name: "Cool-down",
      duration_s: 600,
      targets: {},
      distance_m: null,
    },
  ],
};

const LONG_STRUCTURE: Schemas["EnduranceStructureSchema-Output"] = {
  discipline: "cycling",
  steps: [
    {
      kind: "steady",
      role: "warmup",
      name: "Roll out",
      duration_s: 900,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.5,
          pct_high: 0.6,
        },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "work",
      name: "Endurance",
      duration_s: 9600,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.62,
          pct_high: 0.72,
        },
        hr: { kind: "absolute", unit: "bpm", low: 120, high: 148 },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "cooldown",
      name: "Spin home",
      duration_s: 900,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.4,
          pct_high: 0.5,
        },
      },
      distance_m: null,
    },
  ],
};

/**
 * A recovery spin prescribed off the power meter entirely.
 *
 * Deliberately anchor-free: it pins nothing, resolves to nothing, and has no
 * predicted load — which is what makes the week's cycling row carry a real
 * coverage shortfall (two of three rides predicted) instead of a tidy total.
 */
const RECOVERY_STRUCTURE: Schemas["EnduranceStructureSchema-Output"] = {
  discipline: "cycling",
  steps: [
    {
      kind: "steady",
      role: "work",
      name: "Easy spin",
      duration_s: 2700,
      targets: {
        hr: { kind: "absolute", unit: "bpm", low: 100, high: 120 },
        cadence: { kind: "absolute", unit: "rpm", low: 85, high: 95 },
      },
      distance_m: null,
    },
  ],
};

const STRENGTH_STRUCTURE: Schemas["StrengthStructureSchema"] = {
  discipline: "strength",
  groups: [
    {
      label: null,
      items: [
        {
          exercise_id: "back_squat",
          sets: 4,
          reps: 5,
          load: { kind: "percent_e1rm", value: 0.82 },
          rir: 2,
          rest_s: 180,
          tempo: null,
          notes: null,
        },
      ],
    },
    {
      label: "Superset A",
      items: [
        {
          exercise_id: "romanian_deadlift",
          sets: 3,
          reps: 8,
          load: { kind: "kg", value: 80 },
          rir: 2,
          rest_s: 90,
          tempo: null,
          notes: null,
        },
        {
          exercise_id: "hanging_leg_raise",
          sets: 3,
          reps: 12,
          load: { kind: "bodyweight", value: null },
          rir: null,
          rest_s: 60,
          tempo: null,
          notes: null,
        },
      ],
    },
  ],
};

/** Bodyweight throughout: a strength session with no volume load at all. */
const CORE_STRUCTURE: Schemas["StrengthStructureSchema"] = {
  discipline: "strength",
  groups: [
    {
      label: null,
      items: [
        {
          exercise_id: "hanging_leg_raise",
          sets: 4,
          reps: 12,
          load: { kind: "bodyweight", value: null },
          rir: null,
          rest_s: 60,
          tempo: null,
          notes: null,
        },
      ],
    },
  ],
};

// --- what the domain computes from them --------------------------------------

/**
 * Predictions as `app.domain.prediction` actually produces them.
 *
 * Recomputed rather than invented: the 1 Hz expansion, the 30 s rolling mean
 * and the midpoint rule between them make these unguessable, and the previous
 * fixture's round numbers were off by nearly a fifth. Every field below —
 * load, IF, coverage and the explanation's four inputs — came out of
 * `predict_endurance_load(structure, {ftp: 250 W estimated 2026-06-01})`.
 */
const VO2_PREDICTED_LOAD: Schemas["PredictedLoadRead"] = {
  load: 78.27591281316042,
  intensity_factor: 0.9077207593641538,
  // 2820 s of 3420 carried a power target; the cool-down carries none.
  coverage: 0.8245614035087719,
  anchor_version_id: FTP_VERSION_ID,
  explanation: {
    formula:
      "NP = mean(rolling_mean_30s(P)^4)^(1/4); IF = NP / FTP; " +
      "TSS = duration_s × IF² / 36",
    inputs: {
      FTP: "250 W (estimated, effective 2026-06-01)",
      "planned NP": "227 W over the prescribed watts",
      duration: "3420 s prescribed",
      coverage: "82.5% of the duration carried a power target",
    },
    assumptions: [
      "target ranges reduced to their midpoint",
      "steps with no power target counted as 0 W and left out of coverage",
    ],
    citation: "Allen & Coggan, Training and Racing with a Power Meter",
  },
};

const LONG_PREDICTED_LOAD: Schemas["PredictedLoadRead"] = {
  load: 134.41188060404838,
  intensity_factor: 0.6515048505794672,
  coverage: 1,
  anchor_version_id: FTP_VERSION_ID,
  explanation: {
    formula:
      "NP = mean(rolling_mean_30s(P)^4)^(1/4); IF = NP / FTP; " +
      "TSS = duration_s × IF² / 36",
    inputs: {
      FTP: "250 W (estimated, effective 2026-06-01)",
      "planned NP": "163 W over the prescribed watts",
      duration: "11400 s prescribed",
      coverage: "the full duration carried a power target",
    },
    // No second assumption: nothing was left out of the coverage.
    assumptions: ["target ranges reduced to their midpoint"],
    citation: "Allen & Coggan, Training and Racing with a Power Meter",
  },
};

/** Σ sets × reps × kg over the kilogram sets only: 3 × 8 × 80. */
const STRENGTH_PREDICTED_VOLUME: Schemas["PredictedVolumeRead"] = {
  volume_load_kg: 1920,
  total_sets: 10,
  // Three of the ten sets are prescribed in kilograms.
  coverage: 0.3,
};

const CORE_PREDICTED_VOLUME: Schemas["PredictedVolumeRead"] = {
  volume_load_kg: null,
  total_sets: 4,
  coverage: 0,
};

/** A power target said both ways, the way the backend resolves it. */
function powerTarget(
  pctLow: number,
  pctHigh: number,
): Schemas["ResolvedTargetRead"] {
  return {
    channel: "power",
    // Rounded, the way the backend renders it: `1.14 * 100` is
    // 114.00000000000001 in IEEE 754, and a fixture that says so is a fixture
    // testing floating point rather than the sheet.
    prescribed: `${Math.round(pctLow * 100)}–${Math.round(
      pctHigh * 100,
    )} % FTP`,
    resolved_low: Math.round(pctLow * FTP_WATTS * 10) / 10,
    resolved_high: Math.round(pctHigh * FTP_WATTS * 10) / 10,
    unit: "W",
    anchor_version_id: FTP_VERSION_ID,
  };
}

/** An absolute target: it needs no anchor, so it pins none. */
function absoluteTarget(
  channel: Schemas["Channel"],
  unit: Schemas["ChannelUnit"],
  low: number,
  high: number,
): Schemas["ResolvedTargetRead"] {
  return {
    channel,
    prescribed: `${low}–${high} ${unit}`,
    resolved_low: low,
    resolved_high: high,
    unit,
    anchor_version_id: null,
  };
}

function resolvedStep(
  index: number,
  role: Schemas["StepRole"],
  name: string,
  durationS: number,
  targets: Schemas["ResolvedTargetRead"][],
): Schemas["ResolvedStepRead"] {
  return {
    index,
    role,
    name,
    duration_s: durationS,
    distance_m: null,
    is_ramp: false,
    start_targets: targets,
    end_targets: targets,
  };
}

/** `VO2_STRUCTURE` flattened and resolved: repeat blocks expand. */
const VO2_RESOLVED_STEPS: Schemas["ResolvedStepRead"][] = [
  resolvedStep(0, "warmup", "Warm-up", 720, [powerTarget(0.5, 0.6)]),
  ...Array.from({ length: 5 }, (_, rep) => [
    resolvedStep(1 + rep * 2, "work", "VO₂ block", 240, [
      powerTarget(1.14, 1.22),
    ]),
    resolvedStep(2 + rep * 2, "rest", "Spin", 180, [powerTarget(0.4, 0.5)]),
  ]).flat(),
  // No target prescribed: nothing is claimed, not a zero-watt target.
  resolvedStep(11, "cooldown", "Cool-down", 600, []),
];

const LONG_RESOLVED_STEPS: Schemas["ResolvedStepRead"][] = [
  resolvedStep(0, "warmup", "Roll out", 900, [powerTarget(0.5, 0.6)]),
  resolvedStep(1, "work", "Endurance", 9600, [
    powerTarget(0.62, 0.72),
    absoluteTarget("hr", "bpm", 120, 148),
  ]),
  resolvedStep(2, "cooldown", "Spin home", 900, [powerTarget(0.4, 0.5)]),
];

const RECOVERY_RESOLVED_STEPS: Schemas["ResolvedStepRead"][] = [
  resolvedStep(0, "work", "Easy spin", 2700, [
    absoluteTarget("hr", "bpm", 100, 120),
    absoluteTarget("cadence", "rpm", 85, 95),
  ]),
];

// --- criteria ----------------------------------------------------------------

const VO2_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
    min_fraction: 0.75,
    selector: { kind: "role", role: "work", index: null },
  },
  {
    // Absolute, not `% of LTHR`: a percentage would oblige the intent to pin
    // an LTHR version, and this session pins FTP alone.
    kind: "ceiling",
    channel: "hr",
    limit: { kind: "absolute", unit: "bpm", value: 178 },
    max_seconds_above: 360,
    smoothing_s: 0,
  },
  { kind: "duration_floor", min_seconds: 3600 },
];

const ENDURANCE_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.92, high: 1.08, smoothing_s: 30 },
    min_fraction: 0.7,
    selector: { kind: "all", role: null, index: null },
  },
  { kind: "duration_floor", min_seconds: 5400 },
];

const RECOVERY_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  { kind: "duration_floor", min_seconds: 2400 },
];

const STRENGTH_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  { kind: "sets_completed", min_fraction: 0.9 },
  { kind: "load_within", pct_tolerance: 0.05 },
];

// --- the week ----------------------------------------------------------------

interface SessionSeed {
  readonly dayOffset: number;
  readonly session: Omit<
    Schemas["WeekSessionRead"],
    "date" | "completion_state"
  >;
  readonly structure: Schemas["SessionIntentRead"]["structure"];
  readonly criteria: Schemas["SessionIntentRead"]["success_criteria"];
  readonly resolvedSteps: Schemas["ResolvedStepRead"][];
  readonly predictedLoad: Schemas["PredictedLoadRead"] | null;
  readonly predictedVolume: Schemas["PredictedVolumeRead"] | null;
  /** Exactly the anchors the prescription and criteria refer to. */
  readonly pinnedAnchors: Schemas["PinnedAnchorRead"][];
  readonly coachNotes: string | null;
}

const SEEDS: readonly SessionSeed[] = [
  {
    dayOffset: 0,
    session: {
      id: SESSION_IDS.strength,
      discipline: "strength",
      purpose: "max_strength",
      status: "completed",
      // Titled because it was planned *from* the library workout below.
      title: "Strength — lower",
      workout_id: WORKOUT_IDS.lower,
      // A lift has no prescribed duration; 10 sets across 3 lines.
      planned_duration_s: null,
      total_sets: 10,
      step_count: 3,
      intent_text: "Keep the legs loaded through base.",
      intent_version: 1,
      predicted_load: null,
      predicted_intensity_factor: null,
      predicted_load_coverage: null,
      predicted_volume_load_kg: 1920,
      // No link, on a card that says `completed`. Consistent, not sloppy:
      // this fixture's week has nothing recorded in it (see the day builder
      // below), and `completed` with no link is exactly what the API produces
      // when the athlete marks a session done by hand — WP-6's link is what a
      // *recording* creates.
      matched_session_id: null,
      match_status: null,
    },
    structure: STRENGTH_STRUCTURE,
    criteria: STRENGTH_CRITERIA,
    resolvedSteps: [],
    predictedLoad: null,
    predictedVolume: STRENGTH_PREDICTED_VOLUME,
    pinnedAnchors: [],
    coachNotes: "Stop the squats the moment the bar speed drops.",
  },
  {
    dayOffset: 1,
    session: {
      id: SESSION_IDS.vo2,
      discipline: "cycling",
      purpose: "vo2max",
      status: "planned",
      title: "VO₂ 5×4′",
      workout_id: WORKOUT_IDS.vo2,
      // 720 + 5 × (240 + 180) + 600.
      planned_duration_s: 3420,
      total_sets: null,
      // The repeat expands: 1 + 10 + 1.
      step_count: 12,
      intent_text: "Open the top end without digging a hole.",
      intent_version: 2,
      predicted_load: VO2_PREDICTED_LOAD.load,
      predicted_intensity_factor: VO2_PREDICTED_LOAD.intensity_factor,
      // The card's coverage is the session's own, not a second measurement
      // of it: both come out of `predict_endurance_load` (D88).
      predicted_load_coverage: VO2_PREDICTED_LOAD.coverage,
      predicted_volume_load_kg: null,
      matched_session_id: null,
      match_status: null,
    },
    structure: VO2_STRUCTURE,
    criteria: VO2_CRITERIA,
    resolvedSteps: VO2_RESOLVED_STEPS,
    predictedLoad: VO2_PREDICTED_LOAD,
    predictedVolume: null,
    pinnedAnchors: [PINNED_FTP],
    coachNotes: "Two minutes in on the first one is where it is decided.",
  },
  {
    // Planned inline, so it has no title of its own: the card has to fall
    // back to the purpose.
    dayOffset: 2,
    session: {
      id: SESSION_IDS.recovery,
      discipline: "cycling",
      purpose: "recovery",
      status: "planned",
      title: null,
      workout_id: null,
      planned_duration_s: 2700,
      total_sets: null,
      step_count: 1,
      intent_text: null,
      intent_version: 1,
      // Prescribed off heart rate: nothing to integrate over watts.
      predicted_load: null,
      predicted_intensity_factor: null,
      predicted_load_coverage: null,
      predicted_volume_load_kg: null,
      matched_session_id: null,
      match_status: null,
    },
    structure: RECOVERY_STRUCTURE,
    criteria: RECOVERY_CRITERIA,
    resolvedSteps: RECOVERY_RESOLVED_STEPS,
    predictedLoad: null,
    predictedVolume: null,
    pinnedAnchors: [],
    coachNotes: null,
  },
  {
    dayOffset: 3,
    session: {
      id: SESSION_IDS.missed,
      discipline: "strength",
      purpose: "core",
      status: "missed",
      title: null,
      workout_id: null,
      planned_duration_s: null,
      total_sets: 4,
      step_count: 1,
      intent_text: "Not attempted.",
      intent_version: 1,
      predicted_load: null,
      predicted_intensity_factor: null,
      predicted_load_coverage: null,
      // Bodyweight throughout: kilograms exist once it is performed.
      predicted_volume_load_kg: null,
      matched_session_id: null,
      match_status: null,
    },
    structure: CORE_STRUCTURE,
    criteria: STRENGTH_CRITERIA,
    resolvedSteps: [],
    predictedLoad: null,
    predictedVolume: CORE_PREDICTED_VOLUME,
    pinnedAnchors: [],
    coachNotes: null,
  },
  {
    dayOffset: 5,
    session: {
      id: SESSION_IDS.long,
      discipline: "cycling",
      purpose: "endurance",
      status: "planned",
      title: "Long endurance",
      workout_id: WORKOUT_IDS.long,
      planned_duration_s: 11400,
      total_sets: null,
      step_count: 3,
      intent_text: "Build durability before the Ötztal.",
      intent_version: 1,
      predicted_load: LONG_PREDICTED_LOAD.load,
      predicted_intensity_factor: LONG_PREDICTED_LOAD.intensity_factor,
      predicted_load_coverage: LONG_PREDICTED_LOAD.coverage,
      predicted_volume_load_kg: null,
      matched_session_id: null,
      match_status: null,
    },
    structure: LONG_STRUCTURE,
    criteria: ENDURANCE_CRITERIA,
    resolvedSteps: LONG_RESOLVED_STEPS,
    predictedLoad: LONG_PREDICTED_LOAD,
    predictedVolume: null,
    pinnedAnchors: [PINNED_FTP],
    coachNotes:
      "Eat before you are hungry and the last hour looks after itself.",
  },
];

/**
 * `app.domain.scoring.completion_state`, line for line.
 *
 * Derived rather than typed onto each seed, for the reason every other total
 * in this fixture is: the API computes it, so a fixture that stated it by hand
 * could disagree with the payload the API produces — and, worse, could produce
 * a card whose `status` and `completion_state` contradict each other, which is
 * a week no service can answer with.
 *
 * The status leads, because it is the fact: a session the sweep marked
 * `missed` is missed whatever anyone later computes. Only a `completed`
 * session asks the verdict, and only then does the absence of one mean
 * `completed` — judged by nobody yet (D152).
 */
function completionState(
  status: Schemas["WeekSessionRead"]["status"],
  verdict: Schemas["Verdict"] | null,
): Schemas["CompletionState"] {
  if (status !== "completed" || verdict === null) {
    return status;
  }
  return VERDICT_STATES[verdict];
}

/** `app.domain.scoring.VERDICT_STATES`. */
const VERDICT_STATES: Readonly<
  Record<Schemas["Verdict"], Schemas["CompletionState"]>
> = {
  as_intended: "completed-as_intended",
  under: "under",
  over: "over",
  abandoned: "abandoned",
  different_session: "different_session",
};

/**
 * `app.domain.scoring.STATE_SEVERITY`, worst first: a day takes the worst of
 * its cards' states, so an abandoned session is never hidden behind a
 * completed one. `null` for a day with nothing planned and nothing recorded.
 */
const STATE_SEVERITY: readonly Schemas["CompletionState"][] = [
  "abandoned",
  "missed",
  "different_session",
  "displaced",
  "under",
  "over",
  "planned",
  "completed",
  "completed-as_intended",
  "unplanned",
];

function worstState(
  states: readonly Schemas["CompletionState"][],
): Schemas["CompletionState"] | null {
  return STATE_SEVERITY.find((state) => states.includes(state)) ?? null;
}

/**
 * Seven days from `start`, with the seeded sessions on their offsets.
 *
 * Every total is the same fold the service performs over the same cards
 * (`app.services.plan`), so the rail and the grid reconcile by construction
 * rather than by a number typed twice.
 *
 * `verdicts` is how a test asks for a **judged** week: the strip colours a
 * card by its completion state, and a state like `under` only exists because
 * somebody declared a verdict on a completed session. Passing the verdict and
 * deriving the state — rather than setting the state directly — is what stops
 * a test asserting against a card that says `planned` and `abandoned` at once.
 */
export function planWeekFixture(
  start: string,
  verdicts: Readonly<Record<string, Schemas["Verdict"]>> = {},
): Schemas["PlanWeekRead"] {
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = addDays(start, index);
    const sessions = SEEDS.filter((seed) => seed.dayOffset === index).map(
      (seed) => ({
        ...seed.session,
        date,
        completion_state: completionState(
          seed.session.status,
          verdicts[seed.session.id] ?? null,
        ),
      }),
    );
    return {
      date,
      sessions,
      // Nothing recorded in this fixture's week: the completed columns are
      // exercised by the session fixtures, and a planned-week fixture that
      // invented recordings would assert against a week no ingest could
      // produce.
      completed_session_count: 0,
      completed_duration_s: null,
      completed_load: null,
      completion_state: worstState(sessions.map((one) => one.completion_state)),
    };
  });
  const sessions = days.flatMap((day) => day.sessions);
  const byDiscipline = (["cycling", "strength"] as const)
    .map((discipline) => {
      const group = sessions.filter((s) => s.discipline === discipline);
      const sets = group.filter((s) => s.total_sets !== null);
      return {
        discipline,
        session_count: group.length,
        ...totals(group),
        ...NOTHING_COMPLETED,
        total_sets: sets.length
          ? sets.reduce((total, s) => total + (s.total_sets ?? 0), 0)
          : null,
      };
    })
    .filter((row) => row.session_count > 0);
  return {
    start,
    end: addDays(start, 6),
    days,
    session_count: sessions.length,
    ...totals(sessions),
    ...NOTHING_COMPLETED,
    completed_polarization_index: null,
    completed_polarization_not_assessed:
      "the polarization index needs time in all three bands; there was none " +
      "in the easy or moderate or hard band",
    completed_polarization_rule: ONE_CHANNEL_PER_SESSION_RULE,
    completed_polarization_sessions_counted: 0,
    completed_polarization_sessions_uncounted: 0,
    by_discipline: byDiscipline,
  };
}

/**
 * The A5.4 rule the weekly polarization index counts by, verbatim from
 * `app.domain.metrics.ONE_CHANNEL_PER_SESSION_RULE`. Copied rather than
 * paraphrased: the number is meaningless without the rule beside it, so a
 * fixture that softened the wording would assert against a payload the API
 * cannot produce.
 */
export const ONE_CHANNEL_PER_SESSION_RULE =
  "one channel per session — the same one the session's training load came " +
  "from (power where it was recorded, otherwise heart rate) — so no " +
  "session's duration is counted twice";

/**
 * A week in which nothing was recorded. Null, never 0: a zero completed load
 * reads as a rest week, and "nothing happened" is a different fact.
 */
const NOTHING_COMPLETED = {
  completed_session_count: 0,
  completed_duration_s: null,
  completed_load: null,
  completed_load_sessions_counted: 0,
  completed_load_sessions_uncounted: 0,
} as const;

/** The two totals and their coverage pairs. Null, never 0. */
function totals(of: readonly Schemas["WeekSessionRead"][]) {
  const timed = of.filter((s) => s.planned_duration_s !== null);
  const predicted = of.filter((s) => s.predicted_load !== null);
  return {
    planned_duration_s: timed.length
      ? timed.reduce((total, s) => total + (s.planned_duration_s ?? 0), 0)
      : null,
    duration_sessions_counted: timed.length,
    duration_sessions_uncounted: of.length - timed.length,
    planned_load: predicted.length
      ? predicted.reduce((total, s) => total + (s.predicted_load ?? 0), 0)
      : null,
    load_sessions_counted: predicted.length,
    load_sessions_uncounted: of.length - predicted.length,
  };
}

/**
 * The detail behind one card. Keyed by id so a test can open any of them.
 *
 * The date matches the day the current week's fixture puts the card on, so
 * opening a sheet from the calendar shows the date the card showed.
 */
export function plannedSessionFixture(
  sessionId: string,
): Schemas["PlannedSessionRead"] {
  const seed =
    SEEDS.find((s) => s.session.id === sessionId) ??
    (SEEDS[1] as (typeof SEEDS)[number]);
  return {
    id: seed.session.id,
    date: addDays(mondayOf(todayIsoDate()), seed.dayOffset),
    discipline: seed.session.discipline,
    status: seed.session.status,
    intent_versions: seed.session.intent_version,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-27T18:30:00Z",
    intent: {
      id: intentId(seed.session.id, seed.session.intent_version),
      // The versioned artefact *is* the planned session (D49): every version
      // of the intent hangs off the session's own id.
      artefact_id: seed.session.id,
      version: seed.session.intent_version,
      as_of: "2026-07-27T18:30:00Z",
      superseded_by: null,
      recompute_reason: null,
      edited_post_hoc: false,
      purpose: seed.session.purpose,
      intent_text: seed.session.intent_text,
      coach_notes: seed.coachNotes,
      workout_id: seed.session.workout_id,
      // Exactly the anchors resolved below — the domain refuses a pin the
      // prescription does not refer to, and a reference with no pin.
      pinned_anchor_versions: Object.fromEntries(
        seed.pinnedAnchors.map((anchor) => [
          anchor.anchor_type,
          anchor.anchor_version_id,
        ]),
      ),
      structure: seed.structure,
      success_criteria: seed.criteria,
      summary: {
        step_count: seed.session.step_count,
        total_duration_s: seed.session.planned_duration_s,
        total_sets: seed.session.total_sets,
      },
    },
    pinned_anchors: seed.pinnedAnchors,
    resolved_steps: seed.resolvedSteps,
    predicted_load: seed.predictedLoad,
    // The other axis: kilograms for a lift, nothing for a ride.
    predicted_volume: seed.predictedVolume,
  };
}

/**
 * A stable, well-formed uuid for version `n` of a session's intent.
 *
 * The session's own id with its last byte replaced, so it is a real uuid (the
 * schema says `format: uuid` and the old `"<id>-intent"` was not one) and
 * still readably related to the session it belongs to.
 */
function intentId(sessionId: string, version: number): string {
  return `${sessionId.slice(0, -2)}${(0xe0 + version).toString(16)}`;
}

// --- library, catalogue and templates ----------------------------------------

/**
 * The three workouts the library tests search through.
 *
 * The same three documents the seeded sessions were planned from, so a card's
 * title, its step count and its duration all agree with the library entry it
 * names — which is the only way they can agree in the real API, where the
 * title *is* the workout's name.
 */
export const WORKOUTS: Schemas["WorkoutRead"][] = [
  {
    id: WORKOUT_IDS.vo2,
    name: "VO₂ 5×4′",
    description: "Five hard fours with three easy between.",
    discipline: "cycling",
    folder: "Intervals",
    tags: ["vo2max", "indoor"],
    structure: VO2_STRUCTURE,
    summary: { step_count: 12, total_duration_s: 3420, total_sets: null },
    created_at: "2026-07-01T09:00:00Z",
    updated_at: "2026-07-01T09:00:00Z",
  },
  {
    id: WORKOUT_IDS.long,
    name: "Long endurance",
    description: "Three and a half hours, flat as you can find.",
    discipline: "cycling",
    folder: "Base",
    tags: ["endurance"],
    structure: LONG_STRUCTURE,
    summary: { step_count: 3, total_duration_s: 11400, total_sets: null },
    created_at: "2026-06-20T09:00:00Z",
    updated_at: "2026-06-20T09:00:00Z",
  },
  {
    id: WORKOUT_IDS.lower,
    name: "Strength — lower",
    description: null,
    discipline: "strength",
    folder: "Gym",
    tags: ["lower"],
    structure: STRENGTH_STRUCTURE,
    summary: { step_count: 3, total_duration_s: null, total_sets: 10 },
    created_at: "2026-06-01T09:00:00Z",
    updated_at: "2026-06-01T09:00:00Z",
  },
];

export const WORKOUT_LABELS: Schemas["WorkoutLabelsRead"] = {
  folders: ["Base", "Gym", "Intervals"],
  tags: ["endurance", "indoor", "lower", "vo2max"],
};

/** A slice of the movement catalogue — enough to pick from and to name. */
export const EXERCISES: Schemas["ExerciseRead"][] = [
  {
    id: "back_squat",
    name: "Back Squat",
    category: "squat",
    unilateral: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "romanian_deadlift",
    name: "Romanian Deadlift",
    category: "hinge",
    unilateral: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "hanging_leg_raise",
    name: "Hanging Leg Raise",
    category: "core",
    unilateral: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

const ENDURANCE_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] =
  ENDURANCE_CRITERIA;

const VO2_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
    min_fraction: 0.85,
    selector: { kind: "role", role: "work", index: null },
  },
];

const STRENGTH_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] =
  STRENGTH_CRITERIA;

/**
 * A purpose's template. Three distinct shapes — endurance, VO₂max and the
 * strength family — so a test can watch the criteria list change when the
 * purpose does, and change *discipline* when it crosses the vocabulary.
 */
export function purposeTemplateFixture(
  purpose: Schemas["Purpose"],
): Schemas["PurposeTemplateRead"] {
  const strength = STRENGTH_PURPOSES.has(purpose);
  return {
    purpose,
    discipline: strength ? "strength" : "cycling",
    description: null,
    axes: strength
      ? ["completion", "sets_load"]
      : ["completion", "adherence", "discipline"],
    default_criteria: strength
      ? STRENGTH_DEFAULTS
      : purpose === "vo2max"
        ? VO2_DEFAULTS
        : ENDURANCE_DEFAULTS,
  };
}

const STRENGTH_PURPOSES = new Set<Schemas["Purpose"]>([
  "max_strength",
  "strength_endurance",
  "hypertrophy",
  "power",
  "core",
  "mobility",
  "conditioning",
]);

/**
 * The anchor version **in force**, which is deliberately not the one any
 * planned session pinned.
 *
 * 265 W and tested, against the sessions' pinned 250 W estimate. Nothing in
 * the application should reach for this to render a planned session; the
 * fixture exists so a test can prove that it doesn't.
 */
export function anchorVersionFixture(
  anchorType: Schemas["AnchorType"],
): Schemas["AnchorVersionRead"] {
  const values: Partial<
    Record<
      Schemas["AnchorType"],
      [number, Schemas["AnchorUnit"], Schemas["Provenance"], string | null]
    >
  > = {
    // A `tested` version without a protocol is refused by the domain: a
    // tested value that cannot say how it was tested cannot be compared with
    // the next test.
    ftp: [265, "W", "tested", "20 min × 0.95"],
    lthr: [162, "bpm", "athlete_reported", null],
    max_hr: [188, "bpm", "estimated", null],
  };
  const [value, unit, provenance, protocol] = values[anchorType] ?? [
    265,
    "W",
    "estimated",
    null,
  ];
  return {
    id: "0199a000-0000-7000-8000-0000000000f2",
    anchor_type: anchorType,
    value,
    unit,
    provenance,
    source: "athlete",
    staleness_state: "fresh",
    effective_date: "2026-07-15",
    protocol,
    ci_low: null,
    ci_high: null,
    created_at: "2026-07-15T09:00:00Z",
  };
}

/** One library workout, by id — the editor's GET. */
export function workoutFixture(workoutId: string): Schemas["WorkoutRead"] {
  return (
    WORKOUTS.find((workout) => workout.id === workoutId) ??
    (WORKOUTS[0] as Schemas["WorkoutRead"])
  );
}

// --- WP-4: ingestion, quarantine and the session log -------------------------

/**
 * The completed sessions the log is built from, and the files behind them.
 *
 * Same rule as the week above — **the real API could have produced every byte**
 * — and for these rows that rule has arithmetic in it:
 *
 * * a device session's `duration_s` **is** the sum of its recordings'
 *   `recording_time_s` (`app.api.routes.activity._duration`), and
 *   `recording_time_s` on the row repeats it; a manual session's duration is
 *   wall clock and its `recording_time_s` is null, because there were no
 *   pauses to subtract;
 * * `elapsed_time_s − recording_time_s` equals the total length of the
 *   `recording_stops`, which are half-open row ranges on the 1 Hz grid (D89) —
 *   so the coffee stop below is 600 rows and 600 seconds;
 * * `end_time − start_time` is the **elapsed** time, not the recording time;
 * * `local_date` is the date of `start_time` read in `timezone`;
 * * a channel absent from `channels` has a null source and no candidates, and
 *   a source that had one candidate carries the rule `"only candidate"` —
 *   the strings are `app.ingest.parsers.base`'s own;
 * * `anomaly_count` counts **repairs**, so a clean trainer file is 0 (D99).
 */
export const ACTIVITY_IDS = {
  outdoorRide: "0199a000-0000-7000-8000-000000000101",
  trainerRide: "0199a000-0000-7000-8000-000000000102",
  gym: "0199a000-0000-7000-8000-000000000103",
} as const;

export const QUARANTINE_IDS = {
  /** The overlap duplicate: the only record that may be rejected. */
  duplicate: "0199a000-0000-7000-8000-000000000201",
  /** A file no parser could open: confirm-only, reject is a 409. */
  unreadable: "0199a000-0000-7000-8000-000000000202",
  /** Already dealt with, so it sits below the queue. */
  discarded: "0199a000-0000-7000-8000-000000000203",
} as const;

const RECORDING_IDS = {
  outdoorRide: "0199a000-0000-7000-8000-000000000301",
  trainerRide: "0199a000-0000-7000-8000-000000000302",
} as const;

/**
 * Details the pipeline really writes, quoted rather than invented.
 *
 * A `detail` is the parser's own sentence, truncated to `MAX_DETAIL_LENGTH`
 * and stored (`app.ingest.pipeline._refuse_whole_file`). A fixture that puts a
 * shorter, tidier sentence there is testing a message no backend emits — and
 * these two in particular are the strings a *row* is read for, so the row was
 * being asserted against copy that does not exist.
 */
export const DETAILS = {
  /** `app.ingest.parsers.fit._decode`, for bytes that are not a FIT file. */
  unreadableFit:
    "the file is not a readable FIT recording: no samples could be " +
    "decoded from it (not a FIT file @ 0; the Garmin decoder said: not a " +
    "FIT file)",
  /** `app.ingest.parsers.parse`, for an extension no parser is registered for. */
  noParser: (filename: string) =>
    `'${filename}' is not a file type this application reads ` +
    "(expected one of: fit, gpx, tcx)",
} as const;

/** sha256 is 64 hex digits; these are the shape the column actually holds. */
const HASHES = {
  outdoorRide:
    "1f3a9c0e7b5d24681f3a9c0e7b5d24681f3a9c0e7b5d24681f3a9c0e7b5d2468",
  trainerRide:
    "2b7e15163ad2a6db2b7e15163ad2a6db2b7e15163ad2a6db2b7e15163ad2a6db",
  wahooCopy: "9d4c6f21ae08b3579d4c6f21ae08b3579d4c6f21ae08b3579d4c6f21ae08b357",
  corrupt: "c0ffee11deadbeefc0ffee11deadbeefc0ffee11deadbeefc0ffee11deadbeef",
  shortLap: "77aa33bb99cc55dd77aa33bb99cc55dd77aa33bb99cc55dd77aa33bb99cc55dd",
} as const;

/** The coffee stop: 600 rows of the 1 Hz grid, and therefore 600 seconds. */
const COFFEE_STOP = { start_index: 3600, end_index: 4200 } as const;
const OUTDOOR_ELAPSED_S = 9540;
const OUTDOOR_RECORDING_S =
  OUTDOOR_ELAPSED_S - (COFFEE_STOP.end_index - COFFEE_STOP.start_index);

const OUTDOOR_RECORDING: Schemas["RecordingRead"] = {
  id: RECORDING_IDS.outdoorRide,
  file_hash: HASHES.outdoorRide,
  file_sport_index: 0,
  original_ext: "fit",
  sport: "cycling",
  elapsed_time_s: OUTDOOR_ELAPSED_S,
  recording_time_s: OUTDOOR_RECORDING_S,
  recording_stops: [COFFEE_STOP],
  median_time_delta_s: 1,
  moving_time_s: 8712,
  power_source_candidates: ["Quarq DZero", "Garmin Edge 830"],
  power_source: "Quarq DZero",
  // The tie-break FIT forces on us, spelled as the parser spells it (D96).
  power_source_rule: "lowest device_index among 2 candidates",
  hr_source_candidates: ["Garmin HRM-Pro"],
  hr_source: "Garmin HRM-Pro",
  hr_source_rule: "only candidate",
  channels: ["power", "hr", "cadence", "speed", "elevation", "lat", "lon"],
  anomaly_count: 3,
  created_at: "2026-08-05T07:55:12Z",
};

/** A clean indoor file: no GPS, no heart rate, nothing to repair. */
const TRAINER_RECORDING: Schemas["RecordingRead"] = {
  id: RECORDING_IDS.trainerRide,
  file_hash: HASHES.trainerRide,
  file_sport_index: 0,
  original_ext: "fit",
  sport: "virtual_ride",
  elapsed_time_s: 3600,
  recording_time_s: 3600,
  recording_stops: [],
  median_time_delta_s: 1,
  moving_time_s: 3600,
  power_source_candidates: ["Wahoo KICKR"],
  power_source: "Wahoo KICKR",
  power_source_rule: "only candidate",
  hr_source_candidates: [],
  hr_source: null,
  hr_source_rule: null,
  channels: ["power", "cadence", "speed"],
  anomaly_count: 0,
  created_at: "2026-08-03T17:10:00Z",
};

const GYM_SETS: Schemas["LoggedSetRead"][] = [
  {
    id: "0199a000-0000-7000-8000-000000000401",
    set_index: 0,
    exercise_id: "back_squat",
    exercise_name: "Back Squat",
    reps: 5,
    load_kg: 100,
    rir: 2,
    notes: null,
  },
  {
    id: "0199a000-0000-7000-8000-000000000402",
    set_index: 1,
    exercise_id: "back_squat",
    exercise_name: "Back Squat",
    reps: 5,
    load_kg: 102.5,
    rir: 1,
    notes: null,
  },
  {
    // Free text rather than a catalogue entry: the API allows either, and a
    // set with no load is bodyweight rather than zero kilos.
    id: "0199a000-0000-7000-8000-000000000403",
    set_index: 2,
    exercise_id: null,
    exercise_name: "Pull-up",
    reps: 8,
    load_kg: null,
    rir: null,
    notes: "strict",
  },
];

/**
 * The outdoor ride's metric artefact and stream payload, **generated**.
 *
 * `backend/scripts/emit_metrics_fixture.py` runs the real domain over a
 * synthetic 1 Hz session and emits both halves together, so NP, IF, TSS, the
 * zone distribution and the detected intervals in the fixture agree with each
 * other and with the streams a test can plot. A hand-typed metric block would
 * type-check and describe a ride no pipeline could produce, and the component
 * test would then agree with the fixture rather than with the application.
 */
export { RIDE_METRICS, RIDE_STREAMS };

/** The three sessions the log starts with, newest first. */
function seedSessions(): Schemas["SessionRead"][] {
  return [
    {
      id: ACTIVITY_IDS.gym,
      local_date: "2026-08-06",
      start_time: "2026-08-06T16:30:00Z",
      end_time: "2026-08-06T17:30:00Z",
      timezone: "Europe/Zurich",
      discipline: "strength",
      classification_source: "manual",
      discipline_overridden: false,
      recording_kind: "manual",
      status: "unmatched",
      duration_s: 3600,
      recording_time_s: null,
      rpe: 7,
      notes: "Felt strong; added a set of pull-ups at the end.",
      // WP-5: nothing computed yet — a real state the page has an
      // action for, not a placeholder.
      load: null,
      load_basis: null,
      metrics: null,
      recordings: [],
      logged_sets: GYM_SETS,
      created_at: "2026-08-06T17:34:00Z",
      updated_at: "2026-08-06T17:34:00Z",
    },
    {
      id: ACTIVITY_IDS.outdoorRide,
      local_date: "2026-08-05",
      start_time: "2026-08-05T05:14:00Z",
      end_time: "2026-08-05T07:53:00Z",
      timezone: "Europe/Zurich",
      discipline: "cycling",
      classification_source: "sport_field",
      discipline_overridden: false,
      recording_kind: "device",
      status: "unmatched",
      duration_s: OUTDOOR_RECORDING_S,
      recording_time_s: OUTDOOR_RECORDING_S,
      rpe: null,
      notes: null,
      // The one session with an artefact. Its load and basis are read off
      // that artefact rather than typed beside it, so the row and the page it
      // opens cannot disagree.
      load: RIDE_METRICS.load.training_load ?? null,
      load_basis: RIDE_METRICS.load.load_basis ?? null,
      metrics: RIDE_METRICS,
      recordings: [OUTDOOR_RECORDING],
      logged_sets: [],
      created_at: "2026-08-05T07:55:12Z",
      updated_at: "2026-08-05T07:55:12Z",
    },
    {
      id: ACTIVITY_IDS.trainerRide,
      local_date: "2026-08-03",
      start_time: "2026-08-03T16:02:00Z",
      end_time: "2026-08-03T17:02:00Z",
      // The offset form a head unit's local_timestamp implies (D93).
      timezone: "UTC+02:00",
      discipline: "cycling",
      classification_source: "heuristic",
      discipline_overridden: false,
      recording_kind: "device",
      status: "unmatched",
      duration_s: 3600,
      recording_time_s: 3600,
      rpe: null,
      notes: null,
      // WP-5: nothing computed yet — a real state the page has an
      // action for, not a placeholder.
      load: null,
      load_basis: null,
      metrics: null,
      recordings: [TRAINER_RECORDING],
      logged_sets: [],
      created_at: "2026-08-03T17:10:00Z",
      updated_at: "2026-08-03T17:10:00Z",
    },
  ];
}

/** A detail response, projected onto the row the list endpoint returns. */
export function toListItem(
  session: Schemas["SessionRead"],
): Schemas["SessionListItem"] {
  const {
    // `metrics` is detail-only; `load` and `load_basis` are on both shapes
    // and stay, so a row and the page it opens cannot disagree about them.
    metrics: _metrics,
    recordings: _recordings,
    logged_sets: _sets,
    notes: _notes,
    end_time: _end,
    created_at: _created,
    updated_at: _updated,
    ...row
  } = session;
  return row;
}

function seedQuarantine(): Schemas["QuarantineRecordRead"][] {
  return [
    {
      id: QUARANTINE_IDS.duplicate,
      // A second head unit's copy of the same ride: a different file, so a
      // different hash — which is why it reached the *overlap* check at all.
      original_filename: "wahoo-2026-08-05.fit",
      file_hash: HASHES.wahooCopy,
      file_sport_index: 0,
      reason: "suspected_duplicate",
      detail:
        "87% of this activity's time range overlaps the session already recorded on 2026-08-05; confirm to discard it, or reject to keep both",
      status: "pending",
      suspected_session_id: ACTIVITY_IDS.outdoorRide,
      created_at: "2026-08-06T06:12:31Z",
      resolved_at: null,
    },
    {
      id: QUARANTINE_IDS.unreadable,
      original_filename: "corrupt-export.fit",
      file_hash: HASHES.corrupt,
      // Nothing parsed, so there is no activity to have an index.
      file_sport_index: null,
      reason: "unreadable_file",
      detail: DETAILS.unreadableFit,
      status: "pending",
      suspected_session_id: null,
      created_at: "2026-08-06T06:12:33Z",
      resolved_at: null,
    },
    {
      id: QUARANTINE_IDS.discarded,
      original_filename: "2026-07-30-lap.fit",
      file_hash: HASHES.shortLap,
      file_sport_index: 0,
      reason: "too_short",
      detail: "the activity lasts 74 s; at least 120 s is needed for a session",
      status: "confirmed_discarded",
      suspected_session_id: null,
      created_at: "2026-07-30T18:44:02Z",
      // A resolved record has a resolution time; a pending one does not.
      resolved_at: "2026-07-31T08:00:00Z",
    },
  ];
}

function seedEvents(): Schemas["IngestEventRead"][] {
  const meaningful: Schemas["IngestEventRead"][] = [
    {
      id: "0199a000-0000-7000-8000-000000000501",
      at: "2026-08-06T06:12:33Z",
      filename: "corrupt-export.fit",
      file_hash: HASHES.corrupt,
      outcome: "quarantined",
      detail: DETAILS.unreadableFit,
      session_id: null,
    },
    {
      id: "0199a000-0000-7000-8000-000000000502",
      at: "2026-08-06T06:12:31Z",
      filename: "wahoo-2026-08-05.fit",
      file_hash: HASHES.wahooCopy,
      outcome: "quarantined",
      detail: "0 session(s) ingested, 1 quarantined",
      session_id: null,
    },
    {
      id: "0199a000-0000-7000-8000-000000000503",
      at: "2026-08-05T08:02:10Z",
      filename: "2026-08-05-morning-ride.fit",
      file_hash: HASHES.outdoorRide,
      outcome: "duplicate_file",
      detail: "already ingested as 1 recording(s) of this file",
      session_id: ACTIVITY_IDS.outdoorRide,
    },
    {
      id: "0199a000-0000-7000-8000-000000000504",
      at: "2026-08-05T07:55:12Z",
      filename: "2026-08-05-morning-ride.fit",
      file_hash: HASHES.outdoorRide,
      outcome: "ingested",
      detail: "1 session(s) ingested, 0 quarantined",
      session_id: ACTIVITY_IDS.outdoorRide,
    },
    {
      id: "0199a000-0000-7000-8000-000000000505",
      at: "2026-08-03T17:10:00Z",
      filename: "trainer-2026-08-03.fit",
      file_hash: HASHES.trainerRide,
      outcome: "ingested",
      detail: "1 session(s) ingested, 0 quarantined",
      session_id: ACTIVITY_IDS.trainerRide,
    },
    {
      id: "0199a000-0000-7000-8000-000000000506",
      at: "2026-07-30T18:44:02Z",
      filename: "2026-07-30-lap.fit",
      file_hash: HASHES.shortLap,
      outcome: "quarantined",
      detail: "the activity lasts 74 s; at least 120 s is needed for a session",
      session_id: null,
    },
  ];
  // Enough history that the log is genuinely longer than one page. Every one
  // of these is the shape `_known_file` writes when a hash is already sitting
  // unresolved in quarantine: `duplicate_file`, no session, that sentence.
  //
  // And they are dated **inside the window in which that sentence was true**:
  // the file was quarantined at 18:44:02 on the 30th and the athlete discarded
  // it at 08:00 on the 31st, so a re-sighting saying "already waiting in
  // quarantine" belongs between those two instants and nowhere else. The
  // previous run sat on 2026-07-20…29 — before the record it claims to have
  // found existed, which is a payload the pipeline cannot produce.
  const rescans = Array.from({ length: 20 }, (_, index) => ({
    id: `0199a000-0000-7000-8000-0000000006${String(index).padStart(2, "0")}`,
    // 19:00 on the 30th, then every half hour: the last is 04:30 on the 31st,
    // three and a half hours before the record was resolved.
    at: rescanStamp(index),
    filename: "2026-07-30-lap.fit",
    file_hash: HASHES.shortLap,
    outcome: "duplicate_file" as const,
    detail: "already waiting in quarantine for a decision",
    session_id: null,
  }));
  return [...meaningful, ...rescans];
}

/** The instant of re-sighting `index`, half-hourly from 2026-07-30T19:00:00Z. */
function rescanStamp(index: number): string {
  const first = Date.UTC(2026, 6, 30, 19, 0, 0);
  return new Date(first + index * 30 * 60_000)
    .toISOString()
    .replace(".000", "");
}

/**
 * The ingest mock's state: what the pipeline has already seen.
 *
 * The quarantine handlers are the reason this exists. A confirm that answered
 * with a canned `confirmed_discarded` record could not fail when the page
 * confirms the wrong record, and a second confirm on the same record has to
 * be the 409 the API gives — which is only true if something remembers the
 * first one. So the handlers mutate this, and `resetMockState` (called from
 * `vitest.setup.ts` after every test) puts it back.
 */
export interface IngestMockState {
  /** Newest first, the way the list endpoint answers. */
  sessions: Schemas["SessionRead"][];
  /** Pending first, then resolved — the order the API sorts them in. */
  quarantine: Schemas["QuarantineRecordRead"][];
  /** Newest first. */
  events: Schemas["IngestEventRead"][];
  /**
   * The plan the recordings are matched against, in date order (WP-6).
   *
   * Here rather than beside the week fixture because a link *moves* a planned
   * session's status — `completed`, `displaced`, or back to exactly what it
   * was — and a status that lived in a pure function could not be moved.
   */
  planned: Schemas["PlannedSessionListItem"][];
  /** Newest first, the order `list_matches` answers in. */
  matches: MatchLinkRecord[];
  /** Content hash → the sessions that file was ingested as. The dedup key. */
  known: Map<string, string[]>;
  /** How many ids this run has minted, so each one is different. */
  minted: number;
}

function seedState(): IngestMockState {
  return {
    sessions: seedSessions(),
    quarantine: seedQuarantine(),
    events: seedEvents(),
    planned: seedPlanned(),
    matches: [],
    known: new Map(),
    minted: 0,
  };
}

/**
 * Built on first use, not at import: the seeds it reads are `const`s declared
 * further down the file, and a module-level `seededState()` runs before they
 * are initialised.
 */
let state: IngestMockState | null = null;

/**
 * A whole fresh state, links included.
 *
 * The links are attached in a second step because a link records the two
 * statuses it displaced, and reading those means reading the rows — which do
 * not exist until the first step has run. That ordering is not an accident of
 * the mock: it is the same reason the service reads both rows before it writes
 * a link.
 */
function seededState(): IngestMockState {
  const fresh = seedState();
  state = fresh;
  fresh.matches = seedMatches();
  for (const link of fresh.matches) {
    applyLinkStatuses(link);
  }
  return fresh;
}

/** The current mock state. Call it per request; it is replaced, not mutated. */
export function ingestState(): IngestMockState {
  return state ?? seededState();
}

/** Put the ingest mock back to its seed. Wired into the global `afterEach`. */
export function resetMockState(): void {
  state = seededState();
  resetScoringState();
  resetAgentState();
}

/** A fresh uuid-shaped id, so nothing minted twice collides. */
export function mintId(): string {
  ingestState().minted += 1;
  return `0199a000-0000-7000-8000-0000000009${String(ingestState().minted).padStart(2, "0")}`;
}

/**
 * A stable 64-hex digest of some bytes.
 *
 * Not sha256 — it does not have to be, it has to be a *function of the
 * content*, so that uploading the same file twice is a duplicate for the same
 * reason the real pipeline says it is, rather than because a handler was told
 * to say so.
 */
export function contentHash(text: string): string {
  let a = 0x811c9dc5;
  let b = 0x01000193;
  for (let index = 0; index < text.length; index += 1) {
    a = Math.imul(a ^ text.charCodeAt(index), 0x01000193) >>> 0;
    b = Math.imul(b + text.charCodeAt(index) + index, 0x85ebca6b) >>> 0;
  }
  // `>>> 0` is the whole point: `Math.imul` is *signed*, so without it roughly
  // two in five words came out negative and stringified with a leading `-` —
  // a "digest" containing a character sha256 cannot produce, which the schema
  // could not catch (it is a string either way) and which made
  // `Number.parseInt(hash.slice(0, 6), 16)` in `ingestedSessionFixture` return
  // NaN for the exact bytes `inbox.test.tsx` uploads.
  const word = (seed: number) =>
    (Math.imul(seed ^ (seed >>> 15), 0x2545f491) >>> 0)
      .toString(16)
      .padStart(8, "0");
  return [a, b, (a ^ b) >>> 0, Math.imul(a, 31) >>> 0]
    .map((seed) => `${word(seed)}${word(seed + 1)}`)
    .join("");
}

/**
 * A session built from an uploaded file, the way the pipeline would build one.
 *
 * The duration is derived from the file's own digest rather than chosen: a
 * mock cannot parse FIT, but it can make the answer a function of what was
 * posted, which is what keeps a test from passing against a file it never
 * sent.
 */
export function ingestedSessionFixture(
  hash: string,
  filename: string,
): Schemas["SessionRead"] {
  const seed = Number.parseInt(hash.slice(0, 6), 16);
  const elapsed = 1800 + (seed % 7200);
  const stop = seed % 2 === 0 ? [] : [{ start_index: 600, end_index: 900 }];
  const paused = stop.reduce(
    (total, range) => total + (range.end_index - range.start_index),
    0,
  );
  const recording = elapsed - paused;
  const start = new Date(Date.UTC(2026, 7, 7, 5, 0, 0));
  const id = mintId();
  return {
    id,
    local_date: "2026-08-07",
    start_time: start.toISOString().replace(".000", ""),
    end_time: new Date(start.getTime() + elapsed * 1000)
      .toISOString()
      .replace(".000", ""),
    timezone: "UTC",
    discipline: "cycling",
    classification_source: "sport_field",
    discipline_overridden: false,
    recording_kind: "device",
    status: "unmatched",
    duration_s: recording,
    recording_time_s: recording,
    rpe: null,
    notes: null,
    // WP-5: nothing computed yet — a real state the page has an action for,
    // not a placeholder.
    load: null,
    load_basis: null,
    metrics: null,
    recordings: [
      {
        id: mintId(),
        file_hash: hash,
        file_sport_index: 0,
        original_ext: filename.split(".").pop() ?? "fit",
        sport: "cycling",
        elapsed_time_s: elapsed,
        recording_time_s: recording,
        recording_stops: stop,
        median_time_delta_s: 1,
        moving_time_s: recording,
        power_source_candidates: ["Quarq DZero"],
        power_source: "Quarq DZero",
        power_source_rule: "only candidate",
        hr_source_candidates: [],
        hr_source: null,
        hr_source_rule: null,
        channels: ["power", "cadence", "speed"],
        anomaly_count: 0,
        created_at: "2026-08-07T07:00:00Z",
      },
    ],
    logged_sets: [],
    created_at: "2026-08-07T07:00:00Z",
    updated_at: "2026-08-07T07:00:00Z",
  };
}

// --- fixtures longer than one page -------------------------------------------
//
// A three-row list cannot fail the way a paged list fails: an offset that is
// never sent, a range that lies on the last page, an "Older" that stays
// enabled past the end are all invisible until there is a second page. These
// two build one.

/**
 * A quarantine queue longer than one request, in the order the API returns it.
 *
 * Pending first, then resolved, newest first within each — `list_quarantine`'s
 * own sort, and the fact the waiting count is derived from (`waitingLabel`).
 * Every record is a `too_short` lap, which is the one verdict that arrives in
 * bulk in real life: a head unit left recording between efforts.
 */
export function longQuarantineFixture(
  pendingCount: number,
  resolvedCount: number,
): Schemas["QuarantineRecordRead"][] {
  const record = (
    index: number,
    status: Schemas["QuarantineStatus"],
  ): Schemas["QuarantineRecordRead"] => {
    // Newest first: index 0 is the most recent, so the stamps run backwards.
    const created = new Date(Date.UTC(2026, 7, 6, 6, 0, 0) - index * 3_600_000);
    const seconds = 30 + (index % 80);
    return {
      id: `0199a000-0000-7000-8000-00000000${(0x7000 + index).toString(16)}`,
      original_filename: `lap-${String(index).padStart(3, "0")}.fit`,
      file_hash: contentHash(`lap-${index}`),
      file_sport_index: 0,
      reason: "too_short",
      detail: `the activity lasts ${seconds} s; at least 120 s is needed for a session`,
      status,
      suspected_session_id: null,
      created_at: created.toISOString().replace(".000", ""),
      // A resolved record has a resolution time; a pending one does not.
      resolved_at:
        status === "pending"
          ? null
          : new Date(created.getTime() + 3_600_000)
              .toISOString()
              .replace(".000", ""),
    };
  };
  return [
    ...Array.from({ length: pendingCount }, (_, index) =>
      record(index, "pending"),
    ),
    ...Array.from({ length: resolvedCount }, (_, index) =>
      record(pendingCount + index, "confirmed_discarded"),
    ),
  ];
}

/**
 * A session log longer than one page, newest first, with honest arithmetic.
 *
 * One device ride per day counting backwards from 2026-08-06. Every third one
 * has a stop in it, and where it does, `duration_s` is elapsed *minus* the
 * stop's rows — because that is what the API returns for a device session
 * (`_duration`), and a run of rows whose duration ignored their pauses would
 * be a page of sessions no pipeline could have produced.
 */
export function sessionRunFixture(count: number): Schemas["SessionRead"][] {
  return Array.from({ length: count }, (_, index) => {
    const start = new Date(Date.UTC(2026, 7, 6, 6, 0, 0) - index * 86_400_000);
    const elapsed = 3600 + (index % 5) * 600;
    const stops =
      index % 3 === 0 ? [{ start_index: 900, end_index: 900 + 120 }] : [];
    const paused = stops.reduce(
      (total, stop) => total + (stop.end_index - stop.start_index),
      0,
    );
    const recording = elapsed - paused;
    const stamp = (at: Date) => at.toISOString().replace(".000", "");
    const id = `0199a000-0000-7000-8000-00000000${(0x8000 + index).toString(16)}`;
    return {
      id,
      // Started at 06:00 UTC, so the UTC day and the local day agree.
      local_date: stamp(start).slice(0, 10),
      start_time: stamp(start),
      end_time: stamp(new Date(start.getTime() + elapsed * 1000)),
      timezone: "UTC",
      discipline: "cycling" as const,
      classification_source: "sport_field" as const,
      discipline_overridden: false,
      recording_kind: "device" as const,
      status: "unmatched" as const,
      duration_s: recording,
      recording_time_s: recording,
      rpe: null,
      notes: null,
      // WP-5: nothing computed yet — a real state the page has an action
      // for, not a placeholder.
      load: null,
      load_basis: null,
      metrics: null,
      recordings: [
        {
          id: `0199a000-0000-7000-8000-00000000${(0x9000 + index).toString(16)}`,
          file_hash: contentHash(`ride-${index}`),
          file_sport_index: 0,
          original_ext: "fit",
          sport: "cycling",
          elapsed_time_s: elapsed,
          recording_time_s: recording,
          recording_stops: stops,
          median_time_delta_s: 1,
          moving_time_s: recording,
          power_source_candidates: ["Quarq DZero"],
          power_source: "Quarq DZero",
          power_source_rule: "only candidate",
          hr_source_candidates: [],
          hr_source: null,
          hr_source_rule: null,
          channels: ["power", "cadence", "speed"],
          anomaly_count: 0,
          created_at: stamp(new Date(start.getTime() + elapsed * 1000)),
        },
      ],
      logged_sets: [],
      created_at: stamp(new Date(start.getTime() + elapsed * 1000)),
      updated_at: stamp(new Date(start.getTime() + elapsed * 1000)),
    };
  });
}

// --- the plan the recorded sessions are matched against (WP-6) ----------------
//
// A second, *fixed-date* set of planned sessions, deliberately separate from
// the week above. The week fixture is built around whatever Monday the
// calendar asks for, because a calendar test must not have to freeze the
// clock; a match, on the other hand, joins one planned session to one
// recording, and the three recordings in this file sit on 2026-08-03, -05 and
// -06. So the planned sessions a link can point at are dated around those, and
// `GET /planned-sessions` answers with them.
//
// Every duration, set count and work-step count below is a property of the
// structure document beside it, and the same numbers are what
// `backend/scripts/emit_matching_fixture.py` stated its evidence from — so a
// breakdown in `generated-matching.ts` and the row the picker shows describe
// the same prescription.

export const PLANNED_IDS = {
  /** 2026-08-05, the day of the outdoor ride: the proposal it carries. */
  vo2: "0199a000-0000-7000-8000-000000000501",
  /** 2026-08-04: what the outdoor ride's link is swapped to. */
  long: "0199a000-0000-7000-8000-000000000502",
  /** 2026-08-06, the day of the gym session: the proposal it carries. */
  strength: "0199a000-0000-7000-8000-000000000503",
  /** 2026-08-07: what the gym session's link is swapped to. */
  core: "0199a000-0000-7000-8000-000000000504",
  /** 2026-08-03, the evening of the trainer ride. */
  threshold: "0199a000-0000-7000-8000-000000000505",
} as const;

/**
 * 2 × 20′ at threshold: 600 + 1200 + 300 + 1200 + 600 = 3900 s, two work steps.
 *
 * The one prescription here that no other fixture already carries, and it
 * exists because the trainer hour needs something plausible to have been.
 */
const THRESHOLD_STRUCTURE: Schemas["EnduranceStructureSchema-Output"] = {
  discipline: "cycling",
  steps: [
    {
      kind: "steady",
      role: "warmup",
      name: "Wind up",
      duration_s: 600,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.55,
          pct_high: 0.65,
        },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "work",
      name: "Threshold 1",
      duration_s: 1200,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.95,
          pct_high: 1,
        },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "recovery",
      name: "Float",
      duration_s: 300,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.5,
          pct_high: 0.55,
        },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "work",
      name: "Threshold 2",
      duration_s: 1200,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.95,
          pct_high: 1,
        },
      },
      distance_m: null,
    },
    {
      kind: "steady",
      role: "cooldown",
      name: "Spin down",
      duration_s: 600,
      targets: {
        power: {
          kind: "percent_of_anchor",
          anchor_type: "ftp",
          pct_low: 0.45,
          pct_high: 0.55,
        },
      },
      distance_m: null,
    },
  ],
};

/** Five working sets over two lines — the gym session logged three of them. */
const FULL_BODY_STRUCTURE: Schemas["StrengthStructureSchema"] = {
  discipline: "strength",
  groups: [
    {
      label: null,
      items: [
        {
          exercise_id: "back_squat",
          sets: 3,
          reps: 5,
          load: { kind: "percent_e1rm", value: 0.8 },
          rir: 2,
          rest_s: 180,
          tempo: null,
          notes: null,
        },
      ],
    },
    {
      label: null,
      items: [
        {
          exercise_id: "romanian_deadlift",
          sets: 2,
          reps: 8,
          load: { kind: "kg", value: 80 },
          rir: 2,
          rest_s: 120,
          tempo: null,
          notes: null,
        },
      ],
    },
  ],
};

const THRESHOLD_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
    min_fraction: 0.8,
    selector: { kind: "role", role: "work", index: null },
  },
];

interface PlannedSeed {
  readonly id: string;
  readonly date: string;
  readonly discipline: Schemas["Discipline"];
  readonly purpose: Schemas["Purpose"];
  readonly intentText: string | null;
  readonly workoutId: string | null;
  readonly structure: Schemas["SessionIntentRead"]["structure"];
  readonly criteria: Schemas["SessionIntentRead"]["success_criteria"];
  readonly pinnedAnchors: Schemas["PinnedAnchorRead"][];
  readonly summary: Schemas["SessionIntentRead"]["summary"];
}

const PLANNED_SEEDS: readonly PlannedSeed[] = [
  {
    id: PLANNED_IDS.threshold,
    date: "2026-08-03",
    discipline: "cycling",
    purpose: "threshold",
    intentText: "Two twenties on the trainer, no heroics.",
    workoutId: null,
    structure: THRESHOLD_STRUCTURE,
    criteria: THRESHOLD_CRITERIA,
    pinnedAnchors: [PINNED_FTP],
    summary: { step_count: 5, total_duration_s: 3900, total_sets: null },
  },
  {
    id: PLANNED_IDS.long,
    date: "2026-08-04",
    discipline: "cycling",
    purpose: "endurance",
    intentText: "Build durability before the Ötztal.",
    workoutId: WORKOUT_IDS.long,
    structure: LONG_STRUCTURE,
    criteria: ENDURANCE_CRITERIA,
    pinnedAnchors: [PINNED_FTP],
    summary: { step_count: 3, total_duration_s: 11400, total_sets: null },
  },
  {
    id: PLANNED_IDS.vo2,
    date: "2026-08-05",
    discipline: "cycling",
    purpose: "vo2max",
    intentText: "Open the top end without digging a hole.",
    workoutId: WORKOUT_IDS.vo2,
    structure: VO2_STRUCTURE,
    criteria: VO2_CRITERIA,
    pinnedAnchors: [PINNED_FTP],
    summary: { step_count: 12, total_duration_s: 3420, total_sets: null },
  },
  {
    id: PLANNED_IDS.strength,
    date: "2026-08-06",
    discipline: "strength",
    purpose: "max_strength",
    intentText: "Squat, hinge, get out.",
    workoutId: null,
    structure: FULL_BODY_STRUCTURE,
    criteria: STRENGTH_CRITERIA,
    pinnedAnchors: [],
    summary: { step_count: 2, total_duration_s: null, total_sets: 5 },
  },
  {
    id: PLANNED_IDS.core,
    date: "2026-08-07",
    discipline: "strength",
    purpose: "core",
    intentText: null,
    workoutId: null,
    structure: CORE_STRUCTURE,
    criteria: STRENGTH_CRITERIA,
    pinnedAnchors: [],
    summary: { step_count: 1, total_duration_s: null, total_sets: 4 },
  },
];

/** The planned sessions `GET /planned-sessions` answers with, in date order. */
function seedPlanned(): Schemas["PlannedSessionListItem"][] {
  return PLANNED_SEEDS.map((seed) => ({
    id: seed.id,
    date: seed.date,
    discipline: seed.discipline,
    status: "planned",
    intent_versions: 1,
    created_at: "2026-07-30T09:00:00Z",
    updated_at: "2026-07-30T09:00:00Z",
    match: null,
    pinned_anchors: seed.pinnedAnchors,
    intent: {
      id: intentId(seed.id, 1),
      artefact_id: seed.id,
      version: 1,
      as_of: "2026-07-30T09:00:00Z",
      superseded_by: null,
      recompute_reason: null,
      edited_post_hoc: false,
      purpose: seed.purpose,
      intent_text: seed.intentText,
      coach_notes: null,
      workout_id: seed.workoutId,
      pinned_anchor_versions: Object.fromEntries(
        seed.pinnedAnchors.map((anchor) => [
          anchor.anchor_type,
          anchor.anchor_version_id,
        ]),
      ),
      structure: seed.structure,
      success_criteria: seed.criteria,
      summary: seed.summary,
    },
  }));
}

// --- links, and the three statuses that move together ------------------------

/**
 * One link, as the mock stores it.
 *
 * The two *contexts* a `MatchRead` carries are deliberately not stored: they
 * are projections of the session and the planned session as they stand right
 * now, and a stored copy would go stale the moment a confirm moved either
 * status — which is exactly the bug a test of the confirm flow exists to
 * catch.
 */
export interface MatchLinkRecord {
  id: string;
  session_id: string;
  planned_session_id: string;
  status: Schemas["MatchLinkStatus"];
  similarity: number | null;
  confirmed_at: string | null;
  created_by: string;
  previous_session_status: Schemas["SessionMatchStatus"];
  previous_planned_status: Schemas["app__domain__sessions__SessionStatus"];
  created_at: string;
  updated_at: string;
}

/**
 * The breakdown the domain produces for one pair, **stated**.
 *
 * Throws for a pair nothing has been generated for, for the reason
 * `statedLocalDate` throws: a mock cannot compute a similarity — that is the
 * domain's job, and reimplementing the renormalisation here would make every
 * test agree with the reimplementation. Add the pair to
 * `backend/scripts/emit_matching_fixture.py` and re-run `just
 * matching-fixture` instead.
 */
export function statedBreakdown(
  sessionId: string,
  plannedSessionId: string,
): Schemas["MatchBreakdownRead"] {
  const breakdown = MATCH_BREAKDOWNS[`${sessionId}|${plannedSessionId}`];
  if (breakdown === undefined) {
    throw new Error(
      `No generated breakdown for session ${sessionId} against planned ` +
        `session ${plannedSessionId}. Add the pair to ` +
        "backend/scripts/emit_matching_fixture.py and run `just " +
        "matching-fixture` rather than inventing a similarity here.",
    );
  }
  return breakdown;
}

/**
 * What re-running matching over one session decides, **stated**.
 *
 * `app.domain.matching.classify` turns a score into a verdict against two
 * thresholds; the mock states the verdict rather than applying the thresholds
 * itself, because a mock that reimplemented `classify` would agree with its
 * own copy of the rule instead of with the domain's. Each entry below is
 * consistent with the score `generated-matching.ts` carries for the same pair:
 * 0.92 is at or above the auto-link threshold, 0.69 and 0.60 are in the band
 * where arc proposes and asks.
 */
const STATED_REMATCH: Readonly<
  Record<string, { planned: string; status: Schemas["MatchLinkStatus"] }>
> = {
  [ACTIVITY_IDS.trainerRide]: {
    planned: PLANNED_IDS.threshold,
    status: "auto_high",
  },
  [ACTIVITY_IDS.outdoorRide]: { planned: PLANNED_IDS.vo2, status: "pending" },
  [ACTIVITY_IDS.gym]: { planned: PLANNED_IDS.strength, status: "pending" },
};

/** What a re-run would decide about one session, or nothing at all. */
export function statedRematch(
  sessionId: string,
): { planned: string; status: Schemas["MatchLinkStatus"] } | null {
  return STATED_REMATCH[sessionId] ?? null;
}

/** The two proposals the log opens with: one per discipline, both waiting. */
function seedMatches(): MatchLinkRecord[] {
  return [
    linkRecord({
      sessionId: ACTIVITY_IDS.outdoorRide,
      plannedSessionId: PLANNED_IDS.vo2,
      status: "pending",
      createdBy: "system",
      id: "0199a000-0000-7000-8000-000000000601",
    }),
    linkRecord({
      sessionId: ACTIVITY_IDS.gym,
      plannedSessionId: PLANNED_IDS.strength,
      status: "pending",
      createdBy: "system",
      id: "0199a000-0000-7000-8000-000000000602",
    }),
  ];
}

/**
 * A link, with the two statuses it displaced recorded on it.
 *
 * The previous statuses are read off the rows rather than assumed, because
 * that is what makes an unlink restore *exactly* what was there (WP-6.8) —
 * including a planned session that was already `missed`.
 */
export function linkRecord({
  sessionId,
  plannedSessionId,
  status,
  createdBy,
  id,
}: {
  sessionId: string;
  plannedSessionId: string;
  status: Schemas["MatchLinkStatus"];
  createdBy: string;
  id?: string;
}): MatchLinkRecord {
  const session = ingestState().sessions.find((row) => row.id === sessionId);
  const planned = ingestState().planned.find(
    (row) => row.id === plannedSessionId,
  );
  const athletes = status === "confirmed" || status === "displaced";
  return {
    id: id ?? mintId(),
    session_id: sessionId,
    planned_session_id: plannedSessionId,
    status,
    similarity: statedBreakdown(sessionId, plannedSessionId).score,
    confirmed_at: athletes ? MATCH_NOW : null,
    created_by: createdBy,
    previous_session_status: session?.status ?? "unmatched",
    previous_planned_status: planned?.status ?? "planned",
    created_at: MATCH_NOW,
    updated_at: MATCH_NOW,
  };
}

/** The instant the mock's match operations claim to have happened. */
export const MATCH_NOW = "2026-08-07T09:05:00Z";

/**
 * Move the two sides to where a link of this status puts them.
 *
 * The table is `app.services.matching`'s own, quoted rather than invented:
 * pending changes nothing on either side (a proposal is a question), a
 * confirmed or automatic link completes the planned session, and a displaced
 * one leaves it neither missed nor completed.
 */
export function applyLinkStatuses(link: MatchLinkRecord): void {
  const session = ingestState().sessions.find(
    (row) => row.id === link.session_id,
  );
  const planned = ingestState().planned.find(
    (row) => row.id === link.planned_session_id,
  );
  if (!session || !planned || link.status === "pending") {
    return;
  }
  if (link.status === "displaced") {
    session.status = "displaced";
    planned.status = "displaced";
    return;
  }
  session.status = "matched";
  planned.status = "completed";
}

/** Put both sides back to exactly what the link displaced (WP-6.8). */
export function restoreLinkStatuses(link: MatchLinkRecord): void {
  const session = ingestState().sessions.find(
    (row) => row.id === link.session_id,
  );
  const planned = ingestState().planned.find(
    (row) => row.id === link.planned_session_id,
  );
  if (session) {
    session.status = link.previous_session_status;
  }
  if (planned) {
    planned.status = link.previous_planned_status;
  }
}

/** The link one session carries, or null. */
export function linkForSession(sessionId: string): MatchLinkRecord | null {
  return (
    ingestState().matches.find((link) => link.session_id === sessionId) ?? null
  );
}

/** The link one planned session carries, or null. */
export function linkForPlanned(plannedId: string): MatchLinkRecord | null {
  return (
    ingestState().matches.find(
      (link) => link.planned_session_id === plannedId,
    ) ?? null
  );
}

/** The summary shape both joined resources carry. */
export function matchSummary(
  link: MatchLinkRecord | null,
): Schemas["MatchSummary"] | null {
  if (link === null) {
    return null;
  }
  const {
    id,
    session_id,
    planned_session_id,
    status,
    similarity,
    confirmed_at,
  } = link;
  return {
    id,
    session_id,
    planned_session_id,
    status,
    similarity,
    confirmed_at,
  };
}

/**
 * One link as its own resource, both sides projected as they stand now.
 *
 * Throws when either side has gone: the API loads both in one query and drops
 * a row it cannot resolve, and a mock that answered with half a proposal would
 * be describing a response no route can produce.
 */
export function matchRead(link: MatchLinkRecord): Schemas["MatchRead"] {
  const session = ingestState().sessions.find(
    (row) => row.id === link.session_id,
  );
  const planned = ingestState().planned.find(
    (row) => row.id === link.planned_session_id,
  );
  if (!session || !planned) {
    throw new Error(
      `Match ${link.id} has a side that is not in the mock state`,
    );
  }
  return {
    ...(matchSummary(link) as Schemas["MatchSummary"]),
    breakdown: statedBreakdown(link.session_id, link.planned_session_id),
    created_by: link.created_by,
    previous_session_status: link.previous_session_status,
    previous_planned_status: link.previous_planned_status,
    created_at: link.created_at,
    updated_at: link.updated_at,
    session: {
      id: session.id,
      local_date: session.local_date,
      discipline: session.discipline,
      status: session.status,
      duration_s: session.duration_s,
    },
    planned_session: {
      id: planned.id,
      date: planned.date,
      discipline: planned.discipline,
      purpose: planned.intent.purpose,
      status: planned.status,
      intent_text: planned.intent.intent_text,
    },
  };
}

/** A session as the API returns it: with whatever link it carries attached. */
export function withMatch<T extends { id: string }>(session: T): T {
  return { ...session, match: matchSummary(linkForSession(session.id)) };
}

/**
 * The other half of one ride, for the merge case (WP-6.5).
 *
 * Not seeded, because two device sessions on the same day is the *exception* —
 * seeding it would put a garage-door stop in every test of the log. A test
 * that wants the merge control calls this first.
 */
export function seedMergeCandidate(): Schemas["SessionRead"] {
  const half: Schemas["SessionRead"] = {
    id: "0199a000-0000-7000-8000-000000000701",
    local_date: "2026-08-05",
    // Twelve minutes after the outdoor ride's 07:53 local finish: one ride,
    // recorded as two files, well inside the six hours a merge will bridge.
    start_time: "2026-08-05T08:05:00Z",
    end_time: "2026-08-05T08:41:00Z",
    timezone: "Europe/Zurich",
    discipline: "cycling",
    classification_source: "sport_field",
    discipline_overridden: false,
    recording_kind: "device",
    status: "unmatched",
    duration_s: 2160,
    recording_time_s: 2160,
    rpe: null,
    notes: null,
    load: null,
    load_basis: null,
    metrics: null,
    recordings: [
      {
        id: "0199a000-0000-7000-8000-000000000702",
        file_hash:
          "5c1d8e02fa47b9635c1d8e02fa47b9635c1d8e02fa47b9635c1d8e02fa47b963",
        file_sport_index: 0,
        original_ext: "fit",
        sport: "cycling",
        elapsed_time_s: 2160,
        recording_time_s: 2160,
        recording_stops: [],
        median_time_delta_s: 1,
        moving_time_s: 2160,
        power_source_candidates: ["Quarq DZero"],
        power_source: "Quarq DZero",
        power_source_rule: "only candidate",
        hr_source_candidates: ["Garmin HRM-Pro"],
        hr_source: "Garmin HRM-Pro",
        hr_source_rule: "only candidate",
        channels: ["power", "hr", "cadence", "speed"],
        anomaly_count: 0,
        created_at: "2026-08-05T08:45:00Z",
      },
    ],
    logged_sets: [],
    created_at: "2026-08-05T08:45:00Z",
    updated_at: "2026-08-05T08:45:00Z",
  };
  ingestState().sessions.unshift(half);
  return half;
}

// --- WP-7: the score, the alignment and the athlete's verdict -----------------
//
// Kept out of the seed on purpose. A session is scored once its link is
// **settled** — a pending proposal is a question, not a link — and the three
// recordings this file seeds carry two open proposals and one unmatched ride.
// Seeding a score would therefore mean seeding a link that is not there, which
// is exactly the kind of quietly impossible state a fixture must not invent.
// A test that wants a judged session calls `seedScoredSession` and gets both.

/** One revision of the reasons behind a verdict, as the mock stores it. */
export interface ReasonsVersionRecord {
  version: number;
  recorded_at: string;
  revision_reason: string | null;
  reasons: Schemas["Reason"][];
  note: string | null;
  recorded_by: string;
}

/** The athlete's standing declaration, and whether the machine contests it. */
export interface DeclarationRecord {
  declared_verdict: Schemas["Verdict"];
  declared_at: string;
  suggested_at_declaration: Schemas["Verdict"] | null;
  contested: boolean;
  contested_at: string | null;
  contested_verdict: Schemas["Verdict"] | null;
  /** Append-only: the tip is what is in force, the rest is what was said. */
  reasons: ReasonsVersionRecord[];
}

/**
 * Everything one scored session is, as the mock stores it.
 *
 * The score and the alignment are **not** stored: they are looked up in
 * `generated-scoring.ts` by the offset in force, because the domain is what
 * produces them and a mock that kept its own copy would be free to drift from
 * it. What is stored is the mutable part — which offset is in force, how many
 * versions have been written, and what the athlete has said.
 */
export interface ScoringRecord {
  session_id: string;
  planned_session_id: string;
  offset_s: number;
  score_version: number;
  alignment_version: number;
  declaration: DeclarationRecord | null;
}

/** The instant the mock's scoring operations claim to have happened. */
export const SCORING_NOW = "2026-08-07T09:10:00Z";

const scoring = new Map<string, ScoringRecord>();

/** The scoring record one session carries, or null. */
export function scoringFor(sessionId: string): ScoringRecord | null {
  return scoring.get(sessionId) ?? null;
}

/** Drop every scoring record. Wired into `resetMockState`. */
export function resetScoringState(): void {
  scoring.clear();
}

/**
 * Settle a link and score the session behind it — the state WP-7 needs.
 *
 * Both halves together, because one without the other is not a state the API
 * can be in: a score belongs to a settled link, and a settled link on a
 * session the pipeline could score has one. The pair must exist in
 * `generated-scoring.ts`; asking for one that does not throws rather than
 * inventing a score, for the reason `statedBreakdown` throws.
 */
export function seedScoredSession(
  sessionId: string,
  plannedSessionId: string,
  offsetS = 0,
): ScoringRecord {
  statedScoring(sessionId, plannedSessionId, offsetS);
  const state = ingestState();
  const existing = linkForSession(sessionId);
  if (existing) {
    state.matches = state.matches.filter((row) => row.id !== existing.id);
    restoreLinkStatuses(existing);
  }
  const link = linkRecord({
    sessionId,
    plannedSessionId,
    status: "confirmed",
    createdBy: "athlete",
  });
  state.matches.unshift(link);
  applyLinkStatuses(link);
  const record: ScoringRecord = {
    session_id: sessionId,
    planned_session_id: plannedSessionId,
    offset_s: offsetS,
    score_version: 1,
    alignment_version: 1,
    declaration: null,
  };
  scoring.set(sessionId, record);
  return record;
}

/**
 * The score and the alignment for one pair at one offset, **stated**.
 *
 * Throws for a combination nothing has been generated for: a mock cannot
 * compute a score — that is the domain's job, and reimplementing the axes here
 * would make every test agree with the reimplementation. Add the offset to
 * `backend/scripts/emit_scoring_fixture.py` and run `just scoring-fixture`
 * instead.
 */
export function statedScoring(
  sessionId: string,
  plannedSessionId: string,
  offsetS: number,
): (typeof SCORED_PAIRS)[string] {
  const pair = SCORED_PAIRS[`${sessionId}|${plannedSessionId}|${offsetS}`];
  if (pair === undefined) {
    throw new Error(
      `No generated score for session ${sessionId} against planned session ` +
        `${plannedSessionId} at an offset of ${offsetS} s. Add it to ` +
        "backend/scripts/emit_scoring_fixture.py and run `just " +
        "scoring-fixture` rather than inventing a score here.",
    );
  }
  return pair;
}

/**
 * Flag a declaration the machine has come to disagree with (WP-7.4).
 *
 * `app.services.scoring._contest`'s rule, not a shortcut past it: contested
 * means the new suggestion contradicts what the athlete **declared** *and*
 * differs from what the machine was suggesting when they declared it. An
 * override of a suggestion that has not moved is not contested, and a mock
 * that let a test set the flag anyway would let the banner be tested against
 * a state the service never produces.
 */
export function contestDeclaration(
  sessionId: string,
  suggested: Schemas["Verdict"],
): void {
  const record = scoring.get(sessionId);
  const held = record?.declaration;
  if (!held) {
    throw new Error(`Session ${sessionId} has no declaration to contest.`);
  }
  if (
    suggested === held.declared_verdict ||
    suggested === held.suggested_at_declaration
  ) {
    throw new Error(
      `A suggestion of ${suggested} contests nothing: the athlete declared ` +
        `${held.declared_verdict} against a suggestion of ` +
        `${held.suggested_at_declaration}, and one of those is the same.`,
    );
  }
  held.contested = true;
  held.contested_at = SCORING_NOW;
  held.contested_verdict = suggested;
}

/** The unsuperseded reasons version, or null. */
export function reasonsTip(record: ScoringRecord): ReasonsVersionRecord | null {
  const chain = record.declaration?.reasons ?? [];
  return chain.length > 0 ? chain[chain.length - 1] : null;
}

/** One stored reasons version, on the wire. */
export function reasonsRead(
  version: ReasonsVersionRecord,
): Schemas["ReasonsRead"] {
  return { ...version, reasons: [...version.reasons] };
}

/** The declaration in force, with the reasons in force. */
export function declarationRead(
  record: ScoringRecord,
): Schemas["VerdictDeclarationRead"] | null {
  const held = record.declaration;
  if (!held) {
    return null;
  }
  const tip = reasonsTip(record);
  return {
    session_id: record.session_id,
    planned_session_id: record.planned_session_id,
    declared_verdict: held.declared_verdict,
    declared_at: held.declared_at,
    suggested_at_declaration: held.suggested_at_declaration,
    score_version_id: versionId(record.session_id, "sc", record.score_version),
    contested: held.contested,
    contested_at: held.contested_at,
    contested_verdict: held.contested_verdict,
    reasons: tip ? reasonsRead(tip) : null,
  };
}

/** One version of one session's score, as the API projects it. */
export function scoreRead(record: ScoringRecord): Schemas["SessionScoreRead"] {
  const pair = statedScoring(
    record.session_id,
    record.planned_session_id,
    record.offset_s,
  );
  return {
    ...pair.score,
    version: record.score_version,
    computed_at: SCORING_NOW,
    recompute_reason:
      record.score_version === 1 ? null : "the alignment offset was corrected",
    planned_session_id: record.planned_session_id,
    intent_version: 1,
    pinned_anchor_versions: { ftp: SCORED_FTP_VERSION_ID },
    metrics_version_id: versionId(record.session_id, "me", 1),
    alignment_version_id: versionId(
      record.session_id,
      "al",
      record.alignment_version,
    ),
  };
}

/** One version of one session's alignment, as the API projects it. */
export function alignmentRead(
  record: ScoringRecord,
): Schemas["SessionAlignmentRead"] {
  const pair = statedScoring(
    record.session_id,
    record.planned_session_id,
    record.offset_s,
  );
  return {
    ...pair.alignment,
    version: record.alignment_version,
    computed_at: SCORING_NOW,
    recompute_reason:
      record.alignment_version === 1
        ? null
        : "the athlete corrected the offset",
    planned_session_id: record.planned_session_id,
  };
}

/**
 * `app.domain.scoring`'s reason rule, as the **server** applies it.
 *
 * Restated here rather than trusted to the client: a mock that accepted any
 * list could not fail when the form stopped enforcing the rule, and the whole
 * point of a typed handler that honours its request is that the test can be
 * wrong about it.
 */
export function reasonsRefusal(
  verdict: Schemas["Verdict"],
  reasons: readonly Schemas["Reason"][],
): string | null {
  if (new Set(reasons).size !== reasons.length) {
    return "Each reason may appear once.";
  }
  if (reasons.length > 3) {
    return "At most three reasons, ordered by primacy.";
  }
  if (verdict !== "as_intended" && reasons.length < 1) {
    return `A verdict of ${verdict} needs one to three reasons, ordered by primacy.`;
  }
  return null;
}

/**
 * A stable, uuid-shaped id for version `n` of one facet of one session.
 *
 * Derived rather than minted so a re-render asks for the same id twice and
 * gets it: these are the ids a score reports having been computed *against*,
 * and one that changed on every read would make the artefact look recomputed.
 */
function versionId(
  sessionId: string,
  facet: keyof typeof FACET_CODES,
  version: number,
): string {
  return `${sessionId.slice(0, 24)}${FACET_CODES[facet]}${String(version).padStart(10, "0")}`;
}

/** Two hex digits per facet, so the ids stay uuid-shaped and stay apart. */
const FACET_CODES = { sc: "5c", al: "a1", me: "6e" } as const;

// --- WP-8: the coach's proposals, its notes, and the athlete's red flag ------
//
// Everything below is *agent-written*, and every byte of it is a payload the
// real API could produce. Two rules do the work:
//
//  * a diff's unchanged fields are **identical** on both sides, character for
//    character, because the backend computes the after-snapshot by applying a
//    change to the before-snapshot rather than by writing a second one out. A
//    fixture whose "unchanged" purpose differed in case would let a diff view
//    that marks every row as changed pass;
//  * a note carries exactly one subject — `session_id` or `plan_week`, never
//    both and never neither (`app.api.routes.agent_notes`).

export const PROPOSAL_IDS = {
  /** The only pending one: four changes, one of each kind. */
  pending: "0199a000-0000-7000-8000-000000000801",
  accepted: "0199a000-0000-7000-8000-000000000802",
  rejected: "0199a000-0000-7000-8000-000000000803",
  lapsed: "0199a000-0000-7000-8000-000000000804",
  /** Overtaken by `pending`, which supersedes it. */
  superseded: "0199a000-0000-7000-8000-000000000805",
} as const;

export const NOTE_IDS = {
  /** An evaluation of the outdoor ride, unrated. */
  rideEvaluation: "0199a000-0000-7000-8000-000000000811",
  /** An annotation on the same ride, already rated up. */
  rideAnnotation: "0199a000-0000-7000-8000-000000000812",
  /** An evaluation of the week as a whole. */
  weekEvaluation: "0199a000-0000-7000-8000-000000000813",
} as const;

/** The key label the coach's MCP key carries, as the audit actor spells it. */
export const COACH_ACTOR = "agent:coach";
/** The model the fixtures' notes and proposals were written by. */
export const COACH_MODEL = "claude-opus-4-6";
/** The instant the coach's fixtures claim to have been written at. */
export const AGENT_NOW = "2026-08-07T06:30:00Z";

/**
 * One planned session as a proposal snapshots it.
 *
 * Defaults to a plausible endurance ride, so a change states only the fields
 * it is *about* and the two sides of a diff cannot drift apart by accident.
 */
function snapshot(
  overrides: Partial<Schemas["ProposalSessionSnapshot"]> = {},
): Schemas["ProposalSessionSnapshot"] {
  return {
    date: "2026-08-13",
    discipline: "cycling",
    purpose: "endurance",
    status: "planned",
    workout_id: null,
    structure: {},
    intent_text: null,
    success_criteria: [],
    coach_notes: null,
    duration_s: null,
    total_sets: null,
    predicted_load: null,
    predicted_volume_kg: null,
    ...overrides,
  };
}

/**
 * The four changes the pending proposal carries — one of each kind.
 *
 * Built as `before` plus an explicit delta so every field the change does not
 * name is the same object value on both sides. The predicted loads are the
 * only numbers here and they move with the prescription they describe: the
 * VO2 session becomes a threshold session and its TSS-equivalent falls,
 * because a shorter time above threshold is less of it.
 */
function pendingChanges(): Schemas["ProposalChangeDiff"][] {
  const vo2Before = snapshot({
    date: "2026-08-13",
    purpose: "vo2max",
    workout_id: WORKOUT_IDS.vo2,
    intent_text: "Six by three at 118 %, full recoveries.",
    coach_notes: "Bail out if the third rep is under target.",
    predicted_load: 84,
  });
  const recoveryBefore = snapshot({
    date: "2026-08-12",
    purpose: "recovery",
    intent_text: "Spin the legs out. Nothing above 65 %.",
    predicted_load: 22,
  });
  const strengthBefore = snapshot({
    date: "2026-08-14",
    discipline: "strength",
    purpose: "max_strength",
    workout_id: WORKOUT_IDS.lower,
    intent_text: "Heavy triples. Leave two in reserve.",
    predicted_volume_kg: 6200,
  });

  return [
    {
      kind: "update",
      planned_session_id: SESSION_IDS.vo2,
      date: "2026-08-13",
      discipline: "cycling",
      expected_intent_version: 3,
      before: vo2Before,
      after: {
        ...vo2Before,
        purpose: "threshold",
        intent_text: "Three by ten at 98 %, five minutes easy between.",
        predicted_load: 61,
      },
    },
    {
      // The entry's `date` is where the session *is*, not where it would go:
      // the backend fills it from the row it read and only `after.date`
      // carries the target (`ProposalService._diff_one`). A fixture with
      // the target in both places is a shape the API cannot produce.
      kind: "move",
      planned_session_id: SESSION_IDS.recovery,
      date: "2026-08-12",
      discipline: "cycling",
      expected_intent_version: 1,
      before: recoveryBefore,
      after: { ...recoveryBefore, date: "2026-08-11" },
    },
    {
      kind: "create",
      planned_session_id: null,
      date: "2026-08-15",
      discipline: "cycling",
      expected_intent_version: null,
      before: null,
      after: snapshot({
        date: "2026-08-15",
        purpose: "endurance",
        intent_text: "Three hours steady, fuelled properly.",
        predicted_load: 148,
      }),
    },
    {
      kind: "delete",
      planned_session_id: SESSION_IDS.strength,
      date: "2026-08-14",
      discipline: "strength",
      expected_intent_version: 2,
      before: strengthBefore,
      after: null,
    },
  ];
}

/** A one-change diff, for the proposals that are only there to be filtered. */
function oneChange(
  date: string,
  intent: string,
): Schemas["ProposalChangeDiff"][] {
  const before = snapshot({ date, purpose: "tempo", predicted_load: 70 });
  return [
    {
      kind: "update",
      planned_session_id: SESSION_IDS.long,
      date,
      discipline: "cycling",
      expected_intent_version: 1,
      before,
      after: { ...before, intent_text: intent },
    },
  ];
}

/**
 * `changes` as the agent wrote it, beside the diff the backend computed.
 *
 * The API returns both and they are not the same thing: `changes` is the
 * request (what to do), `diff` is the answer (what it would do). The mock
 * derives one from the other so they cannot disagree.
 */
function requestOf(
  diff: readonly Schemas["ProposalChangeDiff"][],
): Record<string, unknown>[] {
  return diff.map((change) => ({
    kind: change.kind,
    planned_session_id: change.planned_session_id,
    // A move's request carries where the session should *go*; the diff entry's
    // `date` is where it is now (the backend fills that from the row it read),
    // so a request built off the entry date alone would ask to move a session
    // to the day it already sits on — a no-op the API would never have written.
    date:
      change.kind === "move" && change.after ? change.after.date : change.date,
    expected_intent_version: change.expected_intent_version,
  }));
}

function seedProposals(): Schemas["ProposalRead"][] {
  const pendingDiff = pendingChanges();
  const soon = `${addDays(todayIsoDate(), 3)}T12:00:00Z`;
  return [
    {
      id: PROPOSAL_IDS.pending,
      status: "pending",
      rationale:
        "Saturday's three hours is the week's real work and Thursday's VO2 block " +
        "sits two days ahead of it. Trade the intervals for threshold, bring the " +
        "spin forward, and put the long ride where you are fresh for it.",
      created_by: COACH_ACTOR,
      created_at: AGENT_NOW,
      expires_at: soon,
      changes: requestOf(pendingDiff),
      diff: pendingDiff,
      supersedes_id: PROPOSAL_IDS.superseded,
      superseded_by_id: null,
      resolved_at: null,
      resolution_note: null,
    },
    {
      id: PROPOSAL_IDS.accepted,
      status: "accepted",
      rationale: "Your Tuesday tempo has no stated intent. Here is one.",
      created_by: COACH_ACTOR,
      created_at: "2026-08-03T06:30:00Z",
      expires_at: "2026-08-06T06:30:00Z",
      changes: requestOf(oneChange("2026-08-04", "Ninety minutes at 88 %.")),
      diff: oneChange("2026-08-04", "Ninety minutes at 88 %."),
      supersedes_id: null,
      superseded_by_id: null,
      resolved_at: "2026-08-03T19:02:00Z",
      resolution_note: null,
    },
    {
      id: PROPOSAL_IDS.rejected,
      status: "rejected",
      rationale: "Add a fourth ride on Friday to lift the week's volume.",
      created_by: COACH_ACTOR,
      created_at: "2026-07-30T06:30:00Z",
      expires_at: "2026-08-02T06:30:00Z",
      changes: requestOf(oneChange("2026-07-31", "Ninety easy minutes.")),
      diff: oneChange("2026-07-31", "Ninety easy minutes."),
      supersedes_id: null,
      superseded_by_id: null,
      resolved_at: "2026-07-30T20:40:00Z",
      resolution_note:
        "Four rides in that week is not happening. Work is busy.",
    },
    {
      id: PROPOSAL_IDS.lapsed,
      status: "lapsed",
      rationale: "Swap Wednesday's spin for a rest day.",
      created_by: COACH_ACTOR,
      created_at: "2026-07-20T06:30:00Z",
      expires_at: "2026-07-23T06:30:00Z",
      changes: requestOf(oneChange("2026-07-22", "Rest.")),
      diff: oneChange("2026-07-22", "Rest."),
      supersedes_id: null,
      superseded_by_id: null,
      resolved_at: "2026-07-23T06:30:00Z",
      resolution_note: "Expired unanswered; the committed plan stands.",
    },
    {
      id: PROPOSAL_IDS.superseded,
      status: "superseded",
      rationale: "Move Thursday's VO2 block to Friday.",
      created_by: COACH_ACTOR,
      created_at: "2026-08-06T06:30:00Z",
      expires_at: "2026-08-09T06:30:00Z",
      changes: requestOf(oneChange("2026-08-13", "As written, one day later.")),
      diff: oneChange("2026-08-13", "As written, one day later."),
      supersedes_id: null,
      superseded_by_id: PROPOSAL_IDS.pending,
      resolved_at: AGENT_NOW,
      resolution_note: "Replaced by a later proposal for the same session.",
    },
  ];
}

function seedNotes(): Schemas["AgentNoteRead"][] {
  return [
    {
      id: NOTE_IDS.rideEvaluation,
      kind: "evaluation",
      session_id: ACTIVITY_IDS.outdoorRide,
      plan_week: null,
      text:
        "You held the tempo band for fifty of the sixty prescribed minutes and " +
        "the drift was under two per cent. The last ten went out the back — " +
        "that is a fuelling story, not a fitness one.",
      model_id: COACH_MODEL,
      created_by: COACH_ACTOR,
      created_at: AGENT_NOW,
      cites: [ACTIVITY_IDS.outdoorRide],
      dispute: null,
      disputed_at: null,
    },
    {
      id: NOTE_IDS.rideAnnotation,
      kind: "annotation",
      session_id: ACTIVITY_IDS.outdoorRide,
      plan_week: null,
      text: "Third ride this month with a coffee stop at the same hour. Noted, not judged.",
      model_id: COACH_MODEL,
      created_by: COACH_ACTOR,
      created_at: "2026-08-07T06:35:00Z",
      cites: [],
      dispute: "up",
      disputed_at: "2026-08-07T18:12:00Z",
    },
    {
      id: NOTE_IDS.weekEvaluation,
      kind: "evaluation",
      session_id: null,
      plan_week: mondayOf(todayIsoDate()),
      text:
        "Three of four sessions landed and the one that did not was the easiest. " +
        "The week did what it was for.",
      model_id: COACH_MODEL,
      created_by: COACH_ACTOR,
      created_at: AGENT_NOW,
      cites: [],
      dispute: null,
      disputed_at: null,
    },
  ];
}

/** The singleton profile, mutable because the red flag is set from the UI. */
function seedAthlete(): Schemas["AthleteRead"] {
  return {
    name: "Alex Rider",
    date_of_birth: "1990-06-15",
    sex: "male",
    height_cm: 181.5,
    capabilities: {},
    plan_state: "active",
    red_flag_active: false,
    red_flag_note: null,
    red_flag_severity: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

let agentState: {
  proposals: Schemas["ProposalRead"][];
  notes: Schemas["AgentNoteRead"][];
  athlete: Schemas["AthleteRead"];
} | null = null;

function agent() {
  if (!agentState) {
    agentState = {
      proposals: seedProposals(),
      notes: seedNotes(),
      athlete: seedAthlete(),
    };
  }
  return agentState;
}

/** Every proposal the mock holds, newest first — the order the API answers in. */
export function proposalList(): Schemas["ProposalRead"][] {
  return agent().proposals;
}

/** One proposal by id, or undefined. */
export function proposalById(id: string): Schemas["ProposalRead"] | undefined {
  return agent().proposals.find((proposal) => proposal.id === id);
}

/** Replace one proposal in place, and hand back what it became. */
export function updateProposal(
  id: string,
  patch: Partial<Schemas["ProposalRead"]>,
): Schemas["ProposalRead"] | undefined {
  const proposals = agent().proposals;
  const index = proposals.findIndex((proposal) => proposal.id === id);
  if (index < 0) {
    return undefined;
  }
  const next = { ...proposals[index], ...patch };
  proposals[index] = next;
  return next;
}

/** Every note the mock holds, oldest first — the order the API answers in. */
export function noteList(): Schemas["AgentNoteRead"][] {
  return agent().notes;
}

/** Rate one note, or clear its rating. Returns the note as it now stands. */
export function rateNote(
  id: string,
  rating: Schemas["DisputeRating"] | null,
  at: string,
): Schemas["AgentNoteRead"] | undefined {
  const notes = agent().notes;
  const index = notes.findIndex((note) => note.id === id);
  if (index < 0) {
    return undefined;
  }
  // `disputed_at` follows the rating rather than recording every tap: a
  // cleared rating has no instant, because there is nothing standing that was
  // said at one.
  const next = {
    ...notes[index],
    dispute: rating,
    disputed_at: rating === null ? null : at,
  };
  notes[index] = next;
  return next;
}

/** The profile as it now stands. */
export function athleteRecord(): Schemas["AthleteRead"] {
  return agent().athlete;
}

/**
 * Apply a PATCH the way the service does, including the red-flag rules.
 *
 * Two of them, and the mock honours both because a handler that did not would
 * let a form that never sends a severity pass: a flag that is up **must**
 * carry one, and lowering the flag clears the note and the severity — they
 * described an illness that is over. Clearing them means *omitting* them: a
 * PATCH that lowers the flag while still sending a note or a severity is
 * refused (422), because those two facts describe an illness the same request
 * says is over (`app.domain.athlete`, `AthleteProfile`).
 */
export function patchAthlete(
  body: Schemas["AthleteUpdate"],
): { athlete: Schemas["AthleteRead"] } | { detail: string } {
  const current = agent().athlete;
  const active = body.red_flag_active ?? current.red_flag_active;
  const next: Schemas["AthleteRead"] = {
    ...current,
    ...(body.name !== undefined ? { name: body.name } : {}),
    ...(body.plan_state !== undefined && body.plan_state !== null
      ? { plan_state: body.plan_state }
      : {}),
    red_flag_active: active,
    updated_at: AGENT_NOW,
  };
  if (!active) {
    if (body.red_flag_note != null || body.red_flag_severity != null) {
      return {
        detail:
          "red_flag_note and red_flag_severity may only be set while " +
          "red_flag_active is set",
      };
    }
    next.red_flag_note = null;
    next.red_flag_severity = null;
  } else {
    if (body.red_flag_severity !== undefined) {
      next.red_flag_severity = body.red_flag_severity;
    }
    if (body.red_flag_note !== undefined) {
      next.red_flag_note = body.red_flag_note;
    }
    if (next.red_flag_severity === null) {
      return {
        detail: "red_flag_severity is required while the red flag is active.",
      };
    }
  }
  agentState = { ...agent(), athlete: next };
  return { athlete: next };
}

/** Put the coach's proposals, notes and the profile back to their seed. */
export function resetAgentState(): void {
  agentState = null;
}
