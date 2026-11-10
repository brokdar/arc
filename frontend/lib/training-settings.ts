import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";

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
