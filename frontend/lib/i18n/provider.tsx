"use client";

import {
  createContext,
  Fragment,
  type ReactNode,
  useContext,
  useEffect,
} from "react";

import { $api } from "@/lib/api/client";
import { weekdayIndex } from "@/lib/dates";
import { formatNumber, formatWeight } from "@/lib/format";
import {
  DEFAULT_LOCALE,
  type MessageKey,
  type MessageParams,
  resolveLocale,
  translate,
} from "@/lib/i18n/catalogue";
import { lastLocale, rememberLocale } from "@/lib/last-contact";
import { type Locale, useDocumentContrast } from "@/lib/training-settings";

export type Translate = (key: MessageKey, params?: MessageParams) => string;

export interface I18n {
  readonly locale: Locale;
  /** One message, numbers in `params` written the locale's way. */
  readonly t: Translate;
  /**
   * One message whose placeholders are elements, not text — a word in a
   * different colour, a time in the mono face. Text placeholders are
   * formatted as `t` formats them.
   */
  readonly rich: (
    key: MessageKey,
    params: Readonly<Record<string, ReactNode>>,
  ) => ReactNode;
  readonly number: (value: number) => string;
  readonly weight: (value: number, unit?: "kg" | "lb") => string;
  /** The short weekday of an ISO date: `Mon` / `Mo`. */
  readonly weekday: (isoDate: string) => string;
}

const WEEKDAY_KEYS = [
  "weekdays.mon",
  "weekdays.tue",
  "weekdays.wed",
  "weekdays.thu",
  "weekdays.fri",
  "weekdays.sat",
  "weekdays.sun",
] as const satisfies readonly MessageKey[];

function textParams(
  locale: Locale,
  params: MessageParams = {},
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).map(([name, value]) => [
      name,
      typeof value === "number" ? formatNumber(value, locale) : value,
    ]),
  );
}

function buildI18n(locale: Locale): I18n {
  const t: Translate = (key, params) =>
    translate(locale, key, textParams(locale, params));
  return {
    locale,
    t,
    rich: (key, params) => {
      // Translate with the placeholders left in, then cut the text at each
      // one and put the element in the gap.
      const text = translate(locale, key);
      return text.split(/(\{\w+\})/).map((part, index) => {
        const name = /^\{(\w+)\}$/.exec(part)?.[1];
        const value =
          name !== undefined && Object.hasOwn(params, name)
            ? params[name]
            : part;
        // The parts are a fixed sequence for one message; position is identity.
        // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
        return <Fragment key={index}>{value}</Fragment>;
      });
    },
    number: (value) => formatNumber(value, locale),
    weight: (value, unit = "kg") => formatWeight(value, locale, unit),
    weekday: (isoDate) =>
      t(WEEKDAY_KEYS[weekdayIndex(isoDate)] ?? "weekdays.mon"),
  };
}

const BUILT: Readonly<Record<Locale, I18n>> = {
  en: buildI18n("en"),
  de: buildI18n("de"),
};

/**
 * English, outside any provider: the login page sits outside the signed-in
 * tree and cannot read a setting that needs a session, and a component test
 * that does not care about language renders exactly as it did before
 * catalogues existed.
 */
const I18nContext = createContext<I18n>(BUILT[DEFAULT_LOCALE]);

/**
 * Publish a language to the tree below. The plumbing, without the fetch —
 * as `AthleteClock` is to `ClockProvider`: for a test that names its
 * language in the test. The app uses `I18nProvider`. An unknown value is
 * English.
 */
export function LocaleProvider({
  locale,
  children,
}: {
  readonly locale: string;
  readonly children: ReactNode;
}) {
  const resolved = resolveLocale(locale);
  useEffect(() => {
    document.documentElement.lang = resolved;
  }, [resolved]);
  return <I18nContext value={BUILT[resolved]}>{children}</I18nContext>;
}

/**
 * The language the athlete chose in Settings, for the whole signed-in app.
 *
 * Read from `GET /settings/training` — the same cache entry the Settings
 * panel writes back after a change (`trainingSettingsQueryKey`), so choosing
 * English there re-renders every string here with no reload. Until the
 * settings arrive, or when they cannot (the gym with no signal), it speaks
 * the language the server last served on this phone (`lib/last-contact.ts`),
 * and English if it never has. Unlike the clock it never blocks the page: a
 * wrong-language label is a nuisance, a wrong timezone is a wrong day.
 *
 * It also applies the high-contrast setting to `<html>`: this is the one
 * component that reads the training settings for the whole signed-in tree
 * and writes onto the document (`lang` here, `data-contrast` there), and it
 * sits above the clock gate, so a dim gym gets its contrast before Today
 * has finished loading.
 */
export function I18nProvider({ children }: { readonly children: ReactNode }) {
  const settings = $api.useQuery("get", "/api/v1/settings/training");
  const served = settings.data?.locale;
  useDocumentContrast();

  useEffect(() => {
    if (served !== undefined) rememberLocale(served);
  }, [served]);

  return (
    <LocaleProvider locale={resolveLocale(served ?? lastLocale())}>
      {children}
    </LocaleProvider>
  );
}

export function useI18n(): I18n {
  return useContext(I18nContext);
}

export function useT(): Translate {
  return useContext(I18nContext).t;
}
