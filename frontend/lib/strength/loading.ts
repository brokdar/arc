// The TypeScript twin of `backend/app/domain/loading.py`.
//
// Same steps in the same order, so the same doubles come out: loadable
// weights are built in integer grams and divided by 1000 last, targets are
// compared (never summed), a tie between two loadable weights rounds down,
// and the plate list is the fewest plates with heavier-first breaking ties.
// The reasoning behind each rule lives in the Python docstrings; this file
// does not restate it, because the Python is the source of truth and
// `loading.vectors.test.ts` fails the moment the two disagree.

/** Kilograms in one international pound — exact by definition. */
export const KG_PER_LB = 0.45359237;

/** Largest fraction `percentOfSet` accepts (`MAX_PERCENT_OF_SET`). */
export const MAX_PERCENT_OF_SET = 1.5;

const STARTING_STEP_INCREASE: Readonly<Record<string, number>> = {
  easy: 0.2,
  good: 0.1,
};

/** A loading question with no meaningful answer (`LoadingError`). */
export class LoadingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LoadingError";
  }
}

export type Equipment = "barbell" | "dumbbell" | "stack";

/** What one gym can load (`Gym`). `platePairs` is `[plateKg, pairs]`. */
export interface Gym {
  readonly barKg: number;
  readonly platePairs: ReadonlyArray<readonly [number, number]>;
  readonly dumbbellStepKg: number;
  readonly dumbbellMaxKg: number;
  readonly stackStepKg: number;
  readonly stackMaxKg: number;
}

/** `DEFAULT_GYM` — the one gym until places exist. */
export const DEFAULT_GYM: Gym = {
  barKg: 20.0,
  platePairs: [
    [25.0, 4],
    [20.0, 2],
    [10.0, 2],
    [5.0, 2],
    [2.5, 2],
    [1.25, 2],
  ],
  dumbbellStepKg: 2.0,
  dumbbellMaxKg: 50.0,
  stackStepKg: 5.0,
  stackMaxKg: 150.0,
};

export interface Loading {
  readonly kg: number;
  readonly capped: boolean;
  /** Heaviest first; `null` for dumbbells and stacks. */
  readonly platesPerSide: number[] | null;
}

// Gym values are whole grams, so neither rounding mode ever sees a half and
// this agrees with Python's banker's `round` on every input it is given.
const grams = (kg: number): number => Math.round(kg * 1000);

function better(one: readonly number[], other: readonly number[]): boolean {
  if (one.length !== other.length) return one.length < other.length;
  for (let i = 0; i < one.length; i++) {
    if (one[i] !== other[i]) return one[i] > other[i];
  }
  return false;
}

const sidesCache = new WeakMap<Gym, Map<number, number[]>>();

function barbellSides(gym: Gym): Map<number, number[]> {
  const cached = sidesCache.get(gym);
  if (cached) return cached;
  let best = new Map<number, number[]>([[0, []]]);
  const kinds = gym.platePairs
    .map(([kg, pairs]) => [grams(kg), pairs] as const)
    .sort((x, y) => y[0] - x[0] || y[1] - x[1]);
  for (const [plate, pairs] of kinds) {
    const grown = new Map<number, number[]>();
    for (const [side, plates] of best) {
      for (let count = 0; count <= pairs; count++) {
        const total = side + plate * count;
        const candidate = [...plates, ...Array<number>(count).fill(plate)];
        const current = grown.get(total);
        if (current === undefined || better(candidate, current)) {
          grown.set(total, candidate);
        }
      }
    }
    best = grown;
  }
  sidesCache.set(gym, best);
  return best;
}

function loadableGrams(equipment: Equipment, gym: Gym): number[] {
  if (equipment === "barbell") {
    const bar = grams(gym.barKg);
    return [...barbellSides(gym).keys()]
      .map((side) => bar + 2 * side)
      .sort((x, y) => x - y);
  }
  const [step, top] =
    equipment === "dumbbell"
      ? [grams(gym.dumbbellStepKg), grams(gym.dumbbellMaxKg)]
      : [grams(gym.stackStepKg), grams(gym.stackMaxKg)];
  const out: number[] = [];
  for (let k = 1; k <= Math.floor(top / step); k++) out.push(step * k);
  return out;
}

/** Every weight `gym` can load on `equipment`, lightest first, in kg. */
export function loadableWeights(
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number[] {
  return loadableGrams(equipment, gym).map((g) => g / 1000);
}

function checkTarget(targetKg: number): void {
  if (!(Number.isFinite(targetKg) && targetKg >= 0)) {
    throw new LoadingError(
      `a target must be a weight of 0 kg or more, got ${targetKg}`,
    );
  }
}

function nearest(
  targetKg: number,
  weights: readonly number[],
): [number, boolean] {
  const top = weights[weights.length - 1];
  if (targetKg >= top) return [top, targetKg > top];
  // bisect_right: the first weight strictly above the target.
  let above = 0;
  while (above < weights.length && weights[above] <= targetKg) above++;
  if (above === 0) return [weights[0], false];
  const low = weights[above - 1];
  const high = weights[above];
  if (low === targetKg) return [low, false];
  return [high - targetKg < targetKg - low ? high : low, false];
}

/** `loading_for`: the nearest loadable weight, whether capped, and its plates. */
export function loadingFor(
  targetKg: number,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): Loading {
  checkTarget(targetKg);
  const [kg, capped] = nearest(targetKg, loadableWeights(equipment, gym));
  let platesPerSide: number[] | null = null;
  if (equipment === "barbell") {
    const side = Math.floor((grams(kg) - grams(gym.barKg)) / 2);
    platesPerSide = (barbellSides(gym).get(side) ?? []).map((g) => g / 1000);
  }
  return { kg, capped, platesPerSide };
}

/** `nearest_loadable`: closest loadable weight; a tie rounds down. */
export function nearestLoadable(
  targetKg: number,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number {
  return loadingFor(targetKg, equipment, gym).kg;
}

/** `plates_per_side`: plates on each side for `kg` (rounded), heaviest first. */
export function platesPerSide(
  kg: number,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number[] {
  if (equipment !== "barbell")
    throw new LoadingError(`a ${equipment} has no plates`);
  return loadingFor(kg, equipment, gym).platesPerSide ?? [];
}

/** `step_down`: one loadable weight lighter, floored at the lightest. */
export function stepDown(
  kg: number,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number {
  const weights = loadableWeights(equipment, gym);
  const index = weights.indexOf(nearestLoadable(kg, equipment, gym));
  return weights[Math.max(index - 1, 0)];
}

/** `starting_step`: easy +20 %, good +10 % (rounded), hard one step down. */
export function startingStep(
  kg: number,
  answer: string,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number {
  if (answer === "hard") return stepDown(kg, equipment, gym);
  const increase = Object.hasOwn(STARTING_STEP_INCREASE, answer)
    ? STARTING_STEP_INCREASE[answer]
    : undefined;
  if (increase === undefined) {
    throw new LoadingError(
      `unknown starting-step answer '${answer}'; expected one of easy, good, hard`,
    );
  }
  checkTarget(kg);
  return nearestLoadable(kg * (1 + increase), equipment, gym);
}

/** `percent_of_set`: a fraction in (0, 1.5] of a set's weight, rounded. */
export function percentOfSet(
  kg: number,
  fraction: number,
  equipment: Equipment = "barbell",
  gym: Gym = DEFAULT_GYM,
): number {
  if (
    !(
      Number.isFinite(fraction) &&
      fraction > 0 &&
      fraction <= MAX_PERCENT_OF_SET
    )
  ) {
    throw new LoadingError(
      `a percent of a set is a fraction above 0 and at most ${MAX_PERCENT_OF_SET}, got ${fraction}`,
    );
  }
  checkTarget(kg);
  return nearestLoadable(kg * fraction, equipment, gym);
}

export const kgToLb = (kg: number): number => kg / KG_PER_LB;
export const lbToKg = (lb: number): number => lb * KG_PER_LB;
