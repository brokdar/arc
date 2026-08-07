import type { components } from "@/generated/api/schema";
import { addDays, mondayOf, todayIsoDate } from "@/lib/dates";

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
  readonly session: Omit<Schemas["WeekSessionRead"], "date">;
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
 * Seven days from `start`, with the seeded sessions on their offsets.
 *
 * Every total is the same fold the service performs over the same cards
 * (`app.services.plan`), so the rail and the grid reconcile by construction
 * rather than by a number typed twice.
 */
export function planWeekFixture(start: string): Schemas["PlanWeekRead"] {
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = addDays(start, index);
    return {
      date,
      sessions: SEEDS.filter((seed) => seed.dayOffset === index).map(
        (seed) => ({
          ...seed.session,
          date,
        }),
      ),
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
    by_discipline: byDiscipline,
  };
}

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
