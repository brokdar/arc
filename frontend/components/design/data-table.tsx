import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The two cells the app's log tables are made of.
 *
 * Shared by the ingest log and the anchor history, which are the same object
 * twice: a scrolling record of things that happened, newest first, read by
 * running an eye down one column. The header is the `SectionLabel` treatment
 * (10px, uppercase, .09em) rather than a bold row, so a table of rows does not
 * out-shout the panel headings around it.
 */
export function Th({
  className,
  children,
}: {
  readonly className?: string;
  readonly children: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  className,
  children,
}: {
  readonly className?: string;
  readonly children: React.ReactNode;
}) {
  return <td className={cn("px-3.5 py-2 align-top", className)}>{children}</td>;
}
