/**
 * The workout builder's client-side draft, and its translation to the wire.
 *
 * A draft is *not* a `WorkoutStructure` with holes in it. Every number is held
 * as the **string the athlete typed**, because a half-typed number is a legal
 * thing to have in a form and coercing `"1"` to `1` on the way to `"12"` makes
 * a field impossible to clear or to lead with a zero. Every node carries a
 * client-side `id` so React can key a tree whose nodes have no identity of
 * their own and so an edit can name the node it edits without an index path.
 *
 * Three functions cross the boundary, and only these three:
 *
 * * `structureFromDraft` — lenient. Anything unreadable becomes `null`, so the
 *   live profile preview can render a tree that is still being typed.
 * * `validateDraft` — strict. The sentences it returns are what stops a save;
 *   they mirror the domain's own bounds (`app/domain/workout.py`,
 *   `app/domain/strength.py`) so an obvious mistake is caught without a round
 *   trip. The backend's 422 remains the authority — this is a courtesy, not a
 *   second rule engine.
 * * `draftFromStructure` — the inverse, for editing something already saved.
 */

import type { components } from "@/generated/api/schema";

import { parseDurationInput, parseNumberInput } from "@/lib/format";

type Schemas = components["schemas"];

export type Channel = Schemas["Channel"];
export type ChannelUnit = Schemas["ChannelUnit"];
export type AnchorType = Schemas["AnchorType"];
export type StepRole = Schemas["StepRole"];
export type LoadKind = Schemas["LoadKind"];
export type Discipline = Schemas["Discipline"];

export type EnduranceStructureInput = Schemas["EnduranceStructureSchema-Input"];
export type StrengthStructure = Schemas["StrengthStructureSchema"];
export type StructureInput = EnduranceStructureInput | StrengthStructure;
export type StructureOutput =
  | Schemas["EnduranceStructureSchema-Output"]
  | StrengthStructure;
type WireTarget =
  | Schemas["PercentOfAnchorSchema"]
  | Schemas["AbsoluteRangeSchema"];
type WireStep =
  | Schemas["SteadyStepSchema"]
  | Schemas["RampStepSchema"]
  | Schemas["RepeatBlockSchema-Input"]
  | Schemas["RepeatBlockSchema-Output"];

// --- the domain's bounds, mirrored ------------------------------------------

/** Prescribable channels, in the order the editor lists them. */
export const CHANNELS: readonly Channel[] = ["power", "hr", "cadence"];

/** The unit each channel is measured in. One per channel, as in the domain. */
export const CHANNEL_UNITS: Readonly<Record<Channel, ChannelUnit>> = {
  power: "W",
  hr: "bpm",
  cadence: "rpm",
};

/**
 * Which anchors a channel may be prescribed as a percentage of.
 *
 * Cadence has none — "80% of FTP rpm" is not a quantity — which is why the
 * editor offers cadence an absolute range only.
 */
export const CHANNEL_ANCHORS: Readonly<Record<Channel, readonly AnchorType[]>> =
  {
    power: ["ftp"],
    hr: ["lthr", "max_hr"],
    cadence: [],
  };

/** Plausibility bounds for an absolute target, per channel. */
export const CHANNEL_BOUNDS: Readonly<
  Record<Channel, readonly [number, number]>
> = {
  power: [0, 2500],
  hr: [25, 230],
  cadence: [0, 250],
};

/** Step roles, in the order a session is normally written. */
export const STEP_ROLES: readonly StepRole[] = [
  "warmup",
  "work",
  "recovery",
  "rest",
  "cooldown",
];

/** How a load may be expressed, and the range each kind admits. */
export const LOAD_BOUNDS: Readonly<
  Record<Exclude<LoadKind, "bodyweight">, readonly [number, number]>
> = {
  kg: [0, 500],
  percent_e1rm: [5, 150], // whole percents, as typed
  rpe: [1, 10],
};

export const LOAD_KINDS: readonly LoadKind[] = [
  "kg",
  "percent_e1rm",
  "rpe",
  "bodyweight",
];

/** How the editor labels each load kind. */
export const LOAD_KIND_LABELS: Readonly<Record<LoadKind, string>> = {
  kg: "kg",
  percent_e1rm: "% e1RM",
  rpe: "RPE",
  bodyweight: "bodyweight",
};

/** How deeply repeat blocks may nest (`MAX_NESTING_DEPTH`). */
export const MAX_NESTING_DEPTH = 4;
/** Most iterations one repeat block may ask for. */
export const MAX_REPEAT_TIMES = 100;
/** Widest percentage a target may prescribe, as a whole percent. */
export const MAX_TARGET_PERCENT = 300;
/** Longest a single step may last, in seconds. */
export const MAX_STEP_SECONDS = 43_200;
/** Longest a single step may cover, in kilometres. */
export const MAX_STEP_KM = 1000;
export const MAX_SETS = 50;
export const MAX_REPS = 500;
export const MAX_RIR = 10;
export const MAX_REST_SECONDS = 3600;
/** Longest a prescribed hold may be, in seconds. */
export const MAX_HOLD_SECONDS = 3600;
export const MAX_STRENGTH_GROUPS = 40;

// --- the draft ---------------------------------------------------------------

/** One channel's target, mid-edit. */
export interface DraftTarget {
  readonly id: string;
  readonly channel: Channel;
  /** `percent` carries an anchor and two percentages; `absolute` two numbers. */
  readonly mode: "percent" | "absolute";
  readonly anchorType: AnchorType;
  /** Whole percents in `percent` mode, the channel's own unit in `absolute`. */
  readonly low: string;
  readonly high: string;
}

interface DraftStepBase {
  readonly id: string;
  readonly role: StepRole;
  readonly name: string;
  /** A step states exactly one of the two — the domain refuses both or neither. */
  readonly extent: "duration" | "distance";
  /** `mm:ss`, `h:mm:ss`, or a bare number of minutes. */
  readonly duration: string;
  readonly distanceKm: string;
}

export interface DraftSteadyStep extends DraftStepBase {
  readonly kind: "steady";
  readonly targets: readonly DraftTarget[];
}

export interface DraftRampStep extends DraftStepBase {
  readonly kind: "ramp";
  readonly startTargets: readonly DraftTarget[];
  readonly endTargets: readonly DraftTarget[];
}

export interface DraftRepeatBlock {
  readonly id: string;
  readonly kind: "repeat";
  readonly times: string;
  readonly children: readonly DraftStep[];
}

export type DraftStep = DraftSteadyStep | DraftRampStep | DraftRepeatBlock;

/**
 * Whether a line prescribes repetitions or a hold. Exactly one, which is why
 * this is a mode rather than two fields either of which may be blank: a form
 * that lets both be filled in has to decide which one it meant, and the domain
 * refuses the answer either way.
 */
export type StrengthItemMode = "reps" | "hold";

export interface DraftStrengthItem {
  readonly id: string;
  readonly exerciseId: string;
  /** Rounds, as written on the card. A per-side round is two working sets. */
  readonly sets: string;
  readonly mode: StrengthItemMode;
  readonly reps: string;
  /** Seconds held per working set, when the mode is `hold`. */
  readonly durationS: string;
  /** Each round performed one side at a time — two working sets, not one. */
  readonly perSide: boolean;
  readonly loadKind: LoadKind;
  readonly loadValue: string;
  readonly rir: string;
  readonly restS: string;
  readonly tempo: string;
  readonly notes: string;
}

/** One or more lines performed together; more than one *is* a superset. */
export interface DraftStrengthGroup {
  readonly id: string;
  readonly label: string;
  readonly items: readonly DraftStrengthItem[];
}

export interface EnduranceDraft {
  readonly discipline: "cycling";
  readonly steps: readonly DraftStep[];
}

export interface StrengthDraft {
  readonly discipline: "strength";
  readonly groups: readonly DraftStrengthGroup[];
}

export type WorkoutDraft = EnduranceDraft | StrengthDraft;

// --- ids ---------------------------------------------------------------------

let sequence = 0;

/**
 * A key for a node that has no identity of its own.
 *
 * A counter rather than `crypto.randomUUID()`: these ids never leave the
 * browser (they are stripped by `structureFromDraft`), and a deterministic
 * sequence makes a failing builder test readable.
 */
export function draftId(): string {
  sequence += 1;
  return `draft-${sequence}`;
}

// --- blanks ------------------------------------------------------------------

export function blankTarget(channel: Channel): DraftTarget {
  const anchors = CHANNEL_ANCHORS[channel];
  const anchorType = anchors[0];
  return {
    id: draftId(),
    channel,
    mode: anchorType ? "percent" : "absolute",
    anchorType: anchorType ?? "ftp",
    low: "",
    high: "",
  };
}

export function blankSteadyStep(role: StepRole = "work"): DraftSteadyStep {
  return {
    id: draftId(),
    kind: "steady",
    role,
    name: "",
    extent: "duration",
    duration: "",
    distanceKm: "",
    targets: [],
  };
}

export function blankRampStep(role: StepRole = "warmup"): DraftRampStep {
  return {
    id: draftId(),
    kind: "ramp",
    role,
    name: "",
    extent: "duration",
    duration: "",
    distanceKm: "",
    startTargets: [],
    endTargets: [],
  };
}

/** A repeat starts with one work step inside it: an empty block is illegal. */
export function blankRepeatBlock(): DraftRepeatBlock {
  return {
    id: draftId(),
    kind: "repeat",
    times: "4",
    children: [blankSteadyStep("work"), blankSteadyStep("rest")],
  };
}

export function blankStrengthItem(exerciseId = ""): DraftStrengthItem {
  return {
    id: draftId(),
    exerciseId,
    sets: "3",
    mode: "reps",
    reps: "8",
    durationS: "",
    perSide: false,
    loadKind: "kg",
    loadValue: "",
    rir: "",
    restS: "",
    tempo: "",
    notes: "",
  };
}

export function blankStrengthGroup(): DraftStrengthGroup {
  return { id: draftId(), label: "", items: [blankStrengthItem()] };
}

/** A draft with nothing in it yet, for the discipline the athlete picked. */
export function emptyDraft(discipline: Discipline): WorkoutDraft {
  return discipline === "cycling"
    ? { discipline, steps: [] }
    : { discipline, groups: [blankStrengthGroup()] };
}

// --- tree edits (pure, by id) ------------------------------------------------

/** The step with this id, anywhere in the tree. */
export function findStep(
  steps: readonly DraftStep[],
  id: string,
): DraftStep | null {
  for (const step of steps) {
    if (step.id === id) {
      return step;
    }
    if (step.kind === "repeat") {
      const found = findStep(step.children, id);
      if (found) {
        return found;
      }
    }
  }
  return null;
}

/** Replace one step in place, wherever it sits. */
export function replaceStep(
  steps: readonly DraftStep[],
  id: string,
  next: DraftStep,
): DraftStep[] {
  return steps.map((step) => {
    if (step.id === id) {
      return next;
    }
    return step.kind === "repeat"
      ? { ...step, children: replaceStep(step.children, id, next) }
      : step;
  });
}

/** Drop one step, wherever it sits. Repeats take their children with them. */
export function removeStep(
  steps: readonly DraftStep[],
  id: string,
): DraftStep[] {
  return steps
    .filter((step) => step.id !== id)
    .map((step) =>
      step.kind === "repeat"
        ? { ...step, children: removeStep(step.children, id) }
        : step,
    );
}

/**
 * Append a step — at the top level when `parentId` is `null`, otherwise inside
 * that repeat block. A `parentId` naming no repeat leaves the tree alone.
 */
export function insertStep(
  steps: readonly DraftStep[],
  parentId: string | null,
  step: DraftStep,
): DraftStep[] {
  if (parentId === null) {
    return [...steps, step];
  }
  return steps.map((candidate) => {
    if (candidate.kind !== "repeat") {
      return candidate;
    }
    if (candidate.id === parentId) {
      return { ...candidate, children: [...candidate.children, step] };
    }
    return {
      ...candidate,
      children: insertStep(candidate.children, parentId, step),
    };
  });
}

/**
 * Move a step one place earlier (`-1`) or later (`+1`) among its siblings.
 *
 * Reordering never changes a step's parent: dragging a step out of a repeat is
 * a different edit from moving it within one, and conflating them would make
 * the arrow buttons occasionally restructure the workout.
 */
export function moveStep(
  steps: readonly DraftStep[],
  id: string,
  delta: number,
): DraftStep[] {
  const index = steps.findIndex((step) => step.id === id);
  if (index !== -1) {
    const target = index + delta;
    if (target < 0 || target >= steps.length) {
      return [...steps];
    }
    const reordered = [...steps];
    const [moved] = reordered.splice(index, 1);
    if (moved) {
      reordered.splice(target, 0, moved);
    }
    return reordered;
  }
  return steps.map((step) =>
    step.kind === "repeat"
      ? { ...step, children: moveStep(step.children, id, delta) }
      : step,
  );
}

/**
 * How many repeat blocks enclose this one, itself included.
 *
 * The editor uses it to stop offering "repeat inside this" once a fifth level
 * would be illegal — the domain caps nesting at `MAX_NESTING_DEPTH` because
 * flattening is exponential in depth.
 */
export function repeatDepth(
  steps: readonly DraftStep[],
  id: string,
  depth = 0,
): number {
  for (const step of steps) {
    if (step.kind !== "repeat") {
      continue;
    }
    const here = depth + 1;
    if (step.id === id) {
      return here;
    }
    const found = repeatDepth(step.children, id, here);
    if (found > 0) {
      return found;
    }
  }
  return 0;
}

// --- draft → wire ------------------------------------------------------------

/**
 * The draft as the API's structure document. Lenient by design: what cannot be
 * read becomes `null` or is dropped, so the preview keeps drawing while a
 * number is being typed. `validateDraft` is what refuses a save.
 */
export function structureFromDraft(draft: WorkoutDraft): StructureInput {
  if (draft.discipline === "strength") {
    return {
      discipline: "strength",
      groups: draft.groups.map((group) => ({
        label: group.label.trim() === "" ? null : group.label.trim(),
        items: group.items.map(strengthItemToWire),
      })),
    };
  }
  return { discipline: "cycling", steps: draft.steps.map(stepToWire) };
}

function stepToWire(
  step: DraftStep,
):
  | Schemas["SteadyStepSchema"]
  | Schemas["RampStepSchema"]
  | Schemas["RepeatBlockSchema-Input"] {
  if (step.kind === "repeat") {
    return {
      kind: "repeat",
      times: Math.max(1, Math.round(parseNumberInput(step.times) ?? 1)),
      children: step.children.map(stepToWire),
    };
  }
  const durationS =
    step.extent === "duration" ? parseDurationInput(step.duration) : null;
  const km =
    step.extent === "distance" ? parseNumberInput(step.distanceKm) : null;
  const base = {
    role: step.role,
    name: step.name.trim() === "" ? null : step.name.trim(),
    duration_s: durationS,
    distance_m: km === null ? null : Math.round(km * 1000),
  };
  return step.kind === "ramp"
    ? {
        ...base,
        kind: "ramp",
        start_targets: targetsToWire(step.startTargets),
        end_targets: targetsToWire(step.endTargets),
      }
    : { ...base, kind: "steady", targets: targetsToWire(step.targets) };
}

function targetsToWire(
  targets: readonly DraftTarget[],
): Record<string, WireTarget> {
  const out: Record<string, WireTarget> = {};
  for (const target of targets) {
    const low = parseNumberInput(target.low);
    const high = parseNumberInput(target.high);
    if (low === null || high === null) {
      continue;
    }
    out[target.channel] =
      target.mode === "percent"
        ? {
            kind: "percent_of_anchor",
            anchor_type: target.anchorType,
            pct_low: low / 100,
            pct_high: high / 100,
          }
        : {
            kind: "absolute",
            unit: CHANNEL_UNITS[target.channel],
            low,
            high,
          };
  }
  return out;
}

function strengthItemToWire(
  item: DraftStrengthItem,
): Schemas["StrengthSetSchema"] {
  const value = parseNumberInput(item.loadValue);
  const hold = item.mode === "hold";
  return {
    exercise_id: item.exerciseId,
    sets: Math.round(parseNumberInput(item.sets) ?? 0),
    // Exactly one of the two, and the *other* one is null rather than absent
    // so a re-edit cannot carry a stale value across a mode switch.
    reps: hold ? null : Math.round(parseNumberInput(item.reps) ?? 0),
    duration_s: hold ? Math.round(parseNumberInput(item.durationS) ?? 0) : null,
    per_side: item.perSide ? true : null,
    load: {
      kind: item.loadKind,
      value:
        item.loadKind === "bodyweight"
          ? null
          : item.loadKind === "percent_e1rm" && value !== null
            ? value / 100
            : value,
    },
    rir: parseNumberInput(item.rir),
    rest_s: parseNumberInput(item.restS),
    tempo: item.tempo.trim() === "" ? null : item.tempo.trim(),
    notes: item.notes.trim() === "" ? null : item.notes.trim(),
  };
}

// --- wire → draft ------------------------------------------------------------

/** Rebuild a draft from something already saved, for the edit form. */
export function draftFromStructure(
  structure: StructureInput | StructureOutput | null | undefined,
): WorkoutDraft | null {
  if (!structure) {
    return null;
  }
  if (structure.discipline === "strength") {
    return {
      discipline: "strength",
      groups: structure.groups.map((group) => ({
        id: draftId(),
        label: group.label ?? "",
        items: group.items.map((item) => ({
          id: draftId(),
          exerciseId: item.exercise_id,
          sets: String(item.sets),
          mode:
            item.duration_s === null || item.duration_s === undefined
              ? ("reps" as const)
              : ("hold" as const),
          reps:
            item.reps === null || item.reps === undefined
              ? ""
              : String(item.reps),
          durationS:
            item.duration_s === null || item.duration_s === undefined
              ? ""
              : String(item.duration_s),
          perSide: item.per_side === true,
          loadKind: item.load.kind,
          loadValue:
            item.load.value === null || item.load.value === undefined
              ? ""
              : String(
                  item.load.kind === "percent_e1rm"
                    ? round(item.load.value * 100)
                    : item.load.value,
                ),
          rir:
            item.rir === null || item.rir === undefined ? "" : String(item.rir),
          restS:
            item.rest_s === null || item.rest_s === undefined
              ? ""
              : String(item.rest_s),
          tempo: item.tempo ?? "",
          notes: item.notes ?? "",
        })),
      })),
    };
  }
  return { discipline: "cycling", steps: structure.steps.map(stepFromWire) };
}

function stepFromWire(step: WireStep): DraftStep {
  if (step.kind === "repeat") {
    return {
      id: draftId(),
      kind: "repeat",
      times: String(step.times),
      children: step.children.map(stepFromWire),
    };
  }
  const base = {
    id: draftId(),
    role: step.role,
    name: step.name ?? "",
    extent: (step.distance_m ? "distance" : "duration") as
      | "duration"
      | "distance",
    duration: step.duration_s ? secondsToInput(step.duration_s) : "",
    distanceKm: step.distance_m ? String(round(step.distance_m / 1000, 2)) : "",
  };
  return step.kind === "ramp"
    ? {
        ...base,
        kind: "ramp",
        startTargets: targetsFromWire(step.start_targets),
        endTargets: targetsFromWire(step.end_targets),
      }
    : { ...base, kind: "steady", targets: targetsFromWire(step.targets ?? {}) };
}

function targetsFromWire(
  targets: Record<string, WireTarget>,
): readonly DraftTarget[] {
  return CHANNELS.filter((channel) => targets[channel]).map((channel) => {
    // Non-null by the filter above; narrowed here so the map stays total.
    const target = targets[channel] as WireTarget;
    return target.kind === "percent_of_anchor"
      ? {
          id: draftId(),
          channel,
          mode: "percent" as const,
          anchorType: target.anchor_type,
          low: String(round(target.pct_low * 100)),
          high: String(round(target.pct_high * 100)),
        }
      : {
          id: draftId(),
          channel,
          mode: "absolute" as const,
          anchorType: CHANNEL_ANCHORS[channel][0] ?? "ftp",
          low: String(target.low),
          high: String(target.high),
        };
  });
}

/** Seconds back into the `mm:ss` the duration field reads and writes. */
function secondsToInput(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const mm = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
  return hours > 0
    ? `${hours}:${mm}:${String(rest).padStart(2, "0")}`
    : `${mm}:${String(rest).padStart(2, "0")}`;
}

function round(value: number, places = 0): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

// --- validation --------------------------------------------------------------

/**
 * Everything wrong with the draft, as sentences to show above the form.
 *
 * Empty means "worth sending". It does not mean "the server will accept it":
 * anchors that are not in force, purposes that disagree with the discipline
 * and every other rule the client cannot see still come back as a 422, which
 * the form renders as-is.
 */
export function validateDraft(draft: WorkoutDraft): string[] {
  return draft.discipline === "cycling"
    ? validateEndurance(draft)
    : validateStrength(draft);
}

function validateEndurance(draft: EnduranceDraft): string[] {
  const problems: string[] = [];
  if (draft.steps.length === 0) {
    problems.push("A workout needs at least one step.");
  }
  walkSteps(draft.steps, 0, (step, depth, label) => {
    if (step.kind === "repeat") {
      const times = parseNumberInput(step.times);
      if (times === null || times < 1 || times > MAX_REPEAT_TIMES) {
        problems.push(
          `${label}: repeat between 1 and ${MAX_REPEAT_TIMES} times.`,
        );
      }
      if (step.children.length === 0) {
        problems.push(`${label}: a repeat needs at least one step inside it.`);
      }
      if (depth > MAX_NESTING_DEPTH) {
        problems.push(
          `${label}: repeats may nest at most ${MAX_NESTING_DEPTH} deep.`,
        );
      }
      return;
    }
    if (step.extent === "duration") {
      const seconds = parseDurationInput(step.duration);
      if (seconds === null || seconds < 1 || seconds > MAX_STEP_SECONDS) {
        problems.push(`${label}: give a duration, as mm:ss or minutes.`);
      }
    } else {
      const km = parseNumberInput(step.distanceKm);
      if (km === null || km <= 0 || km > MAX_STEP_KM) {
        problems.push(`${label}: give a distance in km.`);
      }
    }
    const targets =
      step.kind === "ramp"
        ? [...step.startTargets, ...step.endTargets]
        : step.targets;
    for (const target of targets) {
      problems.push(...validateTarget(target, label));
    }
    if (step.kind === "ramp") {
      for (const start of step.startTargets) {
        const end = step.endTargets.find((t) => t.channel === start.channel);
        if (!end) {
          problems.push(
            `${label}: the ramp's ${start.channel} target needs an end value too.`,
          );
        } else if (end.mode !== start.mode) {
          problems.push(
            `${label}: a ramp's two ends must be the same kind of target.`,
          );
        }
      }
    }
  });
  return problems;
}

function validateTarget(target: DraftTarget, label: string): string[] {
  const problems: string[] = [];
  const low = parseNumberInput(target.low);
  const high = parseNumberInput(target.high);
  const channel = target.channel;
  if (low === null || high === null) {
    return [`${label}: give both ends of the ${channel} target.`];
  }
  if (high < low) {
    problems.push(`${label}: the ${channel} target's high is below its low.`);
  }
  if (target.mode === "percent") {
    if (!CHANNEL_ANCHORS[channel].includes(target.anchorType)) {
      problems.push(
        `${label}: ${channel} cannot be prescribed as a percentage of ${target.anchorType}.`,
      );
    }
    if (low <= 0 || high > MAX_TARGET_PERCENT) {
      problems.push(
        `${label}: the ${channel} target must be between 1% and ${MAX_TARGET_PERCENT}%.`,
      );
    }
  } else {
    const [min, max] = CHANNEL_BOUNDS[channel];
    if (low < min || high > max) {
      problems.push(
        `${label}: ${channel} must be between ${min} and ${max} ${CHANNEL_UNITS[channel]}.`,
      );
    }
  }
  return problems;
}

function validateStrength(draft: StrengthDraft): string[] {
  const problems: string[] = [];
  if (draft.groups.length === 0) {
    problems.push("A strength workout needs at least one exercise.");
  }
  if (draft.groups.length > MAX_STRENGTH_GROUPS) {
    problems.push(
      `A strength workout may hold at most ${MAX_STRENGTH_GROUPS} groups.`,
    );
  }
  draft.groups.forEach((group, index) => {
    const groupLabel = group.label.trim() || `Group ${index + 1}`;
    if (group.items.length === 0) {
      problems.push(`${groupLabel}: add an exercise or remove the group.`);
    }
    for (const item of group.items) {
      const label = `${groupLabel} · ${item.exerciseId || "exercise"}`;
      if (item.exerciseId.trim() === "") {
        problems.push(`${groupLabel}: choose an exercise.`);
      }
      problems.push(...inRange(item.sets, 1, MAX_SETS, `${label}: sets`));
      if (item.mode === "hold") {
        problems.push(
          ...inRange(item.durationS, 1, MAX_HOLD_SECONDS, `${label}: hold`),
        );
      } else {
        problems.push(...inRange(item.reps, 1, MAX_REPS, `${label}: reps`));
      }
      if (item.rir.trim() !== "") {
        problems.push(...inRange(item.rir, 0, MAX_RIR, `${label}: RIR`));
      }
      if (item.restS.trim() !== "") {
        problems.push(
          ...inRange(item.restS, 0, MAX_REST_SECONDS, `${label}: rest`),
        );
      }
      if (item.loadKind === "bodyweight") {
        continue;
      }
      const [min, max] = LOAD_BOUNDS[item.loadKind];
      if (item.loadValue.trim() === "") {
        problems.push(
          `${label}: a ${LOAD_KIND_LABELS[item.loadKind]} load needs a value.`,
        );
      } else {
        problems.push(...inRange(item.loadValue, min, max, `${label}: load`));
      }
    }
  });
  return problems;
}

function inRange(
  text: string,
  min: number,
  max: number,
  label: string,
): string[] {
  const value = parseNumberInput(text);
  return value === null || value < min || value > max
    ? [`${label} must be between ${min} and ${max}.`]
    : [];
}

/** Walk the tree, handing each step a human path like `Step 2 · 3×`. */
function walkSteps(
  steps: readonly DraftStep[],
  depth: number,
  visit: (step: DraftStep, depth: number, label: string) => void,
  prefix = "Step",
): void {
  steps.forEach((step, index) => {
    const label = `${prefix} ${index + 1}`;
    visit(step, depth + (step.kind === "repeat" ? 1 : 0), label);
    if (step.kind === "repeat") {
      walkSteps(step.children, depth + 1, visit, `${label}.`);
    }
  });
}
