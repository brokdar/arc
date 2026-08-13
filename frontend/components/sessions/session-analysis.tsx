"use client";

import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { Explained } from "@/components/design/metric-explanation";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { MetricHeader } from "@/components/sessions/metric-header";
import type { PlannedBand } from "@/components/sessions/stream-charts";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { formatDurationClock } from "@/lib/format";
import {
  type DetectedInterval,
  pinOf,
  type SessionMetrics,
  type StrengthMetrics,
} from "@/lib/metrics";

/**
 * The charts are loaded in the browser only.
 *
 * uPlot reads `document`, `window` and `matchMedia` when its module is
 * evaluated, so importing it statically makes the *server* render of this page
 * throw before anything is sent — and a client component is still prerendered
 * on the server. Nothing is lost by skipping that prerender: a canvas has no
 * server-rendered form, and the page around it renders either way.
 */
const StreamCharts = dynamic(
  () =>
    import("@/components/sessions/stream-charts").then(
      (module) => module.StreamCharts,
    ),
  { ssr: false },
);

export interface SessionAnalysisProps {
  readonly sessionId: string;
  readonly metrics: SessionMetrics | null;
  /** Whether the session came from a device file at all. */
  readonly hasRecording: boolean;
  /**
   * Resolved planned step bands for the power chart.
   *
   * A component capability rather than an API field until WP-6: an
   * alignment describes a *match*, and matches do not exist yet. Passing it a
   * mock is how the overlay is tested today, so WP-6 wires data rather than
   * building a component.
   */
  readonly plannedBands?: readonly PlannedBand[];
}

/**
 * The analysis half of a session page: header metrics, charts, intervals.
 *
 * Three shapes, and which one renders is a fact about the session rather than
 * a loading state:
 *
 * * **no artefact** — nothing has been computed. The page says so and offers
 *   the action that fixes it, because an empty state that names no remedy is
 *   a dead end (UI convention 3).
 * * **no recording** — a session typed in at the gym. It gets the strength
 *   card (volume load and its coverage, sets completed) and no charts, since
 *   there are no per-second samples to plot.
 * * **a ride** — the header row, the stacked streams and the intervals table.
 *
 * The WP-7 execution panel and the WP-8 coach panel belong to the right-hand
 * column of the mockup's layout and are deliberately **absent**, not stubbed:
 * a placeholder for a scoring axis would be a claim about a session nothing
 * has scored.
 */
export function SessionAnalysis({
  sessionId,
  metrics,
  hasRecording,
  plannedBands,
}: SessionAnalysisProps) {
  // Unconditional, and gated by `enabled`: the early return below would
  // otherwise make this hook run on some renders and not others.
  const { data: streams } = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}/streams",
    { params: { path: { session_id: sessionId } } },
    { enabled: hasRecording && metrics !== null },
  );

  if (metrics === null) {
    return <NotComputed sessionId={sessionId} />;
  }

  const ftp = pinOf(metrics, "ftp");

  return (
    <div className="flex flex-col gap-5">
      <MetricHeader metrics={metrics} />

      {hasRecording && streams ? (
        <StreamCharts
          streams={streams}
          ftpWatts={ftp?.value}
          plannedBands={plannedBands}
        />
      ) : null}

      {hasRecording ? (
        <IntervalsTable intervals={metrics.intervals} />
      ) : (
        <StrengthCard strength={metrics.strength} />
      )}

      <Provenance metrics={metrics} sessionId={sessionId} />
    </div>
  );
}

/**
 * What the page says when a session has no metric artefact.
 *
 * A real state, not a loading one: a session ingested before WP-5, or one
 * whose metric run failed after the file was safely stored. It names
 * the remedy and puts the control beside it.
 */
function NotComputed({ sessionId }: { sessionId: string }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Analysis</SectionLabel>
      <Panel className="flex flex-col items-start gap-2.5 px-5 py-4">
        <p className="max-w-[62ch] text-ink-muted text-base">
          Metrics have not been computed for this session yet. Computing them
          reads the stored stream and the anchors in force now, and writes a new
          version — nothing already recorded is changed.
        </p>
        <RecomputeButton sessionId={sessionId} label="Compute metrics" />
      </Panel>
    </section>
  );
}

/**
 * The recompute action, and what it says about the version it wrote.
 *
 * Recomputation **appends**: version n+1 supersedes n and the old numbers stay
 * readable (invariant 1). The button says so, because "recompute" reads like
 * "overwrite" to anyone who has used another training platform.
 */
function RecomputeButton({
  sessionId,
  label,
}: {
  sessionId: string;
  label: string;
}) {
  const queryClient = useQueryClient();
  const detailKey = $api.queryOptions("get", "/api/v1/sessions/{session_id}", {
    params: { path: { session_id: sessionId } },
  }).queryKey;

  const recompute = $api.useMutation(
    "post",
    "/api/v1/sessions/{session_id}/metrics/recompute",
    {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: detailKey });
      },
    },
  );
  const problems = apiErrorMessages(recompute.error);

  return (
    <div className="flex flex-col items-start gap-1.5">
      <Button
        size="sm"
        variant="secondary"
        disabled={recompute.isPending}
        onClick={() =>
          recompute.mutate({
            params: { path: { session_id: sessionId } },
            body: { reason: "recomputed from the session page" },
          })
        }
      >
        {recompute.isPending ? "Computing…" : label}
      </Button>
      {recompute.isSuccess ? (
        <p role="status" className="text-sm text-status-completed">
          Wrote version {recompute.data.version}. The previous version is still
          readable.
        </p>
      ) : null}
      {problems.length > 0 ? (
        <ul
          role="alert"
          className="flex flex-col gap-1 text-destructive text-sm"
        >
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The detected intervals, as a table.
 *
 * Detection is deterministic from the stream alone, so this is a
 * description of the ride rather than a judgement of it: there is no adherence
 * column — that is WP-7's, against a plan this session may not even have — and
 * no device laps, which WP-4 never persisted.
 */
export function IntervalsTable({
  intervals,
}: {
  intervals: readonly DetectedInterval[];
}) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Intervals</SectionLabel>
      {intervals.length === 0 ? (
        <Panel className="px-5 py-4 text-ink-muted text-base">
          No work intervals were detected: nothing in this ride crossed the
          threshold for long enough to be an effort rather than a hill.
        </Panel>
      ) : (
        <Panel className="overflow-hidden">
          <table className="w-full border-collapse text-base">
            <thead>
              <tr className="border-hairline border-b text-left">
                {["#", "Segment", "Time", "Avg W", "Max W", "Avg HR"].map(
                  (heading) => (
                    <th
                      key={heading}
                      scope="col"
                      className="px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {intervals.map((interval, index) => (
                <tr
                  key={`${interval.start_index}-${interval.end_index}`}
                  className="border-hairline-faint border-b last:border-b-0"
                >
                  <td className="px-3.5 py-2 font-mono text-ink-faint text-sm">
                    {index + 1}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-ink-secondary text-sm">
                    {formatDurationClock(interval.start_index)} –{" "}
                    {formatDurationClock(interval.end_index)}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-ink text-sm">
                    {formatDurationClock(interval.duration_s)}
                  </td>
                  <Cell value={interval.average_power} absent="No power" />
                  <Cell value={interval.max_power} absent="No power" />
                  <Cell value={interval.average_hr} absent="No heart rate" />
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </section>
  );
}

function Cell({ value, absent }: { value: number | null; absent: string }) {
  return (
    <td className="px-3.5 py-2 font-mono text-ink text-sm">
      {value === null ? (
        <NotAssessed reason={`${absent} was recorded inside this interval`} />
      ) : (
        value.toFixed(0)
      )}
    </td>
  );
}

/**
 * What a strength session moved.
 *
 * Kilograms, and labelled as kilograms: volume load is on a different axis
 * from training load (v2 §5.4), so it is never rendered in the same column and
 * never totalled with one. Coverage travels with it, because a session whose
 * bodyweight work is uncounted has a smaller number for a reason.
 *
 * Seconds are a third axis and get their own figure when something was held:
 * a hold has no reps to multiply by kilograms, so folding its work into the
 * volume would be the `reps: 1` lie in another place. The counts are in
 * **working sets** (`app.domain.metrics.PerformedSet`), so three per-side rows
 * read as six — which is why the note says so rather than promising a count of
 * the rows the athlete typed.
 */
export function StrengthCard({ strength }: { strength: StrengthMetrics }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Strength</SectionLabel>
      <Panel className="flex flex-wrap gap-x-8 gap-y-3.5 px-5 py-4">
        {strength.not_assessed ? (
          <NotAssessed reason={strength.not_assessed} />
        ) : (
          <>
            <Figure
              label="Volume load"
              unit="kg"
              value={
                strength.volume_load_kg === null ||
                strength.volume_load_kg === undefined ? (
                  <NotAssessed reason="No set in this session was logged in kilograms" />
                ) : (
                  <Explained explanation={strength.explanation ?? null}>
                    {Math.round(strength.volume_load_kg)}
                  </Explained>
                )
              }
              note={
                strength.coverage === null || strength.coverage === undefined
                  ? undefined
                  : `${Math.round(strength.coverage * 100)}% of the working sets carried kilograms`
              }
            />
            <Figure
              label="Sets completed"
              value={strength.sets_completed ?? 0}
              note="working sets — a per-side row counts twice"
            />
            {strength.total_hold_s === null ||
            strength.total_hold_s === undefined ? null : (
              <Figure
                label="Held"
                unit="s"
                value={strength.total_hold_s}
                note="seconds beside the kilograms, never inside them"
              />
            )}
          </>
        )}
      </Panel>
    </section>
  );
}

function Figure({
  label,
  unit,
  value,
  note,
}: {
  label: string;
  unit?: string;
  value: React.ReactNode;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <SectionLabel>{label}</SectionLabel>
      <div className="flex items-baseline gap-1 font-mono text-ink text-xl">
        {value}
        {unit ? (
          <span className="font-mono text-ink-faint text-sm">{unit}</span>
        ) : null}
      </div>
      {note ? <span className="text-ink-faint text-2xs">{note}</span> : null}
    </div>
  );
}

/**
 * Which version these numbers are, and what they were computed against.
 *
 * The pins are the point: an IF is only meaningful beside the FTP version it
 * divided by, and that version is frozen on the artefact rather than looked up
 * now. Recomputing writes a new version and leaves this one readable.
 */
function Provenance({
  metrics,
  sessionId,
}: {
  metrics: SessionMetrics;
  sessionId: string;
}) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>How these were computed</SectionLabel>
      <Panel className="flex flex-col gap-2.5 px-5 py-4">
        <p className="text-ink-muted text-sm">
          Version {metrics.version}, computed{" "}
          {new Date(metrics.computed_at)
            .toISOString()
            .slice(0, 16)
            .replace("T", " ")}
          {metrics.recompute_reason ? ` — ${metrics.recompute_reason}` : ""}.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {metrics.pins.length === 0 ? (
            <NotAssessed reason="No anchor was in force when these were computed" />
          ) : (
            metrics.pins.map((pin) => (
              <span
                key={pin.version_id}
                title={`${pin.provenance.replace("_", " ")}, effective ${pin.effective_date}`}
                className="rounded-badge border border-hairline px-1.5 py-0.5 font-mono text-2xs text-ink-muted"
              >
                {pin.anchor_type} {pin.value.toFixed(0)} {pin.unit}
              </span>
            ))
          )}
        </div>
        <div className="border-hairline border-t pt-3">
          <RecomputeButton sessionId={sessionId} label="Recompute" />
        </div>
      </Panel>
    </section>
  );
}
