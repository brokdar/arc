import { Fragment } from "react";

import type { components } from "@/generated/api/schema";
import { formatAnchorValue, formatDayMonthYear } from "@/lib/format";
import { anchorLabel } from "@/lib/targets";
import { cn } from "@/lib/utils";

type Schemas = components["schemas"];
type PinnedAnchor = Schemas["PinnedAnchorRead"];
type Provenance = Schemas["Provenance"];

/**
 * How each provenance reads. `tested` is the only one that is a measurement;
 * the other three are marked as claims, which is what makes an estimate read
 * as an estimate.
 */
const PROVENANCE_MARKS: Readonly<
  Record<Provenance, { label: string; note: string }>
> = {
  tested: {
    label: "tested",
    note: "Measured in a test protocol — the strongest anchor there is.",
  },
  estimated: {
    label: "estimated",
    note: "An estimate, not a test. Every number resolved from it is only as good as the estimate.",
  },
  athlete_reported: {
    label: "athlete-reported",
    note: "Reported by the athlete rather than measured here.",
  },
  assumed: {
    label: "assumed",
    note: "Assumed for want of anything better. Treat every number resolved from it as provisional.",
  },
};

export interface ProvenanceMarkProps {
  readonly provenance: Provenance;
  readonly className?: string;
}

/**
 * One word saying how an anchor value was arrived at, and what that costs.
 *
 * Given the `NotAssessed` treatment on purpose (`role="img"` + `aria-label` +
 * `title`): the note is the whole reason the mark exists, and a note only a
 * sighted mouse user can reach is not a note the application gave. The word
 * alone would read as a label; the accessible name says what it means.
 */
export function ProvenanceMark({ provenance, className }: ProvenanceMarkProps) {
  const mark = PROVENANCE_MARKS[provenance];
  const tested = provenance === "tested";
  return (
    <span
      data-slot="provenance-mark"
      data-provenance={provenance}
      data-untested={tested ? undefined : "true"}
      role="img"
      aria-label={`${mark.label}: ${mark.note}`}
      title={mark.note}
      className={cn(
        tested
          ? "cursor-help text-status-completed"
          : "cursor-help text-status-under underline decoration-dotted decoration-status-under/60 underline-offset-[3px]",
        className,
      )}
    >
      {mark.label}
    </span>
  );
}

export interface AnchorProvenanceProps {
  readonly anchors: readonly PinnedAnchor[];
  readonly className?: string;
}

/**
 * The one line that says what the numbers beside it were resolved against.
 *
 * `SessionIntent.pinned_anchor_versions` is the product's most distinctive
 * invariant and it is worth nothing invisible: an FTP of 250 W that was
 * *estimated* is a different claim from one that was *tested*, and every watt
 * on the screen inherits whichever it is. So the provenance is rendered as its
 * own mark, and the three non-tested kinds are marked differently from
 * `tested` rather than merely being spelled differently.
 *
 * Shared by the calendar sheet and the Today view, because both resolve the
 * same percentages against the same pins and neither may show a resolved watt
 * without saying whose watt it is.
 */
export function AnchorProvenance({
  anchors,
  className,
}: AnchorProvenanceProps) {
  if (anchors.length === 0) {
    return null;
  }
  return (
    <p
      data-slot="anchor-provenance"
      className={cn(
        "flex flex-wrap items-baseline gap-x-1.5 text-2xs text-ink-faint",
        className,
      )}
    >
      <span>Resolved against</span>
      {anchors.map((anchor, index) => (
        <Fragment key={anchor.anchor_version_id}>
          {index > 0 ? <span aria-hidden>·</span> : null}
          <span className="font-mono text-ink-muted">
            {anchorLabel(anchor.anchor_type)} {formatAnchorValue(anchor.value)}{" "}
            {anchor.unit}
          </span>
          <span aria-hidden>·</span>
          <ProvenanceMark provenance={anchor.provenance} />
          <span aria-hidden>·</span>
          <span className="font-mono">
            effective {formatDayMonthYear(anchor.effective_date)}
          </span>
        </Fragment>
      ))}
    </p>
  );
}
