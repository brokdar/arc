"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";
// uPlot's own stylesheet, load-bearing rather than cosmetic: it is what
// positions the canvas inside its wrapper.
import "uplot/dist/uPlot.min.css";

import { chartToken } from "@/lib/chart-tokens";
import { formatDayMonth } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The athlete's own normal range for a metric, in the metric's own unit. */
export interface NormalBand {
  readonly low: number;
  readonly high: number;
}

export interface WellnessTrajectoryChartProps {
  /** What is being plotted, for the corner label and the accessible name. */
  readonly label: string;
  readonly unit: string;
  /** Every date in the range, oldest first — including the ones with no reading. */
  readonly dates: readonly string[];
  /** One value per date, `null` where nothing was recorded. */
  readonly values: readonly (number | null)[];
  /**
   * The smallest-worthwhile-change band, drawn behind the trace.
   *
   * `null` when the baseline is immature or the metric has no band at all
   * (body weight, the subjective ratings). Absent rather than guessed: a band
   * drawn from nine readings is a normal range nobody has.
   */
  readonly band?: NormalBand | null;
  readonly className?: string;
}

/** How many pixels tall one metric's panel is. */
const HEIGHT = 96;

/**
 * One metric's trajectory over the requested range.
 *
 * **Nulls are drawn as breaks**, the rule `components/sessions/stream-charts.tsx`
 * already holds to for a recording stop, and it matters more here: a wellness
 * series is mostly gaps for most athletes, and a line that bridged them would
 * draw a fortnight of readings the athlete never gave. Nothing fills,
 * interpolates or zeroes a missing morning — `spanGaps` stays false and the
 * hole is the honest picture.
 *
 * uPlot rather than a second charting stack because the session page already
 * carries it, and the same reason applies: it owns its own canvas, so the
 * instance is created once per data identity and the data is set imperatively.
 *
 * The band is painted as a horizontal region behind the trace **and** named in
 * text below it. The text is not decoration: a coloured region is a claim only
 * a sighted reader can check, and the numbers are what makes "is this outside
 * my normal" answerable without pixel-peeping.
 */
export function WellnessTrajectoryChart({
  label,
  unit,
  dates,
  values,
  band = null,
  className,
}: WellnessTrajectoryChartProps) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (element === null) {
      return;
    }
    const options: uPlot.Options = {
      width: element.clientWidth || 560,
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
        {
          label,
          stroke: chartToken("--color-accent"),
          width: 1.5,
          points: { show: dates.length <= 40, size: 4 },
          // A date the athlete did not answer is a hole, not a zero.
          spanGaps: false,
        },
      ],
      hooks: {
        draw: [
          (self) => {
            drawBand(self, band);
          },
        ],
      },
    };
    const instance = new uPlot(
      options,
      [
        Array.from(dates, (_date, index) => index),
        Array.from(values),
      ] as unknown as uPlot.AlignedData,
      element,
    );
    const observer = new ResizeObserver(() => {
      instance.setSize({ width: element.clientWidth || 560, height: HEIGHT });
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.destroy();
    };
  }, [label, dates, values, band]);

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div className="relative">
        <span className="pointer-events-none absolute top-1 left-2 z-10 font-mono text-2xs text-ink-faint uppercase tracking-[0.09em]">
          {label} · {unit}
        </span>
        <div ref={host} data-slot={`wellness-plot-${label.toLowerCase()}`} />
      </div>
      {band === null ? null : (
        <p className="font-mono text-2xs text-ink-faint">
          normal band {format(band.low)}–{format(band.high)} {unit}
        </p>
      )}
    </div>
  );
}

/** Enough decimals to tell two band edges apart, and no more. */
function format(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 100) {
    return value.toFixed(0);
  }
  return magnitude >= 1 ? value.toFixed(1) : value.toFixed(3);
}

/** Paint the normal band behind the trace, in the plot's own pixels. */
function drawBand(self: uPlot, band: NormalBand | null): void {
  if (band === null) {
    return;
  }
  const context = self.ctx;
  const top = self.valToPos(band.high, "y", true);
  const bottom = self.valToPos(band.low, "y", true);
  context.save();
  context.fillStyle = chartToken("--color-chart-band");
  context.fillRect(
    self.bbox.left,
    Math.min(top, bottom),
    self.bbox.width,
    Math.max(Math.abs(bottom - top), 1),
  );
  context.restore();
}
