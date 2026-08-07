import type { components } from "@/generated/api/schema";
import { addDays } from "@/lib/dates";

type Schemas = components["schemas"];

/**
 * A realistic week: the one the mockup draws, minus the modules that belong to
 * later work packages. Built *from* the requested start date so tests never
 * have to freeze the clock — the calendar asks for the Monday of the current
 * week and gets a week back with the same shape whichever Monday that is.
 */
export const SESSION_IDS = {
  strength: "0199a000-0000-7000-8000-000000000001",
  vo2: "0199a000-0000-7000-8000-000000000002",
  recovery: "0199a000-0000-7000-8000-000000000003",
  missed: "0199a000-0000-7000-8000-000000000004",
  long: "0199a000-0000-7000-8000-000000000005",
} as const;

interface SessionSeed {
  readonly dayOffset: number;
  readonly session: Omit<Schemas["WeekSessionRead"], "date">;
}

const SEEDS: readonly SessionSeed[] = [
  {
    dayOffset: 0,
    session: {
      id: SESSION_IDS.strength,
      discipline: "strength",
      purpose: "max_strength",
      status: "completed",
      title: "Strength — lower",
      workout_id: null,
      planned_duration_s: 2520,
      total_sets: 16,
      step_count: 4,
      intent_text: "Keep the legs loaded through base.",
      intent_version: 1,
      predicted_load: null,
      predicted_intensity_factor: null,
      predicted_volume_load_kg: 4080,
    },
  },
  {
    dayOffset: 1,
    session: {
      id: SESSION_IDS.vo2,
      discipline: "cycling",
      purpose: "vo2max",
      status: "planned",
      title: "VO₂ 5×4′",
      workout_id: "0199a000-0000-7000-8000-0000000000aa",
      planned_duration_s: 4140,
      total_sets: null,
      step_count: 11,
      intent_text: "Open the top end without digging a hole.",
      intent_version: 2,
      predicted_load: 92.4,
      predicted_intensity_factor: 0.9,
      predicted_volume_load_kg: null,
    },
  },
  {
    // No title of its own: the card has to fall back to the purpose.
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
      step_count: 3,
      intent_text: null,
      intent_version: 1,
      predicted_load: 28.1,
      predicted_intensity_factor: 0.55,
      predicted_volume_load_kg: null,
    },
  },
  {
    dayOffset: 3,
    session: {
      id: SESSION_IDS.missed,
      discipline: "strength",
      purpose: "core",
      status: "missed",
      title: "Strength — upper + core",
      workout_id: null,
      planned_duration_s: 2400,
      total_sets: 12,
      step_count: 3,
      intent_text: "Not attempted.",
      intent_version: 1,
      predicted_load: null,
      predicted_intensity_factor: null,
      predicted_volume_load_kg: null,
    },
  },
  {
    dayOffset: 5,
    session: {
      id: SESSION_IDS.long,
      discipline: "cycling",
      purpose: "endurance",
      status: "planned",
      title: "Long endurance",
      workout_id: null,
      planned_duration_s: 11400,
      total_sets: null,
      step_count: 6,
      intent_text: "Build durability before the Ötztal.",
      intent_version: 1,
      predicted_load: 168.3,
      predicted_intensity_factor: 0.73,
      predicted_volume_load_kg: null,
    },
  },
];

/** Seven days from `start`, with the seeded sessions on their offsets. */
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
  const duration = (of: readonly Schemas["WeekSessionRead"][]) =>
    of.reduce((total, session) => total + (session.planned_duration_s ?? 0), 0);
  const load = (of: readonly Schemas["WeekSessionRead"][]) => {
    const counted = of.filter((session) => session.predicted_load !== null);
    // Null, never 0: a week with nothing predictable has no load.
    return counted.length
      ? counted.reduce((total, s) => total + (s.predicted_load ?? 0), 0)
      : null;
  };
  const counted = sessions.filter((session) => session.predicted_load !== null);
  const byDiscipline = (["cycling", "strength"] as const)
    .map((discipline) => {
      const group = sessions.filter((s) => s.discipline === discipline);
      const sets = group.filter((s) => s.total_sets !== null);
      return {
        discipline,
        session_count: group.length,
        planned_duration_s: duration(group),
        planned_load: load(group),
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
    planned_duration_s: duration(sessions),
    planned_load: load(sessions),
    load_sessions_counted: counted.length,
    load_sessions_uncounted: sessions.length - counted.length,
    by_discipline: byDiscipline,
  };
}

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

const CYCLING_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
    min_fraction: 0.75,
    selector: { kind: "role", role: "work", index: null },
  },
  {
    kind: "ceiling",
    channel: "hr",
    limit: { kind: "absolute", unit: "bpm", value: 178 },
    max_seconds_above: 360,
    smoothing_s: 0,
  },
  { kind: "duration_floor", min_seconds: 3600 },
];

const STRENGTH_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  { kind: "sets_completed", min_fraction: 0.9 },
  { kind: "load_within", pct_tolerance: 0.05 },
];

/** The FTP version every cycling fixture's percentages resolve against. */
const FTP_VERSION_ID = "0199a000-0000-7000-8000-0000000000f1";
const FTP_WATTS = 250;

const PINNED_FTP: Schemas["PinnedAnchorRead"] = {
  anchor_type: "ftp",
  anchor_version_id: FTP_VERSION_ID,
  value: FTP_WATTS,
  unit: "W",
  provenance: "estimated",
  effective_date: "2026-06-01",
};

/** A power target said both ways, the way the backend resolves it. */
function powerTarget(
  pctLow: number,
  pctHigh: number,
): Schemas["ResolvedTargetRead"] {
  return {
    channel: "power",
    prescribed: `${pctLow * 100}\u2013${pctHigh * 100} % FTP`,
    resolved_low: Math.round(pctLow * FTP_WATTS * 10) / 10,
    resolved_high: Math.round(pctHigh * FTP_WATTS * 10) / 10,
    unit: "W",
    anchor_version_id: FTP_VERSION_ID,
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
    resolvedStep(1 + rep * 2, "work", "VO\u2082 block", 240, [
      powerTarget(1.14, 1.22),
    ]),
    resolvedStep(2 + rep * 2, "rest", "Spin", 180, [powerTarget(0.4, 0.5)]),
  ]).flat(),
  // No target prescribed: nothing is claimed, not a zero-watt target.
  resolvedStep(11, "cooldown", "Cool-down", 600, []),
];

const VO2_PREDICTED_LOAD: Schemas["PredictedLoadRead"] = {
  load: 92.4,
  intensity_factor: 0.9,
  coverage: 0.855,
  anchor_version_id: FTP_VERSION_ID,
  explanation: {
    formula:
      "NP = mean(rolling_mean_30s(P)^4)^(1/4); IF = NP / FTP; " +
      "TSS = duration_s \u00d7 IF\u00b2 / 36",
    inputs: {
      FTP: "250 W (estimated, effective 2026-06-01)",
      "planned NP": "225 W over the prescribed watts",
      duration: "4140 s prescribed",
      coverage: "86% of the duration carried a power target",
    },
    assumptions: [
      "target ranges reduced to their midpoint",
      "steps with no power target counted as 0 W and left out of coverage",
    ],
    citation: "Allen & Coggan, Training and Racing with a Power Meter",
  },
};

/** The detail behind one card. Keyed by id so a test can open any of them. */
export function plannedSessionFixture(
  sessionId: string,
): Schemas["PlannedSessionRead"] {
  const seed =
    SEEDS.find((s) => s.session.id === sessionId) ??
    (SEEDS[1] as (typeof SEEDS)[number]);
  const cycling = seed.session.discipline === "cycling";
  return {
    id: seed.session.id,
    date: "2026-08-01",
    discipline: seed.session.discipline,
    status: seed.session.status,
    intent_versions: seed.session.intent_version,
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-27T18:30:00Z",
    intent: {
      id: `${seed.session.id}-intent`,
      artefact_id: `${seed.session.id}-artefact`,
      version: seed.session.intent_version,
      as_of: "2026-07-27T18:30:00Z",
      superseded_by: null,
      recompute_reason: null,
      edited_post_hoc: false,
      purpose: seed.session.purpose,
      intent_text: seed.session.intent_text,
      coach_notes:
        "Eat before you are hungry and the last hour looks after itself.",
      workout_id: seed.session.workout_id,
      pinned_anchor_versions: { ftp: FTP_VERSION_ID },
      structure: cycling ? VO2_STRUCTURE : STRENGTH_STRUCTURE,
      success_criteria: cycling ? CYCLING_CRITERIA : STRENGTH_CRITERIA,
      summary: {
        step_count: seed.session.step_count,
        total_duration_s: seed.session.planned_duration_s,
        total_sets: seed.session.total_sets,
      },
    },
    // Resolved on read against the pins above — a strength prescription has
    // no anchor percentages, so it resolves to nothing.
    pinned_anchors: cycling ? [PINNED_FTP] : [],
    resolved_steps: cycling ? VO2_RESOLVED_STEPS : [],
    predicted_load: cycling ? VO2_PREDICTED_LOAD : null,
  };
}

// --- library, catalogue and templates ----------------------------------------

export const WORKOUT_IDS = {
  vo2: "0199a000-0000-7000-8000-0000000000aa",
  long: "0199a000-0000-7000-8000-0000000000bb",
  lower: "0199a000-0000-7000-8000-0000000000cc",
} as const;

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

/** The three workouts the library tests search through. */
export const WORKOUTS: Schemas["WorkoutRead"][] = [
  {
    id: WORKOUT_IDS.vo2,
    name: "VO₂ 5×4′",
    description: "Five hard fours with three easy between.",
    discipline: "cycling",
    folder: "Intervals",
    tags: ["vo2max", "indoor"],
    structure: VO2_STRUCTURE,
    summary: { step_count: 11, total_duration_s: 4140, total_sets: null },
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
    summary: { step_count: 3, total_duration_s: null, total_sets: 16 },
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

const ENDURANCE_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.92, high: 1.08, smoothing_s: 30 },
    min_fraction: 0.7,
    selector: { kind: "all", role: null, index: null },
  },
  { kind: "duration_floor", min_seconds: 5400 },
];

const VO2_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] = [
  {
    kind: "time_in_band",
    band: { channel: "power", low: 0.95, high: 1.05, smoothing_s: 30 },
    min_fraction: 0.85,
    selector: { kind: "role", role: "work", index: null },
  },
];

const STRENGTH_DEFAULTS: Schemas["PurposeTemplateRead"]["default_criteria"] = [
  { kind: "sets_completed", min_fraction: 0.9 },
  { kind: "load_within", pct_tolerance: 0.05 },
];

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

/** The FTP in force, so the Today view can resolve `% of FTP` into watts. */
export function anchorVersionFixture(
  anchorType: Schemas["AnchorType"],
): Schemas["AnchorVersionRead"] {
  const values: Partial<
    Record<Schemas["AnchorType"], [number, Schemas["AnchorUnit"]]>
  > = {
    ftp: [250, "W"],
    lthr: [162, "bpm"],
    max_hr: [188, "bpm"],
  };
  const [value, unit] = values[anchorType] ?? [250, "W"];
  return {
    id: `0199a000-0000-7000-8000-0000000000f1`,
    anchor_type: anchorType,
    value,
    unit,
    provenance: "tested",
    source: "athlete",
    staleness_state: "fresh",
    effective_date: "2026-06-01",
    protocol: null,
    ci_low: null,
    ci_high: null,
    created_at: "2026-06-01T09:00:00Z",
  };
}

/** One library workout, by id — the editor's GET. */
export function workoutFixture(workoutId: string): Schemas["WorkoutRead"] {
  return (
    WORKOUTS.find((workout) => workout.id === workoutId) ??
    (WORKOUTS[0] as Schemas["WorkoutRead"])
  );
}
