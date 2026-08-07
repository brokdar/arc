import { type SessionStatus, STATUS_TONES } from "@/lib/purpose";
import { cn } from "@/lib/utils";

/**
 * The 6px dot that carries a session's status.
 *
 * It is the only thing on a calendar card that says how the session *went*, so
 * it is never decorative: it renders as a labelled `img` and the colour is a
 * second channel on top of a name a screen reader can read.
 *
 * `outline` is the mockup's treatment for a session that is still ahead of the
 * athlete on today's card — an unfilled ring rather than a filled dot.
 */
export interface StatusDotProps {
  readonly status: SessionStatus;
  readonly outline?: boolean;
  readonly className?: string;
}

export function StatusDot({ status, outline, className }: StatusDotProps) {
  const tone = STATUS_TONES[status];
  return (
    <span
      role="img"
      aria-label={tone.label}
      className={cn("size-1.5 shrink-0 rounded-full", className)}
      style={
        outline
          ? { border: `1.5px solid ${tone.color}` }
          : { backgroundColor: tone.color }
      }
    />
  );
}
