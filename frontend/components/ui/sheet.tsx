"use client";

import { Dialog } from "@base-ui/react/dialog";
import type * as React from "react";

import { CloseIcon } from "@/components/icons";
import { cn } from "@/lib/utils";

/**
 * A side sheet: a Base UI dialog that flies in from the right edge.
 *
 * Base UI, not Radix — the primitives here take `render={...}` to change the
 * rendered element, never `asChild`. Everything is a thin styled pass-through
 * so the parts stay composable: `Sheet` / `SheetTrigger` / `SheetContent` with
 * `SheetTitle` and `SheetDescription` inside it (both required for the dialog
 * to be announced; hide one visually rather than dropping it).
 */
const Sheet = Dialog.Root;
const SheetTrigger = Dialog.Trigger;
const SheetClose = Dialog.Close;

function SheetContent({
  className,
  children,
  ...props
}: Dialog.Popup.Props & { children?: React.ReactNode }) {
  return (
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-50 min-h-dvh bg-black/55 transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
      <Dialog.Popup
        data-slot="sheet-content"
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-[min(30rem,100vw)] flex-col overflow-y-auto border-hairline border-l bg-panel shadow-2xl shadow-black/40 outline-none transition-transform duration-200 ease-out data-ending-style:translate-x-full data-starting-style:translate-x-full",
          className,
        )}
        {...props}
      >
        {children}
      </Dialog.Popup>
    </Dialog.Portal>
  );
}

function SheetTitle({ className, ...props }: Dialog.Title.Props) {
  return (
    <Dialog.Title
      className={cn(
        "font-semibold text-2xl text-ink tracking-[-0.02em]",
        className,
      )}
      {...props}
    />
  );
}

function SheetDescription({ className, ...props }: Dialog.Description.Props) {
  return (
    <Dialog.Description
      className={cn("text-ink-muted text-sm leading-relaxed", className)}
      {...props}
    />
  );
}

/** The ✕ in a sheet's top-right corner. */
function SheetCloseButton({ className, ...props }: Dialog.Close.Props) {
  return (
    <Dialog.Close
      aria-label="Close"
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-button text-ink-muted transition-colors hover:bg-raised hover:text-ink",
        className,
      )}
      {...props}
    >
      <CloseIcon />
    </Dialog.Close>
  );
}

export {
  Sheet,
  SheetClose,
  SheetCloseButton,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
};
