"use client";

import { NotAssessed } from "@/components/design/not-assessed";
import type { Metric, MetricExplanation } from "@/lib/metrics";
import { resolve } from "@/lib/metrics";
import { cn } from "@/lib/utils";

/**
 * How an explanation reads as one string.
 *
 * Formula, then the inputs it was fed, then what it had to assume, then where
 * the method comes from. Flattened rather than laid out because it is
 * delivered through `title` and `aria-label`, which take text: a tooltip only
 * a sighted mouse user can reach is not an explanation the application gave —
 * the same reasoning `NotAssessed` and `ProvenanceMark` are built on.
 */
export function explanationText(explanation: MetricExplanation): string {
  const inputs = Object.entries(explanation.inputs).map(
    ([name, rendered]) => `${name}: ${rendered}`,
  );
  return [
    explanation.formula,
    inputs.length > 0 ? `Inputs — ${inputs.join("; ")}` : null,
    explanation.assumptions.length > 0
      ? `Assumes — ${explanation.assumptions.join("; ")}`
      : null,
    explanation.citation,
  ]
    .filter((line): line is string => line !== null)
    .join("\n");
}

export interface ExplainedProps {
  readonly explanation: MetricExplanation | null;
  readonly children: React.ReactNode;
  readonly className?: string;
}

/**
 * One affordance, reused by every computed number on the page.
 *
 * Every value the backend produces carries its own `MetricExplanation`
 * (A3.7): the formula, the inputs *as they were resolved* — an anchor names
 * the version's value, provenance and effective date, never "your current
 * FTP" — and the assumptions the arithmetic had to make. Rendering that
 * through one component rather than per stat is what stops the header from
 * explaining NP one way and load another.
 *
 * A number with no explanation renders plainly. That is not a gap to fill with
 * invented copy: it means nothing claimed to explain it.
 */
export function Explained({
  explanation,
  children,
  className,
}: ExplainedProps) {
  if (explanation === null) {
    return <span className={className}>{children}</span>;
  }
  const text = explanationText(explanation);
  return (
    <span
      data-slot="explained"
      // `note` rather than a bare span: the explanation reaches assistive
      // technology through `aria-label`, which a role-less element does not
      // support, and "parenthetic or ancillary content" is exactly what an
      // explanation attached to a number is. The same reasoning `NotAssessed`
      // and `ProvenanceMark` follow — a tooltip only a sighted mouse user can
      // reach is not an explanation the application gave.
      role="note"
      title={text}
      aria-label={text}
      className={cn(
        "cursor-help decoration-dotted decoration-hairline-strong underline-offset-[5px] hover:underline",
        className,
      )}
    >
      {children}
    </span>
  );
}

export interface MetricValueProps {
  /**
   * The slot to render. Nullable because a **block** of the artefact can be
   * absent as well as a number inside it: an artefact written before a metric
   * existed carries no key for it, so the generated type makes the block
   * optional. `resolve` already answers for that case with a reason, and this
   * accepting it is what keeps the header free of `?? {}` at every slot.
   */
  readonly metric: Metric | null | undefined;
  /** How the number is written once it exists. */
  readonly format?: (value: number) => string;
  readonly className?: string;
}

/**
 * A metric slot: the number under its explanation, or the reason it has none.
 *
 * The single branch every metric on this page goes through. The slot is the
 * same width and in the same place either way (UI convention 4), because
 * position is how a returning eye finds a number.
 */
export function MetricValue({
  metric,
  format = (value) => value.toFixed(0),
  className,
}: MetricValueProps) {
  const resolved = resolve(metric);
  if (resolved.kind === "absent") {
    return <NotAssessed reason={resolved.reason} className={className} />;
  }
  return (
    <Explained explanation={resolved.explanation} className={className}>
      {format(resolved.value)}
    </Explained>
  );
}
