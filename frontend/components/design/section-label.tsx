import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The uppercase micro-heading that names a block: WORKOUT PROFILE, TARGETS,
 * SUCCESS CRITERIA.
 *
 * Renders as a `<h*>` when given a `level`, so a panel that really is a
 * section of the page contributes to the heading outline instead of being a
 * styled `<div>`; the default is a plain span for the many places where the
 * label is a caption rather than a heading.
 */
export interface SectionLabelProps extends React.HTMLAttributes<HTMLElement> {
  readonly level?: 2 | 3 | 4;
}

export function SectionLabel({
  level,
  className,
  ...props
}: SectionLabelProps) {
  const Tag: React.ElementType = level ? `h${level}` : "span";
  return (
    <Tag
      data-slot="section-label"
      className={cn(
        "font-semibold text-2xs text-ink-faint uppercase tracking-[0.09em]",
        className,
      )}
      {...props}
    />
  );
}
