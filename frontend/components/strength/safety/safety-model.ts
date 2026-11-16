import type { components } from "@/generated/api/schema";
import type { MessageKey } from "@/lib/i18n/catalogue";

/**
 * The gym screen's half of `backend/app/domain/safety.py`, decided on the
 * phone because the phone may have no signal when it is needed.
 *
 * - **Sharp pain stops the exercise for today**: once any working set of a
 *   line carries `pain_kind: "sharp"`, no set of it is left to do
 *   (`setsLeftToday`, the server's `sets_left_today`). The plan line itself
 *   is never touched — on a follow-exactly plan above all — and taking the
 *   note back gives the sets back.
 * - **A red flag's text** depends only on the symptom's group
 *   (`RED_FLAG_GROUP`, the server's table of the same name), so it is shown
 *   at once, offline, and the outbox carries the symptom for the server to
 *   raise the flag.
 */

type Schemas = components["schemas"];
export type PainKind = Schemas["PainKind"];
export type PainBand = Schemas["PainBand"];
export type RedFlagSymptom = Schemas["RedFlagSymptom"];
export type RedFlagKind = Schemas["RedFlagKind"];

export function stopsExercise(kind: PainKind | null | undefined): boolean {
  return kind === "sharp";
}

/** Working sets still to do on a line: none once one had sharp pain. */
export function setsLeftToday(
  prescribed: number,
  loggedPain: readonly (PainKind | null | undefined)[],
): number {
  if (loggedPain.some(stopsExercise)) return 0;
  return Math.max(prescribed - loggedPain.length, 0);
}

export const PAIN_KINDS: readonly { kind: PainKind; label: MessageKey }[] = [
  { kind: "ache", label: "safety.pain.ache" },
  { kind: "sharp", label: "safety.pain.sharp" },
];

export const PAIN_BANDS: readonly { band: PainBand; label: MessageKey }[] = [
  { band: "0-3", label: "safety.pain.mild" },
  { band: "4-5", label: "safety.pain.moderate" },
  { band: "6+", label: "safety.pain.severe" },
];

export const RED_FLAG_GROUP: Readonly<Record<RedFlagSymptom, RedFlagKind>> = {
  chest_pain: "emergency",
  cannot_breathe: "emergency",
  fainted: "emergency",
  numbness: "advice",
  dizziness: "advice",
  sudden_swelling: "advice",
};

/** The symptoms in the order the list offers them: emergency group first. */
export const SYMPTOMS: readonly {
  symptom: RedFlagSymptom;
  label: MessageKey;
}[] = [
  { symptom: "chest_pain", label: "safety.redFlag.chestPain" },
  { symptom: "cannot_breathe", label: "safety.redFlag.cannotBreathe" },
  { symptom: "fainted", label: "safety.redFlag.fainted" },
  { symptom: "numbness", label: "safety.redFlag.numbness" },
  { symptom: "dizziness", label: "safety.redFlag.dizziness" },
  { symptom: "sudden_swelling", label: "safety.redFlag.swelling" },
];

export const RED_FLAG_TEXT: Readonly<Record<RedFlagKind, MessageKey>> = {
  emergency: "safety.redFlag.emergency",
  advice: "safety.redFlag.advice",
};
