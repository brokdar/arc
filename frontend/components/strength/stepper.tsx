"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n/provider";

const HOLD_DELAY_MS = 450;
const REPEAT_MS = 110;

/**
 * One number with − and + either side: a tap steps once, holding repeats,
 * and tapping the number opens the keypad (a numeric input) for a value the
 * steps would take long to reach.
 *
 * The steps are the caller's (`onStep(±1)`): a weight moves through the
 * gym's loadable weights, reps by one. The click does the single step, so a
 * keyboard and a screen reader step exactly as a thumb does; a hold that
 * started repeating swallows the click that ends it.
 */
export function Stepper({
  what,
  display,
  value,
  onStep,
  onEnter,
  decimal = false,
}: {
  /** "Weight", "Reps": names the controls. */
  what: string;
  /** The value as shown, with its unit. */
  display: string;
  /** The value the keypad opens with, as typed (display units). */
  value: number | null;
  onStep: (direction: 1 | -1) => void;
  onEnter: (value: number) => void;
  decimal?: boolean;
}) {
  const { t } = useI18n();
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const hold = useRef<{ delay?: number; repeat?: number; fired: boolean }>({
    fired: false,
  });
  const step = useRef(onStep);
  step.current = onStep;

  const stop = useCallback(() => {
    window.clearTimeout(hold.current.delay);
    window.clearInterval(hold.current.repeat);
  }, []);
  useEffect(() => stop, [stop]);

  const holdable = (direction: 1 | -1) => ({
    onPointerDown: () => {
      stop();
      hold.current.fired = false;
      hold.current.delay = window.setTimeout(() => {
        hold.current.fired = true;
        step.current(direction);
        hold.current.repeat = window.setInterval(
          () => step.current(direction),
          REPEAT_MS,
        );
      }, HOLD_DELAY_MS);
    },
    onPointerUp: stop,
    onPointerLeave: stop,
    onPointerCancel: stop,
    onClick: () => {
      if (hold.current.fired) {
        hold.current.fired = false;
        return;
      }
      step.current(direction);
    },
  });

  const commit = () => {
    const parsed = Number(draft.replace(",", "."));
    if (draft.trim() !== "" && Number.isFinite(parsed) && parsed >= 0) {
      onEnter(parsed);
    }
    setTyping(false);
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        className="size-[56px] shrink-0 font-mono text-2xl"
        aria-label={t("train.decrease", { what })}
        {...holdable(-1)}
      >
        −
      </Button>
      {typing ? (
        <input
          // biome-ignore lint/a11y/noAutofocus: the athlete just asked for the keypad
          autoFocus
          aria-label={what}
          inputMode={decimal ? "decimal" : "numeric"}
          className="h-[56px] min-w-0 flex-1 rounded-lg border border-hairline-strong bg-inset px-3 text-center font-mono text-2xl text-ink"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") commit();
            if (event.key === "Escape") setTyping(false);
          }}
        />
      ) : (
        <button
          type="button"
          aria-label={`${t("train.enter", { what })} (${display})`}
          className="min-h-[56px] min-w-0 flex-1 rounded-lg px-1 text-center font-mono font-semibold text-2xl text-ink leading-tight [overflow-wrap:anywhere]"
          onClick={() => {
            setDraft(value === null ? "" : String(value));
            setTyping(true);
          }}
        >
          {display}
        </button>
      )}
      <Button
        variant="outline"
        className="size-[56px] shrink-0 font-mono text-2xl"
        aria-label={t("train.increase", { what })}
        {...holdable(1)}
      >
        +
      </Button>
    </div>
  );
}
