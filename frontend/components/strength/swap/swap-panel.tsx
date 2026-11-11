"use client";

import { useState } from "react";

import type { PacketLine } from "@/components/strength/session-model";
import type { SwapScope } from "@/components/strength/swap/swap-model";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/lib/i18n/provider";

/**
 * ⋯ → Swap → pick: the third tap applies the swap.
 *
 * The three options are the packet's (`line.swaps`), so the panel works with
 * no signal. The scope defaults to *this session* — the rack is taken today
 * — so choosing it costs no tap; *rest of block* is one extra tap before
 * the pick, and the server turns it into a pending card rather than a plan
 * edit (`backend/app/domain/swaps.py`, `SwapScope`).
 */
export function SwapPanel({
  line,
  onPick,
  onClose,
}: {
  line: PacketLine;
  onPick: (exerciseId: string, scope: SwapScope) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [scope, setScope] = useState<SwapScope>("session");
  const title = t("swap.title", { exercise: line.exercise_name });

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent
        aria-label={title}
        className="w-[min(24rem,calc(100vw-2rem))] gap-4 rounded-shell border border-hairline-card bg-panel p-5"
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <fieldset className="flex gap-2">
          <legend className="sr-only">{t("swap.scope")}</legend>
          {(["session", "rest_of_block"] as const).map((option) => (
            <label
              key={option}
              className="flex min-h-[44px] flex-1 items-center gap-2 rounded-md border border-hairline-strong px-3 text-sm"
            >
              <input
                type="radio"
                name="swap-scope"
                value={option}
                checked={scope === option}
                onChange={() => setScope(option)}
              />
              {option === "session"
                ? t("swap.thisSession")
                : t("swap.restOfBlock")}
            </label>
          ))}
        </fieldset>
        <SwapChoices line={line} onPick={(id) => onPick(id, scope)} />
        <Button variant="ghost" className="h-[44px]" onClick={onClose}>
          {t("swap.back")}
        </Button>
      </DialogContent>
    </Dialog>
  );
}

/** The packet's swaps for `line` as one button each, best first. */
export function SwapChoices({
  line,
  onPick,
}: {
  line: PacketLine;
  onPick: (exerciseId: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      {line.swaps.map((swap) => (
        <Button
          key={swap.exercise_id}
          variant="outline"
          className="h-[56px] text-base"
          onClick={() => onPick(swap.exercise_id)}
        >
          {swap.exercise_name}
        </Button>
      ))}
    </div>
  );
}

/**
 * The prompt an avoided exercise opens on: the athlete said never to offer
 * it, so the session asks for a swap before the first set rather than
 * pre-filling a movement they avoid.
 */
export function SwapPrompt({
  line,
  onPick,
}: {
  line: PacketLine;
  onPick: (exerciseId: string) => void;
}) {
  const { t } = useI18n();
  const heading = t("swap.avoided", { exercise: line.exercise_name });
  return (
    <section
      aria-label={heading}
      className="flex flex-col gap-2 rounded-lg border border-hairline-strong bg-raised px-3 py-3"
    >
      <h2 className="font-semibold">{heading}</h2>
      <p className="text-ink-secondary text-sm">{t("swap.avoidedBody")}</p>
      <SwapChoices line={line} onPick={onPick} />
    </section>
  );
}
