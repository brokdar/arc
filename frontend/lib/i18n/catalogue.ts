import type { Locale } from "@/lib/training-settings";

import de from "./de.json";
import en from "./en.json";

/**
 * The message catalogues: every string a person reads on the shell and Today
 * (and every strength surface after them), in English and German.
 *
 * A dictionary chosen by the `locale` training setting, not Next's
 * route-segment i18n: arc has no `/de/...` routes and wants none — one
 * athlete, one language, set once in Settings and followed everywhere,
 * offline included. Displaced: locale-prefixed routes, which would put the
 * language in every bookmark and make switching it a navigation.
 *
 * `en.json` is the reference shape. `de.json` is typed against it here, so a
 * key missing from German is a type error as well as a failure of the
 * parity test (`tests/i18n-catalogue.test.ts`), which also catches the
 * reverse and checks wording.
 */
export type Messages = typeof en;

type Leaves<T, Prefix extends string = ""> = {
  [K in keyof T & string]: T[K] extends string
    ? `${Prefix}${K}`
    : Leaves<T[K], `${Prefix}${K}.`>;
}[keyof T & string];

/** A dotted path to one string, e.g. `"today.title"`. */
export type MessageKey = Leaves<Messages>;

export const CATALOGUES: Readonly<Record<Locale, Messages>> = {
  en,
  de: de satisfies Messages,
};

export const DEFAULT_LOCALE: Locale = "en";

/**
 * A locale arc has a catalogue for, or English. The server only ever serves
 * `en` or `de`; this is for the value remembered on the phone, which could
 * be anything a previous build or a hand-edited storage wrote.
 */
export function resolveLocale(value: unknown): Locale {
  return typeof value === "string" && Object.hasOwn(CATALOGUES, value)
    ? (value as Locale)
    : DEFAULT_LOCALE;
}

/** Nested catalogue → `{ "a.b.c": "text" }`. Non-string leaves are skipped. */
export function flattenCatalogue(
  catalogue: unknown,
  prefix = "",
): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof catalogue !== "object" || catalogue === null) return out;
  for (const [key, value] of Object.entries(catalogue)) {
    const path = `${prefix}${key}`;
    if (typeof value === "string") {
      out[path] = value;
    } else {
      Object.assign(out, flattenCatalogue(value, `${path}.`));
    }
  }
  return out;
}

const PLACEHOLDER = /\{(\w+)\}/g;

/** The distinct `{name}` placeholders in a string, sorted. */
export function placeholdersOf(text: string): string[] {
  return [...new Set([...text.matchAll(PLACEHOLDER)].map((m) => m[1] ?? ""))]
    .filter(Boolean)
    .sort();
}

const FLAT: Readonly<Record<Locale, Record<string, string>>> = {
  en: flattenCatalogue(en),
  de: flattenCatalogue(de),
};

export type MessageParams = Readonly<Record<string, string | number>>;

/**
 * One message, its placeholders filled. A number is formatted by the caller
 * (`useI18n`), so it arrives here as the string the locale writes. A key
 * absent from the locale falls back to English, and then to the key itself —
 * visible, so a gap is noticed rather than rendered as nothing.
 */
export function translate(
  locale: Locale,
  key: MessageKey,
  params: Readonly<Record<string, string>> = {},
): string {
  const text = FLAT[locale][key] ?? FLAT.en[key] ?? key;
  return text.replace(PLACEHOLDER, (match, name: string) =>
    Object.hasOwn(params, name) ? (params[name] ?? match) : match,
  );
}
