/**
 * The one sentence the Today view leads with.
 *
 * Build plan WP-3.4 asks for "what / how long / how hard in one sentence,
 * composed from purpose + duration + dominant target". That composition is
 * here, pure and total, rather than in the view: it is the only piece of
 * Today with a right and a wrong answer, and it is far cheaper to pin down in
 * a unit test than to read off a rendered page.
 *
 * Nothing here invents a number. Every clause comes from the frozen
 * prescription — no TSS, no intensity factor, no readiness (those belong to
 * work packages that do not exist yet).
 */

import { formatDurationWords, formatMinutesPrime } from "@/lib/format";
import { type Translate, translatorFor } from "@/lib/i18n/provider";
import {
  disciplineOfPurpose,
  type Purpose,
  purposeSentence,
} from "@/lib/purpose";
import {
  type ProfileBar,
  profileBars,
  totalDurationS,
  totalSets,
  type WorkoutStructure,
  ZONE_LABELS,
  type ZoneTone,
} from "@/lib/workout-profile";

export interface HeadlineInput {
  readonly purpose: Purpose;
  readonly structure: WorkoutStructure | null | undefined;
  /** The week card's figure, when the caller already has it. */
  readonly plannedDurationS?: number | null;
  readonly totalSets?: number | null;
}

/**
 * The share of prescribed time one band must hold before the session is
 * described as *steady* rather than mixed. Three fifths: much below that,
 * calling a ride "steady Z2" would be describing a minority of it.
 */
const STEADY_SHARE = 0.6;

/**
 * `3h10 endurance ride — steady Z2`, `16 sets of max strength — 3 movements`.
 *
 * Degrades a clause at a time: a session with no structure yet still gets its
 * purpose and its duration, and one with neither gets its purpose alone. It
 * never returns an empty string, because it is rendered as an `<h1>`.
 *
 * Phrased through the catalogue (`headline.*`, `purposeSentence.*`) so Today's
 * headline is in the athlete's language; with no translator it is English.
 * German word order differs ("16 Sätze Maximalkraft"), which is why each
 * clause is a whole message rather than pieces joined here.
 */
export function sessionHeadline(
  input: HeadlineInput,
  t: Translate = translatorFor("en"),
): string {
  const discipline = disciplineOfPurpose(input.purpose);
  const head =
    discipline === "cycling" ? cyclingHead(input, t) : strengthHead(input, t);
  const effort =
    discipline === "cycling"
      ? cyclingEffort(input.structure, t)
      : strengthEffort(input.structure, t);
  return effort ? `${head} — ${effort}` : head;
}

function cyclingHead(input: HeadlineInput, t: Translate): string {
  const seconds = input.plannedDurationS ?? totalDurationS(input.structure);
  const purpose = purposeSentence(input.purpose, t);
  return seconds
    ? t("headline.ride", { duration: formatDurationWords(seconds), purpose })
    : t("headline.rideNoDuration", { purpose });
}

function strengthHead(input: HeadlineInput, t: Translate): string {
  const sets = input.totalSets ?? totalSets(input.structure);
  const purpose = purposeSentence(input.purpose, t);
  if (!sets) return t("headline.session", { purpose });
  return t(sets === 1 ? "headline.setsOne" : "headline.setsOther", {
    sets,
    purpose,
  });
}

/**
 * How hard the ride is, in the terms a plan is written in.
 *
 * Three shapes, in the order they are worth saying:
 *
 * 1. The workout repeats something — that *is* the session. `5×4′ at Z5`.
 * 2. One band holds most of the time. `steady Z2`.
 * 3. Neither. `mixed Z2–Z4`, naming the two ends actually prescribed.
 */
function cyclingEffort(
  structure: WorkoutStructure | null | undefined,
  t: Translate,
): string | null {
  if (structure?.discipline !== "cycling") {
    return null;
  }
  const bars = profileBars(structure);
  if (bars.length === 0) {
    return null;
  }

  const interval = firstWorkInterval(structure);
  if (interval) {
    const zone = zoneOfWorkBars(bars);
    const length = formatMinutesPrime(interval.durationS);
    return zone
      ? t("headline.intervalAt", { times: interval.times, length, zone })
      : t("headline.interval", { times: interval.times, length });
  }

  const shares = weightByZone(bars);
  const total = [...shares.values()].reduce((sum, weight) => sum + weight, 0);
  const top = [...shares.entries()].sort((a, b) => b[1] - a[1])[0];
  if (!top || total === 0) {
    return null;
  }
  if (top[1] / total >= STEADY_SHARE) {
    return t("headline.steady", { zone: ZONE_LABELS[top[0]] });
  }
  const order = Object.keys(ZONE_LABELS) as ZoneTone[];
  const present = [...shares.keys()].sort(
    (a, b) => order.indexOf(a) - order.indexOf(b),
  );
  const low = present[0];
  const high = present[present.length - 1];
  if (!low || !high || low === high) {
    return t("headline.steady", { zone: ZONE_LABELS[top[0]] });
  }
  return t("headline.mixed", {
    low: ZONE_LABELS[low],
    high: ZONE_LABELS[high],
  });
}

/** Total flex weight per band. The profile's histogram, one entry per zone. */
function weightByZone(bars: readonly ProfileBar[]): Map<ZoneTone, number> {
  const shares = new Map<ZoneTone, number>();
  for (const bar of bars) {
    shares.set(bar.zone, (shares.get(bar.zone) ?? 0) + bar.weight);
  }
  return shares;
}

/** The first repeat block with a timed work step in it, if the tree has one. */
function firstWorkInterval(
  structure: WorkoutStructure,
): { times: number; durationS: number } | null {
  if (structure.discipline !== "cycling") {
    return null;
  }
  type Step = (typeof structure.steps)[number];
  const search = (
    steps: readonly Step[],
  ): { times: number; durationS: number } | null => {
    for (const step of steps) {
      if (step.kind !== "repeat") {
        continue;
      }
      const work = step.children.find(
        (child) =>
          child.kind !== "repeat" &&
          child.role === "work" &&
          !!child.duration_s,
      );
      if (work && work.kind !== "repeat" && work.duration_s) {
        return { times: step.times, durationS: work.duration_s };
      }
      const nested = search(step.children);
      if (nested) {
        return nested;
      }
    }
    return null;
  };
  return search(structure.steps);
}

/** The band the work steps sit in — the intensity an interval session is about. */
function zoneOfWorkBars(bars: readonly ProfileBar[]): string | null {
  const work = bars.filter((bar) => bar.role === "work");
  const shares = weightByZone(work.length > 0 ? work : bars);
  const top = [...shares.entries()].sort((a, b) => b[1] - a[1])[0];
  return top ? ZONE_LABELS[top[0]] : null;
}

/** What a lifting session is made of: movements, and whether any are paired. */
function strengthEffort(
  structure: WorkoutStructure | null | undefined,
  t: Translate,
): string | null {
  if (structure?.discipline !== "strength") {
    return null;
  }
  const movements = structure.groups.reduce(
    (count, group) => count + group.items.length,
    0,
  );
  if (movements === 0) {
    return null;
  }
  const supersets = structure.groups.filter(
    (group) => group.items.length > 1,
  ).length;
  const movementClause =
    movements === 1
      ? t("headline.movementsOne")
      : t("headline.movementsOther", { count: movements });
  if (supersets === 0) {
    return movementClause;
  }
  const supersetClause =
    supersets === 1
      ? t("headline.supersetOne")
      : t("headline.supersetsOther", { count: supersets });
  return `${movementClause}, ${supersetClause}`;
}
