"use client";

import type * as React from "react";
import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { chartToken } from "@/lib/chart-tokens";

type Point = components["schemas"]["PowerCurvePointRead"];

/** The durations the readout names, in the order a rider reads them. */
const READOUT: readonly { durationS: number; label: string }[] = [
  { durationS: 5, label: "5 s" },
  { durationS: 60, label: "1 min" },
  { durationS: 300, label: "5 min" },
  { durationS: 1200, label: "20 min" },
  { durationS: 3600, label: "60 min" },
];

/**
 * The duration axis is logarithmic: uPlot's `distr: 3`. One constant so the
 * plot's options and the attribute a test reads cannot disagree.
 */
const X_SCALE = { kind: "log", distr: 3, base: 10 } as const;

const CHART_HEIGHT = 180;

function formatWatts(watts: number): string {
  return `${Math.round(watts)} W`;
}

/** A duration as the axis prints it: `5s`, `1m`, `20m`, `1h`. */
function axisDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  return `${Math.round((seconds / 3600) * 10) / 10}h`;
}

export interface PowerCurveCardProps {
  readonly sessionId: string;
}

/**
 * One ride's power curve: its best average watts for every duration.
 *
 * A log-scale chart of every grid point the API returns, and a readout of
 * the five durations a rider checks first. The readout lists only the
 * durations the ride is long enough for: a forty-minute ride has no hour
 * best, and that is not a missing measurement (UI convention 4's
 * placeholder) but a number that does not exist — so the slot is absent,
 * not dashed. A session with no power meter shows `NotAssessed` with the
 * API's reason instead of a curve of zeros.
 *
 * A best whose window includes seconds arc filled in (a dropout read as
 * zero) or a region the cleaner repaired is marked, because the number is
 * then partly arc's, not the power meter's.
 */
export function PowerCurveCard({ sessionId }: PowerCurveCardProps) {
  const { data, error, isPending } = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}/power-curve",
    { params: { path: { session_id: sessionId } } },
  );

  let body: React.ReactNode;
  if (error) {
    body = (
      <p className="text-ink-muted text-sm">
        {apiErrorMessages(error)[0] ?? "The power curve could not be loaded."}
      </p>
    );
  } else if (isPending || !data) {
    body = <p className="text-ink-muted text-sm">Loading the power curve…</p>;
  } else if (data.status === "not_assessed") {
    body = (
      <p className="flex items-baseline gap-2 text-ink-muted text-sm">
        <NotAssessed reason={data.reason} />
        <span>{data.reason}</span>
      </p>
    );
  } else {
    body = <Curve points={data.points} />;
  }

  return (
    <section
      aria-labelledby="power-curve-heading"
      className="flex flex-col gap-2.5"
    >
      <SectionLabel level={2} id="power-curve-heading">
        Power curve
      </SectionLabel>
      <Panel className="flex flex-col gap-3.5 px-5 py-4">{body}</Panel>
    </section>
  );
}

function Curve({ points }: { points: readonly Point[] }) {
  const readout = useMemo(
    () =>
      READOUT.flatMap(({ durationS, label }) => {
        const point = points.find((one) => one.duration_s === durationS);
        return point ? [{ label, point }] : [];
      }),
    [points],
  );
  const description = `Power curve: ${readout
    .map(({ label, point }) => `${label} ${formatWatts(point.watts)}`)
    .join(", ")}`;

  return (
    <>
      <dl className="flex flex-wrap gap-x-8 gap-y-2">
        {readout.map(({ label, point }) => (
          <div key={label} className="flex flex-col-reverse">
            <dt className="text-2xs text-ink-faint uppercase tracking-[0.09em]">
              {label}
            </dt>
            <dd
              className={
                point.clean
                  ? "font-mono text-ink text-lg"
                  : "font-mono text-ink-muted text-lg"
              }
              title={
                point.clean
                  ? undefined
                  : "Includes seconds arc filled in or repaired"
              }
            >
              {formatWatts(point.watts)}
              {point.clean ? null : (
                <>
                  <span aria-hidden="true">*</span>
                  <span className="sr-only">
                    {" "}
                    (includes seconds arc filled in or repaired)
                  </span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div role="img" aria-label={description}>
        <CurvePlot points={points} />
      </div>
    </>
  );
}

/**
 * The uPlot canvas. Built once per `points` identity, like the stream
 * charts' panels: uPlot owns its canvas, and rebuilding it per render would
 * drop the cursor.
 */
function CurvePlot({ points }: { points: readonly Point[] }) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (element === null || points.length === 0) {
      return;
    }
    const options: uPlot.Options = {
      width: element.clientWidth || 640,
      height: CHART_HEIGHT,
      legend: { show: false },
      cursor: { points: { show: false } },
      scales: {
        x: { time: false, distr: X_SCALE.distr, log: X_SCALE.base },
      },
      axes: [
        {
          stroke: chartToken("--color-ink-faint"),
          grid: { stroke: chartToken("--color-chart-grid"), width: 1 },
          ticks: { show: false },
          values: (_self, splits) => splits.map(axisDuration),
          font: "10px ui-monospace, monospace",
        },
        {
          stroke: chartToken("--color-ink-faint"),
          grid: { stroke: chartToken("--color-chart-grid"), width: 1 },
          ticks: { show: false },
          size: 44,
          font: "10px ui-monospace, monospace",
        },
      ],
      series: [
        {},
        {
          label: "Power",
          stroke: chartToken("--color-zone-5"),
          fill: chartToken("--color-chart-power-fill"),
          width: 1.5,
        },
      ],
    };
    const instance = new uPlot(
      options,
      [
        points.map((point) => point.duration_s),
        points.map((point) => point.watts),
      ],
      element,
    );
    const observer = new ResizeObserver(() => {
      instance.setSize({
        width: element.clientWidth || 640,
        height: CHART_HEIGHT,
      });
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.destroy();
    };
  }, [points]);

  return (
    <div ref={host} data-slot="power-curve-plot" data-x-scale={X_SCALE.kind} />
  );
}
