import { formatDurationClock } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  type ProfileBar,
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
 *
 * The `detail` plot carries the mockup's time axis beneath it, so the athlete
 * can read *when* the third interval starts rather than only that there is a
 * third interval. It appears only when every flattened step states a duration
 * — a tree with an open-ended step has no total, and an axis running to a
 * total the prescription never gave would be an invention.
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
  const ticks = detail ? timeAxis(bars) : [];

  const plot = (
    <div
      data-slot="workout-profile"
      aria-hidden
      className={cn(
        "flex items-end",
        detail
          ? "h-24 gap-[2px] rounded-button border border-hairline-faint bg-inset p-2.5"
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

  if (ticks.length === 0) {
    return plot;
  }

  return (
    <div className="flex flex-col gap-1">
      {plot}
      <div
        data-slot="workout-profile-axis"
        aria-hidden
        className="flex justify-between px-1 font-mono text-2xs text-ink-faint"
      >
        {ticks.map((seconds, index) => (
          // Ticks are positions on an axis, and a very short workout can round
          // two of them to the same second.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          <span key={index}>{formatDurationClock(seconds)}</span>
        ))}
      </div>
    </div>
  );
}

/** How many labels the axis carries, `0:00` and the total included. */
const AXIS_TICKS = 5;

/**
 * Evenly spaced elapsed times across the plot: `0:00 … 17:11 … 1:08:42`.
 *
 * Even *fractions* of the total rather than round clock intervals, because the
 * row is laid out with `justify-between` and nothing else positions the labels
 * — quarters land where they say they land, whereas ten-minute marks would sit
 * wherever flex put them and quietly lie about the third interval's start.
 *
 * Empty when any step is open-ended, so the caller renders no axis at all.
 */
function timeAxis(bars: readonly ProfileBar[]): number[] {
  let total = 0;
  for (const bar of bars) {
    if (bar.durationS === null) {
      return [];
    }
    total += bar.durationS;
  }
  if (total <= 0) {
    return [];
  }
  return Array.from({ length: AXIS_TICKS }, (_, index) =>
    Math.round((total * index) / (AXIS_TICKS - 1)),
  );
}
