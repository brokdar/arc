"use client";

import { useState } from "react";

import {
  PAIN_BANDS,
  PAIN_KINDS,
  type PainBand,
  type PainKind,
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
 * ⋯ → Pain: two taps, kind then band, on the set just logged.
 *
 * Two kinds and three bands, no body chart and no free text: the athlete is
 * mid-session, maybe hurting, and the answer has to drive a rule
 * (`safety-model.ts`), not a conversation. The second tap applies it; the
 * screen then offers a one-tap undo.
 */
export function PainPanel({
  onReport,
  onClose,
}: {
  onReport: (kind: PainKind, band: PainBand) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [kind, setKind] = useState<PainKind | null>(null);

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="w-[min(24rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5">
        <DialogHeader>
          <DialogTitle>{t("safety.pain.title")}</DialogTitle>
        </DialogHeader>
        <p className="text-ink-secondary text-sm">
          {kind === null ? t("safety.pain.which") : t("safety.pain.howBad")}
        </p>
        <div className="flex flex-col gap-2">
          {kind === null
            ? PAIN_KINDS.map((option) => (
                <Button
                  key={option.kind}
                  variant="outline"
                  className="h-[56px] text-base"
                  onClick={() => setKind(option.kind)}
                >
                  {t(option.label)}
                </Button>
              ))
            : PAIN_BANDS.map((option) => (
                <Button
                  key={option.band}
                  variant="outline"
                  className="h-[56px] font-mono text-base"
                  onClick={() => onReport(kind, option.band)}
                >
                  {t(option.label)}
                </Button>
              ))}
          <Button
            variant="ghost"
            className="h-[44px]"
            onClick={() => (kind === null ? onClose() : setKind(null))}
          >
            {t("safety.pain.back")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
