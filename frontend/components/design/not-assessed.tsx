import { cn } from "@/lib/utils";

/**
 * The placeholder that holds a metric's slot when there is no number for it.
 *
 * Missing data means **not assessed**, never zero (spec v2 §5): a week whose
 * load could not be predicted is not a light week, and an unscored axis is not
 * a failed one. So the slot keeps its position (UI convention 4) and renders a
 * dash that carries *why* — on hover through `title`, and to a screen reader
 * through the accessible name, because a reason only sighted mouse users can
 * reach is not a reason the application gave.
 *
 * It is a component rather than a literal `—` because WP-7's scoring axes
 * return `not_assessed(reason)` as a first-class result: the calendar's
 * unpredictable loads and an unscored axis should be the same affordance, not
 * two conventions that drifted.
 */
export interface NotAssessedProps {
  /** Why there is no value: "No FTP anchor pinned", "No power target". */
  readonly reason: string;
  /** The mark drawn in the slot. An em dash unless a caller has a better one. */
  readonly symbol?: string;
  readonly className?: string;
}

export function NotAssessed({
  reason,
  symbol = "—",
  className,
}: NotAssessedProps) {
  return (
    <span
      data-slot="not-assessed"
      role="img"
      aria-label={`Not assessed: ${reason}`}
      title={reason}
      className={cn(
        "cursor-help font-mono text-ink-faint underline decoration-dotted decoration-hairline-strong underline-offset-[3px]",
        className,
      )}
    >
      {symbol}
    </span>
  );
}
