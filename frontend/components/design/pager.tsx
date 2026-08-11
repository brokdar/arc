import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";

/**
 * The heading of a paged band: what it holds, where in it you are, and the
 * two steps.
 *
 * Shared rather than copied — the inbox pages a queue and a log, the settings
 * page pages an anchor history, and three hand-rolled copies of
 * `Math.min(offset + items.length, total)` are three chances to get the last
 * page's range wrong.
 *
 * The buttons read "Newer" and "Older" but are *named* for what they page —
 * "Newer quarantine records", "Newer anchor versions". Two pagers on one page
 * whose controls are both called "Newer" are two controls a screen reader
 * cannot tell apart, and the visible word stays inside the spoken name
 * (WCAG 2.5.3), so nothing is said that is not also shown.
 */
export interface PagerProps {
  /** The band's heading, rendered as the section label. */
  readonly heading: string;
  /** What is being paged, plural, for the buttons' accessible names. */
  readonly subject: string;
  readonly offset: number;
  /** How many rows this page actually returned. */
  readonly onPage: number;
  readonly total: number;
  readonly pageSize: number;
  readonly onOffsetChange: (offset: number) => void;
  /** Rendered between the range and the buttons — a filter, usually. */
  readonly children?: React.ReactNode;
}

export function Pager({
  heading,
  subject,
  offset,
  onPage,
  total,
  pageSize,
  onOffsetChange,
  children,
}: PagerProps) {
  const last = Math.min(offset + onPage, total);
  return (
    <div className="flex flex-wrap items-baseline gap-2.5">
      <SectionLabel level={2}>{heading}</SectionLabel>
      <span className="font-mono text-2xs text-ink-faint">
        {total === 0 ? "" : `${offset + 1}–${last} of ${total}`}
      </span>
      <span className="ml-auto flex items-center gap-1.5">
        {children}
        <Button
          size="xs"
          variant="secondary"
          aria-label={`Newer ${subject}`}
          disabled={offset === 0}
          onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}
        >
          Newer
        </Button>
        <Button
          size="xs"
          variant="secondary"
          aria-label={`Older ${subject}`}
          disabled={last >= total}
          onClick={() => onOffsetChange(offset + pageSize)}
        >
          Older
        </Button>
      </span>
    </div>
  );
}
