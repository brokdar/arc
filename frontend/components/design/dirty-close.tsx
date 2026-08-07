"use client";

import type { Dialog } from "@base-ui/react/dialog";
import { useState } from "react";

import { InlineConfirm } from "@/components/design/confirm";

/**
 * The guard that stands between an unsaved draft and a stray click.
 *
 * Base UI hands `onOpenChange` a `reason` — `outside-press`, `escape-key`,
 * `close-press` — and every one of them used to close a half-written session
 * without a word. The rule here is one rule, applied to all of them: **a dirty
 * dialog does not close, it asks**. Not a browser `confirm()` (it cannot be
 * styled and stacks a second modal on the first), and not a "click again to
 * confirm" on the ✕ alone, which would leave the outside-press path silent.
 *
 * A pristine dialog closes instantly, because there is nothing to protect and
 * a prompt would be furniture.
 */
export interface DirtyCloseOptions {
  /** Whether the surface holds edits that closing would discard. */
  readonly dirty: boolean;
  /** What actually closes the surface, once closing is allowed. */
  readonly onClose: () => void;
}

export interface DirtyClose {
  /** True while the discard prompt is up. */
  readonly confirming: boolean;
  /** Hand straight to a Base UI `Dialog.Root` / `Sheet`. */
  readonly onOpenChange: (
    open: boolean,
    eventDetails: Dialog.Root.ChangeEventDetails,
  ) => void;
  /** For a close control of the surface's own (a Cancel button). */
  readonly requestClose: () => void;
  /** Go through with it. */
  readonly discard: () => void;
  /** Think better of it. */
  readonly keepEditing: () => void;
}

export function useDirtyClose({
  dirty,
  onClose,
}: DirtyCloseOptions): DirtyClose {
  const [confirming, setConfirming] = useState(false);

  const requestClose = () => {
    if (dirty) {
      setConfirming(true);
      return;
    }
    onClose();
  };

  return {
    confirming,
    requestClose,
    onOpenChange: (open, eventDetails) => {
      if (open) {
        return;
      }
      if (dirty) {
        // Stop Base UI dismissing the popup behind our back: the surface is
        // controlled, but the escape-key handler is not ours.
        eventDetails.cancel();
        setConfirming(true);
        return;
      }
      onClose();
    },
    discard: () => {
      setConfirming(false);
      onClose();
    },
    keepEditing: () => setConfirming(false),
  };
}

export interface DiscardPromptProps {
  /** What is being discarded, in the sentence's own words. */
  readonly what: string;
  readonly onDiscard: () => void;
  readonly onKeepEditing: () => void;
  readonly className?: string;
}

/** The prompt `useDirtyClose` raises, phrased the same way everywhere. */
export function DiscardPrompt({
  what,
  onDiscard,
  onKeepEditing,
  className,
}: DiscardPromptProps) {
  return (
    <InlineConfirm
      question={`Discard ${what}?`}
      confirmLabel="Discard"
      cancelLabel="Keep editing"
      onConfirm={onDiscard}
      onCancel={onKeepEditing}
      className={className}
    />
  );
}
