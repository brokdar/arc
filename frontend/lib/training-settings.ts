import { useEffect } from "react";

import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
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
export const LIFTED_BEFORE_LABELS: Readonly<Record<LiftedBefore, string>> = {
  never: "Never",
  a_little: "A little",
  regularly: "Regularly",
};

export const MODE_LABELS: Readonly<Record<TrainingMode, string>> = {
  simple: "Simple",
  standard: "Standard",
  full: "Full control",
};

/** One line each on what a mode changes in the gym, shown under the choice. */
export const MODE_HINTS: Readonly<Record<TrainingMode, string>> = {
  simple: "Rate each set Easy, Good or Hard; arc picks the next weight.",
  standard: "Say how many reps you had left; arc suggests, you adjust.",
  full: "Every control shown, nothing decided for you.",
};

export const ASK_FREQUENCY_LABELS: Readonly<Record<AskFrequency, string>> = {
  always: "After every set",
  last_set: "After the last set of each exercise",
  never: "Never",
};

export const UNITS_LABELS: Readonly<Record<Units, string>> = {
  kg: "Kilograms (kg)",
  lb: "Pounds (lb)",
};

export const LOCALE_LABELS: Readonly<Record<Locale, string>> = {
  en: "English",
  de: "Deutsch",
};
