"use client";

import { useState } from "react";

import {
  STARTING_REPS,
  type StartingAnswer,
} from "@/components/strength/session-model";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/provider";
import {
  type Equipment,
  type Gym,
  lbToKg,
  platesPerSide,
} from "@/lib/strength";

/**
 * The guided starting weight for an exercise with no history: "Do 5 easy
 * reps with this weight", then "How did that feel?" — and the next weight,
 * until Hard settles the working weight.
 *
 * It exists because a first-time exercise has nothing to pre-fill and a
 * beginner cannot judge "8 reps with 2 left". So the flow asks the one
 * question anyone can answer after five light reps, starting at the lightest
 * thing the gym has (the empty bar, the lightest dumbbell or stack weight)
 * and stepping with `startingStep` from `lib/strength` — the vector-checked
 * twin of the server's arithmetic, so it works with no signal. Displaced: a
 * max test, which a beginner should not do and a phone cannot supervise.
 *
 * The rules (what each answer does, that Hard settles, that the easy sets are
 * warm-ups) live in `session-model.ts` (`answerStarting`); this component
 * only shows the weight and reports the answer. Typing a weight skips the
 * flow: someone who already knows their number is not walked through it.
 *
 * Every word on it comes from the `starting.*` catalogue keys, so the flow
 * reads as one thing and not as the logging screen's set controls with a
 * question bolted on (`starting-flow.test.tsx` holds this).
 */
export function StartingFlow({
  kg,
  equipment,
  gym,
  units,
  weightText,
  onAnswer,
  onType,
}: {
  kg: number;
  equipment: Equipment;
  gym: Gym;
  units: "kg" | "lb";
  weightText: (kg: number) => string;
  onAnswer: (answer: StartingAnswer) => void;
  onType: (kg: number) => void;
}) {
  const { t } = useI18n();
  const [typed, setTyped] = useState("");
  const title = t("starting.title");
  const plates =
    equipment === "barbell" ? platesPerSide(kg, equipment, gym) : null;
  const reps = String(STARTING_REPS);

  const typedKg = (() => {
    const value = Number(typed.replace(",", "."));
    if (typed.trim() === "" || !Number.isFinite(value) || value <= 0) {
      return null;
    }
    return units === "lb" ? Math.round(lbToKg(value) * 100) / 100 : value;
  })();

  return (
    <section aria-label={title} className="flex flex-col gap-3 pt-1">
      <h2 className="font-semibold text-lg">{title}</h2>
      <p>{t("starting.instruction", { reps })}</p>
      <p className="font-mono font-semibold text-3xl">
        {t("starting.total", { weight: weightText(kg) })}
      </p>
      {plates ? (
        <p className="font-mono text-ink-muted text-sm">
          {plates.length > 0
            ? t("starting.plates", {
                plates: plates.map((plate) => weightText(plate)).join(" · "),
              })
            : t("starting.emptyBar")}
        </p>
      ) : null}
      {/* biome-ignore lint/a11y/useSemanticElements: a fieldset would restyle the row; role=group names it the same way */}
      <div
        role="group"
        aria-label={t("starting.question")}
        className="flex flex-col gap-2"
      >
        <p className="text-ink-secondary text-sm">{t("starting.question")}</p>
        <div className="flex gap-1.5">
          {(["easy", "good", "hard"] as const).map((answer) => (
            <Button
              key={answer}
              variant="outline"
              className="h-[56px] min-w-0 flex-1 text-lg"
              aria-label={t("starting.answerLabel", {
                answer: t(`starting.${answer}`),
                reps,
              })}
              onClick={() => onAnswer(answer)}
            >
              {t(`starting.${answer}`)}
            </Button>
          ))}
        </div>
      </div>
      <form
        className="flex flex-col gap-1.5 border-hairline border-t pt-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          if (typedKg !== null) onType(typedKg);
        }}
      >
        <p className="text-ink-secondary">{t("starting.typeHint")}</p>
        <div className="flex gap-2">
          <input
            inputMode="decimal"
            aria-label={t("starting.typeLabel")}
            className="h-[48px] min-w-0 flex-1 rounded-lg border border-hairline-strong bg-inset px-3 font-mono text-lg text-ink"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
          <Button
            type="submit"
            variant="secondary"
            className="h-[48px]"
            disabled={typedKg === null}
          >
            {t("starting.use")}
          </Button>
        </div>
      </form>
    </section>
  );
}
