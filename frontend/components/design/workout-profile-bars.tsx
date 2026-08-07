import { cn } from "@/lib/utils";
import {
  profileBars,
  type WorkoutStructure,
  ZONE_COLORS,
} from "@/lib/workout-profile";

/**
 * The horizontal bar profile of a structured ride.
 *
 * Two sizes, both from the mockup: `card` is the 24px strip on a calendar
 * card, `detail` the 96px plot in a well on the session sheet. Purely a
 * function of the step tree — `profileBars` does the arithmetic, this draws
 * flex-weighted `<div>`s, and there is no canvas, chart library or measurement
 * pass anywhere in it.
 *
 * Renders nothing at all for a strength prescription or an empty tree: a card
 * with no profile should lose the row, not gain an empty box.
 */
export interface WorkoutProfileBarsProps {
  readonly structure: WorkoutStructure | null | undefined;
  readonly size?: "card" | "detail";
  readonly className?: string;
}

export function WorkoutProfileBars({
  structure,
  size = "card",
  className,
}: WorkoutProfileBarsProps) {
  const bars = profileBars(structure);
  if (bars.length === 0) {
    return null;
  }

  const detail = size === "detail";
  return (
    <div
      data-slot="workout-profile"
      aria-hidden
      className={cn(
        "flex items-end",
        detail
          ? "h-24 gap-[2px] rounded-button border border-hairline bg-inset p-2.5"
          : "h-6 gap-px",
        className,
      )}
    >
      {bars.map((bar, index) => (
        <div
          // Bars have no identity of their own — they are positions in a
          // flattened tree, and the tree is re-derived whenever it changes.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          key={index}
          className={detail ? "rounded-t-[2px]" : "rounded-[1px]"}
          style={{
            flexGrow: bar.weight,
            flexBasis: 0,
            minWidth: 1,
            height: `${(bar.height * 100).toFixed(1)}%`,
            backgroundColor: ZONE_COLORS[bar.zone],
          }}
        />
      ))}
    </div>
  );
}
