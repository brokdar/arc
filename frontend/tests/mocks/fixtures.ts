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
  return {
    start,
    end: addDays(start, 6),
    days,
    session_count: sessions.length,
    planned_duration_s: sessions.reduce(
      (total, session) => total + (session.planned_duration_s ?? 0),
      0,
    ),
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
          exercise_id: "barbell-back-squat",
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
          exercise_id: "romanian-deadlift",
          sets: 3,
          reps: 8,
          load: { kind: "kg", value: 80 },
          rir: 2,
          rest_s: 90,
          tempo: null,
          notes: null,
        },
        {
          exercise_id: "hanging-leg-raise",
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
    band: { channel: "power", low: 0.95, high: 1.05 },
    min_fraction: 0.75,
    selector: { kind: "role", role: "work", index: null },
  },
  {
    kind: "ceiling",
    channel: "hr",
    limit: { kind: "absolute", unit: "bpm", value: 178 },
    max_seconds_above: 360,
  },
  { kind: "duration_floor", min_seconds: 3600 },
];

const STRENGTH_CRITERIA: Schemas["SessionIntentRead"]["success_criteria"] = [
  { kind: "sets_completed", min_fraction: 0.9 },
  { kind: "load_within", pct_tolerance: 0.05 },
];

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
      pinned_anchor_versions: { ftp: "0199a000-0000-7000-8000-0000000000f1" },
      structure: cycling ? VO2_STRUCTURE : STRENGTH_STRUCTURE,
      success_criteria: cycling ? CYCLING_CRITERIA : STRENGTH_CRITERIA,
      summary: {
        step_count: seed.session.step_count,
        total_duration_s: seed.session.planned_duration_s,
        total_sets: seed.session.total_sets,
      },
    },
  };
}
