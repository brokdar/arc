"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { useI18n } from "@/lib/i18n/provider";

export interface FinishChoice {
  /** 1-10, or null when skipped — a skipped rating stays empty (S10.4). */
  rating: number | null;
  dontLearn: boolean;
  /** The private reason, only with `dontLearn`. */
  reason: string | null;
}

const REASONS = [
  ["ill", "finish.reasonIll"],
  ["bad sleep", "finish.reasonSleep"],
  ["period", "finish.reasonPeriod"],
  ["chaotic gym", "finish.reasonGym"],
  ["other", "finish.reasonOther"],
] as const;

/**
 * Finish: the whole-session rating, where tapping a number *is* finishing —
 * so Finish after the last set is two taps with the rating (S16.1). "Don't
 * learn from today" and its optional private reason are set before that tap;
 * "Finish without rating" leaves the rating empty rather than guessing one.
 */
export function FinishPanel({
  onFinish,
  onBack,
}: {
  onFinish: (choice: FinishChoice) => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const [dontLearn, setDontLearn] = useState(false);
  const [reason, setReason] = useState("");
  const checkbox = useId();
  const select = useId();

  const finish = (rating: number | null) =>
    onFinish({
      rating,
      dontLearn,
      reason: dontLearn && reason ? reason : null,
    });

  return (
    <section
      aria-labelledby={`${checkbox}-title`}
      className="flex flex-col gap-4 px-4 py-4"
    >
      <h2 id={`${checkbox}-title`} className="font-semibold text-xl">
        {t("finish.title")}
      </h2>
      {/* biome-ignore lint/a11y/useSemanticElements: a fieldset would restyle the grid; role=group names it the same way */}
      <div
        role="group"
        aria-label={t("finish.question")}
        className="flex flex-col gap-2"
      >
        <p className="text-ink-secondary text-sm">{t("finish.question")}</p>
        <div className="grid grid-cols-5 gap-1.5">
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <Button
              key={n}
              variant="outline"
              className="h-[56px] min-w-0 font-mono text-lg"
              aria-label={t("finish.ratingLabel", { n: String(n) })}
              onClick={() => finish(n)}
            >
              {n}
            </Button>
          ))}
        </div>
        <p className="font-mono text-ink-muted text-xs">{t("finish.scale")}</p>
      </div>
      <div className="flex flex-col gap-2">
        <div className="flex min-h-[44px] items-center gap-3">
          <input
            id={checkbox}
            type="checkbox"
            className="size-5"
            aria-describedby={`${checkbox}-help`}
            checked={dontLearn}
            onChange={(event) => setDontLearn(event.target.checked)}
          />
          <span className="flex flex-col">
            <label htmlFor={checkbox} className="text-ink">
              {t("finish.dontLearn")}
            </label>
            <span id={`${checkbox}-help`} className="text-ink-muted text-xs">
              {t("finish.dontLearnHelp")}
            </span>
          </span>
        </div>
        {dontLearn ? (
          <div className="flex flex-col gap-1">
            <label htmlFor={select} className="text-ink-secondary text-sm">
              {t("finish.reason")}
            </label>
            <NativeSelect
              id={select}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            >
              <option value="">{t("finish.reasonNone")}</option>
              {REASONS.map(([value, key]) => (
                <option key={value} value={value}>
                  {t(key)}
                </option>
              ))}
            </NativeSelect>
          </div>
        ) : null}
      </div>
      <div className="flex flex-col gap-2">
        <Button
          className="h-[56px]"
          variant="secondary"
          onClick={() => finish(null)}
        >
          {t("finish.withoutRating")}
        </Button>
        <Button className="h-[44px]" variant="ghost" onClick={onBack}>
          {t("finish.back")}
        </Button>
      </div>
    </section>
  );
}
