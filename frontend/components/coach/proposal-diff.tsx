"use client";

import { SectionLabel } from "@/components/design/section-label";
import { DISCIPLINE_LABELS } from "@/lib/activity";
import {
  CHANGE_KIND_LABELS,
  CHANGE_KIND_TONES,
  changeFields,
  type FieldDiff,
  isMonoField,
  type ProposalChangeDiff,
} from "@/lib/proposals";
import { cn } from "@/lib/utils";

/** What a value that is not there renders as, on either side of an arrow. */
const ABSENT = "—";

/**
 * What one proposal would do to the plan, change by change.
 *
 * The whole point of this view is that a proposal is *specific*: "make
 * Thursday easier" is a rationale, and what the athlete accepts is four
 * fields on one session. So every change lists its fields with both sides
 * visible, and the ones that actually differ are the ones drawn in full ink —
 * a diff that showed nine identical-looking rows would hide the one that
 * matters inside eight that do not, which is how an athlete accepts a change
 * they did not read.
 *
 * Unchanged rows are kept rather than dropped, dimmed, because a change is
 * read against the session it is changing: "Thursday, threshold, 90 minutes"
 * is the context that makes "intent: hold 30 min at 95 % → 20 min at 88 %"
 * mean something. They are marked `data-changed="false"` so a test can assert
 * the distinction is real rather than a shade nobody can measure.
 */
export function ProposalDiff({
  changes,
}: {
  changes: readonly ProposalChangeDiff[];
}) {
  if (changes.length === 0) {
    return (
      <p className="text-ink-muted text-sm">
        This proposal computed no diff — there is nothing here to apply.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {changes.map((change, index) => (
        <li
          // A change is a position in the proposal's list, not an entity: two
          // changes can name the same planned session (one moved, one
          // revised) and a `create` names none at all.
          // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
          key={index}
        >
          <ChangeCard change={change} />
        </li>
      ))}
    </ul>
  );
}

function ChangeCard({ change }: { change: ProposalChangeDiff }) {
  const rows = changeFields(change);
  const changed = rows.filter((row) => row.changed).length;

  return (
    <div
      data-testid="proposal-change"
      data-kind={change.kind}
      className="flex flex-col gap-2 rounded-button border border-hairline-faint bg-inset px-3.5 py-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span
          className={cn(
            "rounded-badge border border-hairline px-1.5 py-0.5 font-medium text-2xs",
            CHANGE_KIND_TONES[change.kind],
          )}
        >
          {CHANGE_KIND_LABELS[change.kind]}
        </span>
        <span className="font-mono text-ink-secondary text-sm">
          {change.date}
        </span>
        <span className="text-ink-muted text-sm">
          {DISCIPLINE_LABELS[change.discipline]}
        </span>
        <span className="ml-auto flex items-center gap-2.5 font-mono text-2xs text-ink-faint">
          {/* The concurrency token, shown rather than hidden: it is what the
              accept re-checks, and it is the reason an accept can come back
              409 on a proposal that was fine when it was written. An athlete
              who can see "intent v3" has something to compare against the
              version on the session itself. */}
          {change.expected_intent_version !== null ? (
            <span>intent v{change.expected_intent_version}</span>
          ) : null}
          <span>
            {changed === 0
              ? "no field differs"
              : `${changed} field${changed === 1 ? "" : "s"} differ${changed === 1 ? "s" : ""}`}
          </span>
        </span>
      </div>

      <dl className="flex flex-col">
        {rows.map((row) => (
          <FieldRow key={row.key} row={row} />
        ))}
      </dl>
    </div>
  );
}

function FieldRow({ row }: { row: FieldDiff }) {
  const mono = isMonoField(row.key);
  return (
    <div
      data-testid="diff-field"
      data-field={row.key}
      data-changed={row.changed}
      className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 border-hairline-faint border-b py-1.5 last:border-b-0"
    >
      <dt className="w-[104px] shrink-0">
        <SectionLabel className={row.changed ? "text-ink-muted" : undefined}>
          {row.label}
        </SectionLabel>
      </dt>
      <dd className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span
          className={cn(
            "min-w-0 break-words text-sm",
            mono && "font-mono",
            row.changed ? "text-ink-faint" : "text-ink-muted",
            // Struck through only when there is something to strike: a
            // `create` has no before, and a line drawn through an em dash
            // reads as a value that was taken away rather than one that was
            // never there.
            row.changed && row.before !== null && "line-through",
          )}
        >
          {row.before ?? ABSENT}
        </span>
        <span aria-hidden className="text-ink-faint text-xs">
          →
        </span>
        <span
          className={cn(
            "min-w-0 break-words text-sm",
            mono && "font-mono",
            row.changed ? "font-medium text-ink" : "text-ink-muted",
          )}
        >
          {row.after ?? ABSENT}
        </span>
      </dd>
    </div>
  );
}
