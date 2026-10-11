"use client";

import { useEffect, useId, useRef } from "react";
import uPlot from "uplot";
// uPlot's own stylesheet, load-bearing rather than cosmetic: it is what
// positions the canvas inside its wrapper.
import "uplot/dist/uPlot.min.css";

import type { components } from "@/generated/api/schema";
import { chartToken } from "@/lib/chart-tokens";
import { formatDayMonth } from "@/lib/format";
import { cn } from "@/lib/utils";

type Point = components["schemas"]["FitnessPointRead"];

/** How many pixels tall the plot is. */
const HEIGHT = 220;

/** The three lines, in legend order, with the token each is drawn in. */
export const FITNESS_LINES = [
  { key: "ctl", label: "Fitness (CTL)", token: "--color-chart-fitness" },
  { key: "atl", label: "Fatigue (ATL)", token: "--color-chart-fatigue" },
  { key: "tsb", label: "Form (TSB)", token: "--color-chart-form" },
] as const;

export interface FitnessChartProps {
  /** Every day of the window, oldest first — the API leaves none out. */
  readonly points: readonly Point[];
  readonly className?: string;
}

/**
 * Fitness, fatigue and form on one canvas.
 *
 * uPlot with `chartToken`, as `components/wellness/wellness-trajectory-chart.tsx`
 * draws: the palette stays in `globals.css` and is resolved at draw time,
 * and the instance is created once per data identity.
 *
 * **A day whose load is incomplete is marked twice.** On the canvas, as a
 * point on the fatigue line — the average a session was left out of — and in
 * text below it, which the figure names as its description. A coloured dot is
 * a claim only a sighted reader can check; the sentence is what makes "this
 * day is missing a session" reachable at all.
 */
export function FitnessChart({ points, className }: FitnessChartProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const descriptionId = useId();
  const incomplete = points.filter((point) => point.load_incomplete);

  useEffect(() => {
    const element = host.current;
    if (element === null) {
      return;
    }
    const dates = points.map((point) => point.local_date);
    const options: uPlot.Options = {
      width: element.clientWidth || 640,
      height: HEIGHT,
      legend: { show: false },
      cursor: { points: { show: false }, drag: { x: false, y: false } },
      scales: { x: { time: false } },
      axes: [
        {
          stroke: chartToken("--color-ink-faint"),
          grid: { stroke: chartToken("--color-chart-grid"), width: 1 },
          ticks: { show: false },
          values: (_self, splits) =>
            splits.map((index) => {
              const date = dates[Math.round(index)];
              return date === undefined ? "" : formatDayMonth(date);
            }),
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
        ...FITNESS_LINES.map((line) => ({
          label: line.label,
          stroke: chartToken(line.token),
          width: line.key === "tsb" ? 1 : 1.75,
          points: { show: false },
        })),
        {
          label: "Load incomplete",
          stroke: chartToken("--color-chart-fatigue"),
          fill: chartToken("--color-chart-fatigue"),
          width: 0,
          // Points only: a mark on the day, not a line between marks.
          paths: () => null,
          points: { show: true, size: 7 },
        },
      ],
    };
    const data = [
      points.map((_point, index) => index),
      ...FITNESS_LINES.map((line) =>
        points.map((point) => point[line.key].value),
      ),
      points.map((point) => (point.load_incomplete ? point.atl.value : null)),
    ] as unknown as uPlot.AlignedData;
    const instance = new uPlot(options, data, element);
    const observer = new ResizeObserver(() => {
      instance.setSize({ width: element.clientWidth || 640, height: HEIGHT });
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.destroy();
    };
  }, [points]);

  return (
    <figure
      aria-label="Fitness, fatigue and form, one point per day"
      aria-describedby={incomplete.length > 0 ? descriptionId : undefined}
      className={cn("flex flex-col gap-2", className)}
    >
      <ul className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-2xs text-ink-muted">
        {FITNESS_LINES.map((line) => (
          <li key={line.key} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-0.5 w-3"
              style={{ backgroundColor: `var(${line.token})` }}
            />
            {line.label}
          </li>
        ))}
      </ul>
      <div ref={host} data-slot="fitness-plot" />
      {incomplete.length === 0 ? null : (
        <figcaption
          id={descriptionId}
          data-testid="fitness-incomplete-days"
          className="font-mono text-2xs text-ink-muted"
        >
          Load incomplete on{" "}
          {incomplete
            .map((point) => formatDayMonth(point.local_date))
            .join(", ")}{" "}
          — a session there could not be priced, so it adds nothing to fatigue
          or fitness.
        </figcaption>
      )}
    </figure>
  );
}
