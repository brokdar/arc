/**
 * The checks the catalogue gate (`tests/i18n-catalogue.test.ts`) runs over
 * `en.json` and `de.json`. Pure functions over plain objects, so the test can
 * feed each one a catalogue built to break it as well as the committed ones.
 *
 * Every check answers with a list of problems, each naming the file and the
 * dotted key, so a failing run says which string to fix rather than that
 * some string is wrong.
 */

import { flattenCatalogue, placeholdersOf } from "@/lib/i18n/catalogue";

type Catalogues = Readonly<Record<string, unknown>>;

/** Every key present in one catalogue and missing from another. */
export function parityProblems(catalogues: Catalogues): string[] {
  const flat = Object.entries(catalogues).map(
    ([name, catalogue]) => [name, flattenCatalogue(catalogue)] as const,
  );
  const problems: string[] = [];
  for (const [name, keys] of flat) {
    for (const [otherName, otherKeys] of flat) {
      if (otherName === name) continue;
      for (const key of Object.keys(keys)) {
        if (!(key in otherKeys)) {
          problems.push(`${otherName} has no key "${key}"`);
        }
      }
    }
  }
  return problems;
}

/** A string that is empty or only whitespace renders as a hole in the UI. */
export function emptyValueProblems(name: string, catalogue: unknown): string[] {
  return Object.entries(flattenCatalogue(catalogue))
    .filter(([, value]) => value.trim() === "")
    .map(([key]) => `${name}: "${key}" is empty`);
}

/**
 * Keys whose `{placeholder}` sets differ between catalogues. A placeholder
 * missing from a translation drops the value; an extra one prints its braces.
 * Order is free — German puts the number where German grammar wants it.
 */
export function placeholderProblems(catalogues: Catalogues): string[] {
  const flat = Object.entries(catalogues).map(
    ([name, catalogue]) => [name, flattenCatalogue(catalogue)] as const,
  );
  const [first, ...rest] = flat;
  if (!first) return [];
  const [firstName, firstKeys] = first;
  const problems: string[] = [];
  for (const [key, value] of Object.entries(firstKeys)) {
    const expected = placeholdersOf(value).join(", ");
    for (const [name, keys] of rest) {
      const other = keys[key];
      if (other === undefined) continue; // parity's problem, not this one's
      const found = placeholdersOf(other).join(", ");
      if (found !== expected) {
        problems.push(
          `"${key}": ${firstName} has {${expected}}, ${name} has {${found}}`,
        );
      }
    }
  }
  return problems;
}

/**
 * Coaching shorthand an athlete should never have to decode. The model may
 * compute with these; the screen says what they mean ("reps left", "best
 * single lift") instead.
 */
export const BANNED_TERMS = [
  "RIR",
  "e1RM",
  "EWMA",
  "Epley",
  "MEV",
  "MAV",
  "MRV",
  "CR10",
  "ACWR",
] as const;

/**
 * Whole words only, any case: "FIRIR" and "Havana" are not jargon, "rir" is.
 * Not `\b`: it is ASCII-only, so it sees a boundary between "MAV" and a
 * following "ä". The boundary is written out as "not a letter or digit" in
 * Unicode terms instead.
 */
const BANNED = BANNED_TERMS.map(
  (term) =>
    [
      term,
      new RegExp(`(?<![\\p{L}\\p{N}])${term}(?![\\p{L}\\p{N}])`, "iu"),
    ] as const,
);

export function bannedTermProblems(name: string, catalogue: unknown): string[] {
  const problems: string[] = [];
  for (const [key, value] of Object.entries(flattenCatalogue(catalogue))) {
    for (const [term, pattern] of BANNED) {
      if (pattern.test(value)) {
        problems.push(`${name}: "${key}" uses "${term}"`);
      }
    }
  }
  return problems;
}

// --- Reading level -------------------------------------------------------

/**
 * Words in a string, for scoring. Placeholders are dropped (their value is a
 * number or a name, not a word the reader has to parse) and so is anything
 * with no letter in it.
 */
function wordsOf(text: string): string[] {
  return text
    .replace(/\{[^}]*\}/g, " ")
    .split(/[\s—–/]+/u)
    .map((token) => token.replace(/[^\p{L}\p{N}'-]/gu, ""))
    .filter((token) => /\p{L}/u.test(token));
}

function sentenceCount(text: string): number {
  const ends = text.match(/[.!?…]+(?=\s|$)/gu)?.length ?? 0;
  return Math.max(1, ends);
}

/**
 * English syllables by the usual vowel-group heuristic: drop a silent final
 * `e`/`es`/`ed`, then count runs of up to two vowels. Off by one on some
 * words, which is noise at the scale of a grade threshold.
 */
function syllablesEn(word: string): number {
  const w = word.toLowerCase().replace(/[^a-z]/g, "");
  if (w.length === 0) return 0;
  if (w.length <= 3) return 1;
  const trimmed = w
    .replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, "")
    .replace(/^y/, "");
  return Math.max(1, trimmed.match(/[aeiouy]{1,2}/g)?.length ?? 0);
}

/**
 * German syllables as vowel groups: a diphthong (`ei`, `au`, `eu`, `ie`)
 * is one run of vowels and therefore one syllable.
 */
function syllablesDe(word: string): number {
  const w = word.toLowerCase();
  return Math.max(1, w.match(/[aeiouyäöü]+/g)?.length ?? 0);
}

/** Flesch–Kincaid grade level of English text. */
export function fleschKincaidGrade(text: string): number {
  const words = wordsOf(text);
  if (words.length === 0) return 0;
  const syllables = words.reduce((sum, word) => sum + syllablesEn(word), 0);
  return (
    0.39 * (words.length / sentenceCount(text)) +
    11.8 * (syllables / words.length) -
    15.59
  );
}

/** The first Wiener Sachtextformel — a German school grade, 4 to 15. */
export function wienerSachtextformel(text: string): number {
  const words = wordsOf(text);
  if (words.length === 0) return 0;
  const share = (predicate: (word: string) => boolean) =>
    (100 * words.filter(predicate).length) / words.length;
  const ms = share((word) => syllablesDe(word) >= 3);
  const sl = words.length / sentenceCount(text);
  const iw = share((word) => word.length > 6);
  const es = share((word) => syllablesDe(word) === 1);
  return 0.1935 * ms + 0.1672 * sl + 0.1297 * iw - 0.0327 * es - 0.875;
}

/** The plainest copy: what the athlete reads mid-set or on the first run. */
const STRICT_PREFIXES = ["cards.", "onboarding."];
const STRICT_GRADE = 6;
const GRADE = 8;

/**
 * Below this many words a string is a label — "Success criteria", "Rest
 * day" — and not a sentence. Both formulas were fitted to running prose:
 * fed a two-word label they answer grade 20 for any word over two
 * syllables, which measures the word, not the reading. Labels are held to
 * the banned-term list instead; the grade applies from the first string
 * that can carry a clause.
 */
const MIN_WORDS = 4;

export function readingLevelProblems(
  language: "en" | "de",
  catalogue: unknown,
): string[] {
  const score = language === "en" ? fleschKincaidGrade : wienerSachtextformel;
  const problems: string[] = [];
  for (const [key, value] of Object.entries(flattenCatalogue(catalogue))) {
    if (wordsOf(value).length < MIN_WORDS) continue;
    const limit = STRICT_PREFIXES.some((prefix) => key.startsWith(prefix))
      ? STRICT_GRADE
      : GRADE;
    const grade = score(value);
    if (grade > limit) {
      problems.push(
        `${language}: "${key}" reads at grade ${grade.toFixed(1)} > ${limit}`,
      );
    }
  }
  return problems;
}
