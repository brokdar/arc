"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Two ways of asking "are you sure", and one reason they live together.
 *
 * Neither uses the browser's `confirm()`: it is a modal the design system does
 * not own, it cannot be styled for a dark canvas, and in a dialog it stacks a
 * second modal on top of the first. Both of these render inside the surface
 * they are protecting, where the thing at stake is still visible.
 */

export interface InlineConfirmProps {
  /** What is about to happen, phrased as a question. */
  readonly question: string;
  /** The button that goes through with it. */
  readonly confirmLabel: string;
  /** The button that does not. Focused first — the safe way out. */
  readonly cancelLabel: string;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
  /**
   * Whether the thing being confirmed is already in flight.
   *
   * Only the confirming button is disabled by it. The way out stays open —
   * disabling both would trap the athlete inside the question for as long as
   * the request takes — but a second confirm is not a second answer, it is the
   * same answer twice, and the second one loses: the record has already been
   * resolved by the first, so its 409 arrives *after* the success and paints a
   * refusal over work that went through.
   */
  readonly disabled?: boolean;
  readonly className?: string;
}

/**
 * A strip that asks before something irreversible happens.
 *
 * `role="alertdialog"` so a screen reader is told the question rather than
 * being left to discover two new buttons, and the *cancel* control takes
 * initial focus: the destructive answer should never be one Return away.
 */
export function InlineConfirm({
  question,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
  disabled = false,
  className,
}: InlineConfirmProps) {
  return (
    <div
      role="alertdialog"
      aria-label={question}
      data-slot="inline-confirm"
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5",
        className,
      )}
    >
      <span className="mr-auto text-ink-secondary text-sm">{question}</span>
      {/* The safe answer takes focus, so the destructive one is never one
          Return away from whatever the athlete was doing. */}
      <Button
        type="button"
        size="xs"
        variant="secondary"
        autoFocus
        onClick={onCancel}
      >
        {cancelLabel}
      </Button>
      <Button
        type="button"
        size="xs"
        variant="destructive"
        disabled={disabled}
        onClick={onConfirm}
      >
        {confirmLabel}
      </Button>
    </div>
  );
}

export interface ConfirmButtonProps {
  /** The button before it is armed. */
  readonly label: string;
  /** The question the armed state asks. */
  readonly question: string;
  /** The armed button that goes through with it. */
  readonly confirmLabel: string;
  readonly onConfirm: () => void;
  readonly disabled?: boolean;
  readonly className?: string;
}

/**
 * A destructive button that takes two clicks, in one slot.
 *
 * The armed state replaces the button rather than opening anything, so the
 * layout around it does not move and the athlete's next click lands where they
 * are already looking. Disarming is always available and is what a click
 * anywhere else effectively does, since nothing has happened yet.
 */
export function ConfirmButton({
  label,
  question,
  confirmLabel,
  onConfirm,
  disabled = false,
  className,
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);

  if (!armed) {
    return (
      <Button
        type="button"
        variant="destructive"
        disabled={disabled}
        className={className}
        onClick={() => setArmed(true)}
      >
        {label}
      </Button>
    );
  }

  return (
    <span
      data-slot="confirm-button"
      className={cn("flex items-center gap-1.5", className)}
    >
      <span className="text-ink-muted text-xs">{question}</span>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-ink-muted"
        onClick={() => setArmed(false)}
      >
        Keep
      </Button>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        disabled={disabled}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </Button>
    </span>
  );
}
