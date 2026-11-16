import { useEffect } from "react";

import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n/catalogue";
import { lastHighContrast, rememberHighContrast } from "@/lib/last-contact";

type Schemas = components["schemas"];
export type TrainingSettings = Schemas["TrainingSettingsRead"];
export type TrainingMode = Schemas["TrainingMode"];
export type LiftedBefore = Schemas["LiftedBefore"];
export type AskFrequency = Schemas["AskFrequency"];
export type Units = Schemas["Units"];
export type Locale = Schemas["Locale"];

/** The one cache key both Today's question and the Settings section read. */
export const trainingSettingsQueryKey = $api.queryOptions(
  "get",
  "/api/v1/settings/training",
).queryKey;

/**
 * Put `data-contrast="high"` on `<html>` while the athlete's `high_contrast`
 * setting is on, and take it off when it is not — the switch the
 * high-contrast token set in `app/globals.css` hangs off.
 *
 * On `<html>` rather than a wrapper element because the tokens are defined on
 * `:root`, and the shadcn aliases (`--color-foreground: var(--color-ink)`)
 * resolve where they are defined: re-pointing `--color-ink` lower in the tree
 * would leave every alias on the default value.
 *
 * Read from the same cache entry the Settings panel writes back, so ticking
 * the box re-skins the app with no reload. Offline — the gym with no signal,
 * which is the case this setting exists for — it applies the value the server
 * last served on this phone (`lib/last-contact.ts`), so a dim basement does
 * not lose the setting the moment the network goes. Unmounting removes the
 * attribute: outside the signed-in tree (the login page) nothing has read
 * the setting.
 */
export function useDocumentContrast(): void {
  const settings = $api.useQuery("get", "/api/v1/settings/training");
  const served = settings.data?.high_contrast;
  const high = served ?? lastHighContrast();

  useEffect(() => {
    if (served !== undefined) rememberHighContrast(served);
  }, [served]);

  useEffect(() => {
    const root = document.documentElement;
    if (high) {
      root.dataset.contrast = "high";
    } else {
      delete root.dataset.contrast;
    }
    return () => {
      delete root.dataset.contrast;
    };
  }, [high]);
}

/** The first-run answers, in the order the question offers them. */
export const LIFTED_BEFORE_KEYS: Readonly<Record<LiftedBefore, MessageKey>> = {
  never: "onboarding.firstRun.never",
  a_little: "onboarding.firstRun.aLittle",
  regularly: "onboarding.firstRun.regularly",
};

export const MODE_KEYS: Readonly<Record<TrainingMode, MessageKey>> = {
  simple: "settings.training.modeSimple",
  standard: "settings.training.modeStandard",
  full: "settings.training.modeFull",
};

/** One line each on what a mode changes in the gym, shown under the choice. */
export const MODE_HINT_KEYS: Readonly<Record<TrainingMode, MessageKey>> = {
  simple: "settings.training.hintSimple",
  standard: "settings.training.hintStandard",
  full: "settings.training.hintFull",
};

/**
 * What the effort question offers: two choices, not the three the API stores.
 *
 * `always` and `last_set` both ask once per exercise, after its last working
 * set (`components/strength/effort-row.tsx` — a prompt after every set was
 * displaced by design), so offering "After every set" promised something the
 * gym never did (strength UAT W2). The stored value stays as the API has it:
 * `always` is shown as the option it behaves as, and choosing it writes
 * `last_set`.
 */
export type AskChoice = "each" | "never";

export const ASK_CHOICES: Readonly<Record<AskChoice, MessageKey>> = {
  each: "settings.training.askEach",
  never: "settings.training.askNever",
};

export function askChoiceOf(stored: AskFrequency): AskChoice {
  return stored === "never" ? "never" : "each";
}

export function askFrequencyOf(choice: AskChoice): AskFrequency {
  return choice === "never" ? "never" : "last_set";
}

export const UNITS_KEYS: Readonly<Record<Units, MessageKey>> = {
  kg: "settings.training.unitsKg",
  lb: "settings.training.unitsLb",
};

/** A language is named in itself, whatever language the page is in. */
export const LOCALE_LABELS: Readonly<Record<Locale, string>> = {
  en: "English",
  de: "Deutsch",
};
