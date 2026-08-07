import { type Purpose, purposeTone } from "@/lib/purpose";
import { cn } from "@/lib/utils";

/**
 * The small uppercase chip that says why a session exists.
 *
 * Colours come from `PURPOSE_TONES`, which covers the whole backend enum — a
 * purpose the vocabulary gains cannot reach this component without a colour.
 */
export interface PurposeBadgeProps {
  readonly purpose: Purpose;
  readonly className?: string;
  /** Slightly roomier, for a page or sheet header rather than a card. */
  readonly size?: "sm" | "md";
}

export function PurposeBadge({
  purpose,
  className,
  size = "sm",
}: PurposeBadgeProps) {
  const tone = purposeTone(purpose);
  return (
    <span
      data-slot="purpose-badge"
      className={cn(
        "inline-flex shrink-0 items-center rounded-badge font-semibold text-2xs uppercase tracking-[0.04em]",
        size === "sm" ? "px-1.5 py-0.5" : "px-[7px] py-[2.5px]",
        className,
      )}
      style={{ color: tone.fg, backgroundColor: tone.tint }}
    >
      {tone.label}
    </span>
  );
}
