"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetCloseButton,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import type { components } from "@/generated/api/schema";
import type { MessageKey } from "@/lib/i18n/catalogue";
import { useI18n } from "@/lib/i18n/provider";

type Suggestion = components["schemas"]["SuggestionRead"];
type Input = Suggestion["explanation"]["inputs"][number];

/** The registry rule ids the server names, and their words. */
const RULE_KEYS: Readonly<Record<string, MessageKey>> = {
  "progression.weight_from_effort": "why.rule.weightFromEffort",
  "progression.reps_then_weight": "why.rule.repsThenWeight",
  "progression.simple": "why.rule.simple",
  "progression.backoff_drop": "why.rule.backoff",
  "progression.plateau.short_window": "why.rule.plateau",
  "progression.plateau.reset": "why.rule.reset",
  "progression.hold": "why.rule.hold",
};

const LIMIT_KEYS: Readonly<Record<string, MessageKey>> = {
  "progression.cap.up": "why.limitUp",
  "progression.cap.down": "why.limitDown",
  "progression.cap.small_muscle_steps": "why.limitStep",
  "progression.big_step": "why.limitBigStep",
};

/** The reasons that read two sessions, so line one names both. */
const TWICE = new Set<Suggestion["explanation"]["reason"]>([
  "easier_twice",
  "harder_twice",
  "top_of_range_twice",
  "simple_good_twice",
  "simple_hard_twice",
]);

const WEIGHT_FROM_EFFORT = "progression.weight_from_effort";

/**
 * The reason chip on the logging screen, and the Why sheet behind it.
 *
 * Three layers, as the direction draws them: the chip (eight words at most:
 * what changed and why), *Why?* (three plain lines — what you did, what that
 * means for today, and that the athlete can change it), and *Details* (the
 * arithmetic, the rounding and the rule). **Every word is built from the
 * explanation the server sends** (`SuggestionRead.explanation`, computed by
 * `app.domain.progression`): the phone renders, it does not decide, so
 * nothing here recomputes a weight — the formula line prints the inputs and
 * the server's own pre-rounding result side by side.
 *
 * Without a suggestion (Plain logbook, follow-exactly, a hold) the chip shows
 * `fallback`, the pre-fill's own reason; a first-time exercise says "No
 * history yet". Neither has a Why to open.
 */
export function WhySheet({
  suggestion,
  fallback,
  weightText,
}: {
  readonly suggestion: Suggestion | null | undefined;
  /** The chip text when there is no suggestion. */
  readonly fallback: string;
  /** A weight in kg as the athlete reads it (their unit). */
  readonly weightText?: (kg: number) => string;
}) {
  const i18n = useI18n();
  const { t } = i18n;
  const [details, setDetails] = useState(false);
  const weight = weightText ?? ((kg: number) => i18n.weight(kg));

  const chipClass =
    "inline-flex rounded-md border border-accent-border bg-accent-surface px-2 py-0.5 text-accent-quiet text-xs";

  if (!suggestion || suggestion.load_kg === null) {
    const text =
      suggestion?.outcome === "first_time" ? t("why.firstTime") : fallback;
    return (
      <p>
        <span className={chipClass}>{text}</span>
      </p>
    );
  }

  const why = suggestion.explanation;
  const load = suggestion.load_kg;
  const because = t(`why.because.${why.reason}` as MessageKey);
  const change = Math.abs(suggestion.change_kg ?? 0);
  const chip =
    suggestion.outcome === "up"
      ? t("why.chipUp", { change: weight(change), because })
      : suggestion.outcome === "down"
        ? t("why.chipDown", { change: weight(change), because })
        : suggestion.outcome === "reps"
          ? t("why.chipReps", { because })
          : t("why.chipSame", { because });

  const setText = (input: Input) =>
    input.load_kg === null
      ? `× ${input.reps ?? 0}`
      : `${weight(input.load_kg)} × ${input.reps ?? 0}`;
  const effortText = (input: Input) => {
    if (input.reps_left !== null) {
      return input.reps_left_capped
        ? t("why.withLeftCapped")
        : t("why.withLeft", { left: input.reps_left });
    }
    if (input.effort_simple !== null) {
      return t("why.rated", {
        answer: t(`why.answer.${input.effort_simple}` as MessageKey),
      });
    }
    return t("why.notRated");
  };

  const inputs = why.inputs;
  const last = inputs[inputs.length - 1];
  const first = inputs[0];
  let what = "";
  if (TWICE.has(why.reason) && inputs.length > 1 && first && last) {
    const same =
      setText(first) === setText(last) &&
      effortText(first) === effortText(last);
    what = same
      ? t("why.lastTwo", { set: setText(last), effort: effortText(last) })
      : t("why.lastTwoDiffer", {
          first: setText(first),
          firstEffort: effortText(first),
          second: setText(last),
          secondEffort: effortText(last),
        });
  } else if (last) {
    what = t("why.lastOne", { set: setText(last), effort: effortText(last) });
  }
  if (why.target_reps_left !== null) {
    what = `${what} ${t("why.aims", { left: why.target_reps_left })}`;
  }

  const so =
    suggestion.outcome === "up"
      ? t("why.soUp", { weight: weight(load) })
      : suggestion.outcome === "down"
        ? t("why.soDown", { weight: weight(load) })
        : suggestion.outcome === "reps"
          ? t("why.soReps", {
              weight: weight(load),
              reps: suggestion.reps ?? 0,
            })
          : t("why.soSame", { weight: weight(load) });

  const computed =
    why.computed_kg === null ? null : Math.round(why.computed_kg * 10) / 10;
  const formula =
    computed !== null &&
    why.rule === WEIGHT_FROM_EFFORT &&
    last?.load_kg != null &&
    last.reps !== null &&
    last.reps_left !== null &&
    why.target_reps !== null &&
    why.target_reps_left !== null
      ? t("why.formula", {
          load: weight(last.load_kg),
          reps: last.reps,
          left: last.reps_left,
          targetReps: why.target_reps,
          // The end of a reps-left range the server held the report to,
          // so this line is its arithmetic, not a re-derivation.
          targetLeft: why.target_reps_left,
          computed: weight(computed),
        })
      : computed !== null
        ? t("why.computed", { computed: weight(computed) })
        : null;
  const limitKey = why.limit ? LIMIT_KEYS[why.limit] : undefined;
  const ruleKey = why.rule ? RULE_KEYS[why.rule] : undefined;
  const basis =
    why.rule === WEIGHT_FROM_EFFORT
      ? t("why.basis.estimate")
      : why.evidence
        ? t(`why.basis.${why.evidence}` as MessageKey)
        : null;

  return (
    <Sheet onOpenChange={() => setDetails(false)}>
      <p className="flex items-center gap-2">
        <span className={chipClass}>{chip}</span>
        <SheetTrigger
          render={
            <Button
              variant="ghost"
              className="h-[44px] min-w-[44px] text-xs"
              aria-label={t("why.openLabel")}
            />
          }
        >
          {t("why.open")}
        </SheetTrigger>
      </p>
      <SheetContent>
        <header className="flex flex-col gap-2.5 border-hairline border-b px-6 py-5">
          <div className="flex items-center justify-end">
            <SheetCloseButton />
          </div>
          <SheetTitle>{t("why.title", { weight: weight(load) })}</SheetTitle>
          <SheetDescription>{chip}</SheetDescription>
        </header>
        <div className="flex flex-col gap-3 px-6 py-5 text-sm">
          <p>{what}</p>
          <p>{so}</p>
          <p className="text-ink-muted">{t("why.change")}</p>
          <Button
            variant="outline"
            className="h-[44px] self-start"
            aria-expanded={details}
            onClick={() => setDetails((open) => !open)}
          >
            {t("why.details")}
          </Button>
          {details ? (
            <div className="flex flex-col gap-1 font-mono text-ink-muted text-xs">
              {formula ? <p>{formula}</p> : null}
              {limitKey ? <p>{t(limitKey)}</p> : null}
              <p>{t("why.rounded", { weight: weight(load) })}</p>
              {ruleKey && basis ? (
                <p className="font-sans">
                  {t("why.ruleLine", { rule: t(ruleKey), basis })}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
