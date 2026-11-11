"use client";

import type { EffortScale, Rating } from "@/components/strength/session-model";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/provider";

/**
 * The effort question, folded into the last working set's ✓: one tap logs
 * the set *and* rates the exercise.
 *
 * **The rating applies to every working set of the exercise.** It is asked
 * once, after the last working set, and the screen writes it onto each of
 * the exercise's working sets (a patch per earlier set, the value on the new
 * one) — never a target, and never a guess for a set nobody asked about.
 * Displaced: a prompt after every set, which costs a tap per set for a
 * number progression reads per exercise (§6.2, "one rating per exercise").
 * The test that holds this is AC-50 in `logging-screen.test.tsx`: rate the
 * third set "4+" and all three sets carry `rir: 4, rir_capped: true`.
 *
 * Not rating is fine: "log without rating" stores `rir: null` (or no Simple
 * answer) on every set — unrated, never "as planned".
 *
 * Simple mode asks *How did that feel?* and stores the word
 * (`effort_simple`); Standard and Full control ask for reps left, `4+` being
 * `rir: 4, rir_capped: true`. Ask frequency `always` and `last_set` both ask
 * here, once per exercise; `never` shows the plain ✓ instead. Every chip
 * carries its value as text, so nothing is told by colour alone.
 */
export function EffortRow({
  scale,
  onRate,
}: {
  scale: EffortScale;
  onRate: (rating: Rating) => void;
}) {
  const { t } = useI18n();
  const simple = scale === "easy_good_hard";
  const question = simple
    ? t("effort.questionSimple")
    : t("effort.questionReps");
  const chip =
    "h-auto min-h-[56px] min-w-0 flex-1 flex-col gap-0 px-1 py-1 font-mono text-lg leading-tight whitespace-normal";

  return (
    // biome-ignore lint/a11y/useSemanticElements: a fieldset would restyle the row; role=group names it the same way
    <div role="group" aria-label={question} className="flex flex-col gap-2">
      <p className="text-ink-secondary text-sm">{question}</p>
      <div className="flex gap-1.5">
        {simple
          ? (["easy", "good", "hard"] as const).map((answer) => (
              <Button
                key={answer}
                variant="outline"
                className={`${chip} font-sans`}
                aria-label={t("effort.simpleLabel", {
                  answer: t(`effort.${answer}`),
                })}
                onClick={() => onRate({ kind: "simple", answer })}
              >
                {t(`effort.${answer}`)}
              </Button>
            ))
          : [0, 1, 2, 3, 4].map((value) => (
              <Button
                key={value}
                variant="outline"
                className={chip}
                aria-label={
                  value === 0
                    ? t("effort.noneLabel")
                    : value === 4
                      ? t("effort.cappedLabel")
                      : t("effort.repsLeftLabel", { value: String(value) })
                }
                onClick={() =>
                  onRate({ kind: "reps", rir: value, capped: value === 4 })
                }
              >
                <span>{value === 4 ? "4+" : value}</span>
                {value === 0 ? (
                  <span className="font-sans text-2xs text-ink-muted">
                    {t("effort.none")}
                  </span>
                ) : null}
                {value === 4 ? (
                  <span className="font-sans text-2xs text-ink-muted">
                    {t("effort.feltEasy")}
                  </span>
                ) : null}
              </Button>
            ))}
      </div>
      <Button
        variant="secondary"
        className="h-[48px]"
        aria-label={t("effort.withoutRatingLabel")}
        onClick={() => onRate({ kind: "none" })}
      >
        {t("effort.withoutRating")}
      </Button>
    </div>
  );
}
