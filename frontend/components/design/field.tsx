import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A labelled control.
 *
 * The forms in this application are dense grids of small inputs, and the
 * label above each one is the only thing that says what it is. Rather than
 * repeat a `<label htmlFor>` / wrapper pair a hundred times, `Field` owns the
 * pairing: give it an `id` and it labels whatever it wraps.
 *
 * `hint` is for a unit or a format ("mm:ss", "W"), not for help text — it
 * renders beside the label in the faint ink, where the eye reads it as part of
 * the field's name. It is *inside* the `<label>`, so it is part of the
 * accessible name too: a test reaches a hinted field with
 * `getByLabelText(/Duration/)`, never an exact string.
 */
export interface FieldProps extends React.ComponentProps<"div"> {
  readonly label: React.ReactNode;
  readonly htmlFor: string;
  readonly hint?: React.ReactNode;
}

export function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
  ...props
}: FieldProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)} {...props}>
      <label
        htmlFor={htmlFor}
        className="flex items-baseline gap-1.5 text-ink-muted text-xs"
      >
        {label}
        {hint ? <span className="text-ink-faint text-2xs">{hint}</span> : null}
      </label>
      {children}
    </div>
  );
}

/** A row of fields that wraps: the builder's `duration · role · name` bands. */
export function FieldRow({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-wrap items-end gap-2.5", className)}
      {...props}
    />
  );
}
