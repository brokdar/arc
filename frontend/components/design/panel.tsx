import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The card container the whole app is built from.
 *
 * Three tones, all from the mockup: `panel` for a sidebar module or a
 * standalone block, `card` for something clickable in a list or grid, `inset`
 * for a well that sits *inside* another surface (a chart background, a
 * forecast tile).
 *
 * The hairline differs by tone because the mockup's does: an outer container
 * is drawn at .07 (`hairline-card`) and a well at .05 (`hairline-faint`), so a
 * chart never out-draws the panel holding it.
 */
export type PanelTone = "panel" | "card" | "inset";

const TONE_CLASSES: Record<PanelTone, string> = {
  panel: "rounded-panel border-hairline-card bg-panel",
  card: "rounded-card border-hairline-card bg-card",
  inset: "rounded-button border-hairline-faint bg-inset",
};

export interface PanelProps extends React.ComponentProps<"div"> {
  readonly tone?: PanelTone;
}

export function Panel({ tone = "panel", className, ...props }: PanelProps) {
  return (
    <div
      data-slot="panel"
      className={cn("border", TONE_CLASSES[tone], className)}
      {...props}
    />
  );
}
