import { NotAssessed } from "@/components/design/not-assessed";
import type { components } from "@/generated/api/schema";
import { stepRoleLabel } from "@/lib/criteria";
import { formatDurationClock } from "@/lib/format";

type Schemas = components["schemas"];
type ResolvedStep = Schemas["ResolvedStepRead"];
type ResolvedTarget = Schemas["ResolvedTargetRead"];

/** The channel order a prescription is read in: power first, cadence last. */
const CHANNEL_ORDER: readonly Schemas["Channel"][] = ["power", "hr", "cadence"];

export interface ResolvedStepListProps {
  readonly steps: readonly ResolvedStep[];
}

/**
 * The flattened step list under a profile — what the bars actually are.
 *
 * Rendered from the API's `resolved_steps` rather than from the step tree,
 * for the reason F2 exists: each step's target is shown **both ways**, the
 * prescription (`88–93 % FTP`, which is what survives an FTP change) beside
 * the watts the athlete actually rides. Those watts were resolved against the
 * versions the session *pinned*, so this list is also the only honest way to
 * show absolute numbers for a planned session — nothing here can reach the
 * anchor currently in force.
 *
 * Repeats are already expanded on the wire, so the list and the bars above it
 * are the same things in the same order.
 */
export function ResolvedStepList({ steps }: ResolvedStepListProps) {
  if (steps.length === 0) {
    return null;
  }
  return (
    <ul className="flex flex-col">
      {steps.map((step) => (
        <li
          key={step.index}
          className="flex items-baseline justify-between gap-3 border-hairline border-b py-1.5 text-sm last:border-b-0"
        >
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-ink-secondary">
              {step.name ?? capitalise(stepRoleLabel(step.role))}
            </span>
            <StepTargets step={step} />
          </span>
          <span className="shrink-0 font-mono text-ink-muted text-xs">
            {step.duration_s !== null ? (
              formatDurationClock(step.duration_s)
            ) : step.distance_m !== null ? (
              `${step.distance_m} m`
            ) : (
              // The slot holds its position rather than collapsing (UI
              // convention 4), and says which of the two measures the step
              // is missing rather than drawing a bare dash.
              <NotAssessed reason="This step prescribes neither a duration nor a distance" />
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** One step's targets, prescribed and resolved. A ramp says both of its ends. */
function StepTargets({ step }: { step: ResolvedStep }) {
  const channels = CHANNEL_ORDER.filter((channel) =>
    step.start_targets.some((target) => target.channel === channel),
  );
  if (channels.length === 0) {
    return null;
  }
  return (
    <span className="flex flex-wrap gap-x-2.5 gap-y-0.5">
      {channels.map((channel) => {
        const start = step.start_targets.find((t) => t.channel === channel);
        const end = step.end_targets.find((t) => t.channel === channel);
        if (!start) {
          return null;
        }
        const ramped =
          step.is_ramp && end && end.prescribed !== start.prescribed;
        const resolved = resolvedText(start);
        const resolvedEnd = ramped && end ? resolvedText(end) : null;
        return (
          <span key={channel} className="font-mono text-2xs">
            <span className="text-ink-muted">
              {start.prescribed}
              {ramped && end ? ` → ${end.prescribed}` : ""}
            </span>
            {resolved ? (
              <span className="text-ink-faint">
                {" · "}
                {resolved}
                {resolvedEnd ? ` → ${resolvedEnd}` : ""}
              </span>
            ) : null}
          </span>
        );
      })}
    </span>
  );
}

/** `220–232 W`, or `null` when the target resolved against no anchor. */
function resolvedText(target: ResolvedTarget): string | null {
  if (target.resolved_low === null || target.resolved_high === null) {
    return null;
  }
  const low = roundTenth(target.resolved_low);
  const high = roundTenth(target.resolved_high);
  return low === high
    ? `${low} ${target.unit}`
    : `${low}–${high} ${target.unit}`;
}

function roundTenth(value: number): number {
  return Math.round(value * 10) / 10;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
