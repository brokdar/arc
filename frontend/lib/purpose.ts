import type { components } from "@/generated/api/schema";

export type Purpose = components["schemas"]["Purpose"];
export type Discipline = components["schemas"]["Discipline"];
export type SessionStatus =
  components["schemas"]["app__domain__sessions__SessionStatus"];

/**
 * How one purpose is rendered: its human label and its three colours.
 *
 * `edge` is the card's 2px left border (the saturated base), `fg` the badge's
 * text (a lightened base, so it clears contrast on a dark tint) and `tint` the
 * badge's background.
 */
export interface PurposeTone {
  readonly label: string;
  readonly edge: string;
  readonly fg: string;
  readonly tint: string;
}

/**
 * The purpose vocabulary, coloured.
 *
 * These live here rather than in `@theme` because Tailwind can only see class
 * names it can read in the source: a lookup keyed by a value that arrives at
 * runtime cannot produce `bg-purpose-vo2max`. So the palette is data and the
 * components apply it through `style`. `test_purpose_tones_cover_the_enum`
 * keeps the table complete — a purpose added to the backend enum regenerates
 * `Purpose` and fails the type-check here before it fails a test.
 *
 * The ramp follows intensity: slate → blue → green → lime → amber → orange →
 * rose → magenta for the cycling purposes, matching the mockup's Endurance /
 * Tempo / VO₂max / Recovery badges.
 *
 * The five barbell purposes are a **steel** family — cyan through azure — and
 * not the mockup's violet, because violet is `--color-coach` and
 * `--color-status-over` and those are reserved for interpretive content (build
 * plan invariant 7). D84 records the swap and its numbers; `purpose.test.ts`
 * enforces the reservation mechanically, so the next purpose cannot spend
 * purple again by accident.
 */
export const PURPOSE_TONES: Readonly<Record<Purpose, PurposeTone>> = {
  recovery: {
    label: "Recovery",
    edge: "#94A3B8",
    fg: "#94A3B8",
    tint: "rgb(148 163 184 / 0.12)",
  },
  endurance: {
    label: "Endurance",
    edge: "#4A8FC7",
    fg: "#7BAEDC",
    tint: "rgb(74 143 199 / 0.14)",
  },
  tempo: {
    label: "Tempo",
    edge: "#56A36B",
    fg: "#6FB58C",
    tint: "rgb(86 163 107 / 0.12)",
  },
  sweet_spot: {
    label: "Sweet spot",
    edge: "#8FAE4C",
    fg: "#B2C878",
    tint: "rgb(143 174 76 / 0.14)",
  },
  threshold: {
    label: "Threshold",
    edge: "#D19A3E",
    fg: "#E3B96F",
    tint: "rgb(209 154 62 / 0.13)",
  },
  vo2max: {
    label: "VO₂max",
    edge: "#E0603C",
    fg: "#EE8A6C",
    tint: "rgb(224 96 60 / 0.12)",
  },
  anaerobic: {
    label: "Anaerobic",
    edge: "#DB4A6B",
    fg: "#EE8098",
    tint: "rgb(219 74 107 / 0.13)",
  },
  neuromuscular: {
    label: "Neuromuscular",
    edge: "#D45FB0",
    fg: "#E695CE",
    tint: "rgb(212 95 176 / 0.13)",
  },
  unstructured: {
    label: "Unstructured",
    edge: "#6B7280",
    fg: "#9AA1AC",
    tint: "rgb(107 114 128 / 0.14)",
  },
  technique: {
    label: "Technique",
    edge: "#4FA8A0",
    fg: "#7FC7C0",
    tint: "rgb(79 168 160 / 0.13)",
  },
  test: {
    label: "Test",
    edge: "#4C8DFF",
    fg: "#8FB8FF",
    tint: "rgb(76 141 255 / 0.13)",
  },
  max_strength: {
    label: "Max strength",
    edge: "#0090BB",
    fg: "#1CA8CD",
    tint: "rgb(0 144 187 / 0.14)",
  },
  strength_endurance: {
    label: "Strength endurance",
    edge: "#009095",
    fg: "#55A8AC",
    tint: "rgb(0 144 149 / 0.14)",
  },
  hypertrophy: {
    label: "Hypertrophy",
    edge: "#14AABF",
    fg: "#69C3D3",
    tint: "rgb(20 170 191 / 0.14)",
  },
  power: {
    label: "Power",
    edge: "#3DAEDE",
    fg: "#7BC8EF",
    tint: "rgb(61 174 222 / 0.14)",
  },
  core: {
    label: "Core",
    edge: "#71A3B1",
    fg: "#9EBBC4",
    tint: "rgb(113 163 177 / 0.14)",
  },
  mobility: {
    label: "Mobility",
    edge: "#6FA6A0",
    fg: "#93C0BB",
    tint: "rgb(111 166 160 / 0.13)",
  },
  conditioning: {
    label: "Conditioning",
    edge: "#D9A441",
    fg: "#E7BE72",
    tint: "rgb(217 164 65 / 0.13)",
  },
};

/** The two disciplines, as a heading names them. */
const DISCIPLINE_LABELS: Readonly<Record<Discipline, string>> = {
  cycling: "Cycling",
  strength: "Strength",
};

/** `Cycling` / `Strength` — the discipline as a row or a group is labelled. */
export function disciplineLabel(discipline: Discipline): string {
  return DISCIPLINE_LABELS[discipline];
}

/** The purpose's tone. Total over the enum, so no fallback branch. */
export function purposeTone(purpose: Purpose): PurposeTone {
  return PURPOSE_TONES[purpose];
}

/** The purpose's human label — also a card's title when it has none. */
export function purposeLabel(purpose: Purpose): string {
  return PURPOSE_TONES[purpose].label;
}

/**
 * The purpose as it reads mid-sentence: "endurance ride", "VO₂max ride".
 *
 * Derived from the label rather than stored beside it, so the vocabulary
 * cannot grow a label and a sentence form that disagree. A label carrying an
 * uppercase letter *after* the first is a proper form the language owns
 * (VO₂max) and is left alone; everything else is a common noun phrase and is
 * lowercased.
 */
export function purposeInSentence(purpose: Purpose): string {
  const label = PURPOSE_TONES[purpose].label;
  const tail = label.slice(1);
  return tail === tail.toLowerCase() ? label.toLowerCase() : label;
}

/** Every purpose belonging to a discipline, in the vocabulary's own order. */
export function purposesFor(discipline: Discipline): Purpose[] {
  return (Object.keys(PURPOSE_TONES) as Purpose[]).filter(
    (purpose) => disciplineOfPurpose(purpose) === discipline,
  );
}

/**
 * The discipline a purpose belongs to.
 *
 * Mirrors `app.domain.purpose.PURPOSE_DISCIPLINE`: the split is total over the
 * enum on both sides, and the form needs it *before* it can send anything —
 * a strength purpose on a bike prescription is a 422 the athlete should never
 * have been able to compose. `purpose.test.ts` checks the table against the
 * committed schema, so a purpose the backend gains fails here first.
 */
export function disciplineOfPurpose(purpose: Purpose): Discipline {
  return STRENGTH_PURPOSES.has(purpose) ? "strength" : "cycling";
}

/** The strength half of the vocabulary. Everything else is cycling. */
const STRENGTH_PURPOSES: ReadonlySet<Purpose> = new Set<Purpose>([
  "max_strength",
  "strength_endurance",
  "hypertrophy",
  "power",
  "core",
  "mobility",
  "conditioning",
]);

/**
 * The status vocabulary as the status dot renders it.
 *
 * `planned` is the pending grey; the other three are the outcome colours the
 * whole app uses for completed / missed / trained-something-else.
 */
export const STATUS_TONES: Readonly<
  Record<SessionStatus, { label: string; color: string }>
> = {
  planned: { label: "Planned", color: "var(--color-status-pending)" },
  completed: { label: "Completed", color: "var(--color-status-completed)" },
  missed: { label: "Missed", color: "var(--color-status-missed)" },
  displaced: {
    label: "Trained something else",
    color: "var(--color-status-over)",
  },
};
