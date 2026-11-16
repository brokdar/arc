"use client";

import {
  RED_FLAG_TEXT,
  type RedFlagKind,
  type RedFlagSymptom,
  SYMPTOMS,
} from "@/components/strength/safety/safety-model";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/lib/i18n/provider";

/**
 * ⋯ → *Stop — something's wrong*: one tap on a symptom ends the session.
 *
 * No confirmation step: a confirmation is one more tap between someone with
 * chest pain and the words "call emergency services". Back is there for the
 * mis-tap that opened the list.
 */
export function RedFlagPanel({
  onChoose,
  onClose,
}: {
  onChoose: (symptom: RedFlagSymptom) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="w-[min(24rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5">
        <DialogHeader>
          <DialogTitle>{t("safety.redFlag.title")}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          {SYMPTOMS.map((option) => (
            <Button
              key={option.symptom}
              variant="outline"
              className="h-[52px] text-base"
              onClick={() => onChoose(option.symptom)}
            >
              {t(option.label)}
            </Button>
          ))}
          <Button variant="ghost" className="h-[44px]" onClick={onClose}>
            {t("safety.redFlag.back")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** What the screen says after a red flag, until the athlete leaves it. */
export function RedFlagStop({ kind }: { kind: RedFlagKind }) {
  const { t } = useI18n();
  return (
    <>
      <p role="alert" className="font-semibold text-2xl text-destructive">
        {t(RED_FLAG_TEXT[kind])}
      </p>
      <p className="text-ink-muted">{t("safety.redFlag.ended")}</p>
    </>
  );
}
