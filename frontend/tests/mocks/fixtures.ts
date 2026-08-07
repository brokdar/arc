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
    known: new Map(),
    minted: 0,
  };
}

let state: IngestMockState = seedState();

/** The current mock state. Call it per request; it is replaced, not mutated. */
export function ingestState(): IngestMockState {
  return state;
}

/** Put the ingest mock back to its seed. Wired into the global `afterEach`. */
export function resetMockState(): void {
  state = seedState();
}

/** A fresh uuid-shaped id, so nothing minted twice collides. */
export function mintId(): string {
  state.minted += 1;
  return `0199a000-0000-7000-8000-0000000009${String(state.minted).padStart(2, "0")}`;
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
