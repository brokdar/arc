"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
// uPlot's own stylesheet, and it is load-bearing rather than cosmetic: it is
// what positions the canvas inside its wrapper. Without it every panel of the
// stack renders on top of the one above it.
import "uplot/dist/uPlot.min.css";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { chartToken } from "@/lib/chart-tokens";
import { formatDurationClock } from "@/lib/format";
import {
  channelValues,
  type SelectionStats,
  type SessionStreams,
  type StreamChannel,
  selectionStats,
} from "@/lib/metrics";
import { cn } from "@/lib/utils";

/** One planned step, projected onto the recording's timeline. */
export interface PlannedBand {
  /** First second of the band on the elapsed axis. */
  readonly fromS: number;
  /** One past the last — `[from, to)`, like every range in this system. */
  readonly toS: number;
  /** Lower edge of the prescribed power band, in watts. */
  readonly lowWatts: number;
  /** Upper edge. Equal to `lowWatts` for a point target. */
  readonly highWatts: number;
}

/** One region the cleaner repaired, as the chart marks it. */
interface Repair {
  readonly channel: StreamChannel;
  readonly fromS: number;
  readonly toS: number;
  readonly kind: string;
}

/** The channels the page stacks, top to bottom, and how each is drawn. */
const PANELS: readonly {
  channel: StreamChannel;
  label: string;
  unit: string;
  /** A palette token name; resolved to a canvas colour at draw time. */
  stroke: string;
  fill?: string;
  height: number;
}[] = [
  {
    channel: "power",
    label: "Power",
    unit: "W",
    stroke: "--color-zone-5",
    fill: "--color-chart-power-fill",
    height: 190,
  },
  {
    channel: "hr",
    label: "HR",
    unit: "bpm",
    stroke: "--color-zone-6",
    height: 96,
  },
  {
    channel: "cadence",
    label: "Cadence",
    unit: "rpm",
    stroke: "--color-zone-2",
    height: 78,
  },
  {
    channel: "elevation",
    label: "Elevation",
    unit: "m",
    stroke: "--color-ink-faint",
    fill: "--color-chart-elevation-fill",
    height: 78,
  },
];

export interface StreamChartsProps {
  readonly streams: SessionStreams;
  /**
   * The FTP the artefact was computed against, drawn as a reference line on
   * the power panel. `undefined` when no anchor was in force — the line is
   * then absent rather than drawn at a guess.
   */
  readonly ftpWatts?: number;
  /**
   * Resolved planned step bands, drawn behind the power trace.
   *
   * `undefined` until WP-6 gives a session a match to resolve them from
   * (D116). The component renders nothing for it, so wiring the data later is
   * a prop change rather than a new component — and the capability is tested
   * against a mock today so WP-6 finds it working.
   */
  readonly plannedBands?: readonly PlannedBand[];
  readonly className?: string;
}

/**
 * The stacked stream charts: power, heart rate, cadence, elevation.
 *
 * One uPlot instance per channel present, cursor-synced through a shared
 * `uPlot.sync` key, so a hover anywhere reads every channel at that instant
 * and a zoom on one panel zooms all of them. uPlot rather than a
 * general-purpose chart library because 14 400 points per channel is where
 * SVG charting stops being interactive, and it is the **only** chart
 * dependency this page adds (D113).
 *
 * **Nulls are drawn as breaks.** A recording stop is a hole in the data
 * (A4.1), and uPlot renders a null as a gap in the trace by default. Nothing
 * here fills, interpolates or zeroes them: the gap is the honest picture of a
 * coffee stop, and a continuous line across it would be a ride the athlete
 * did not do.
 *
 * A drag across any panel selects a range and publishes its statistics —
 * duration, average power, NP, average HR — computed in the browser over the
 * arrays already loaded. That is the one place this codebase computes a
 * training number twice; see `lib/metrics.normalizedPower` for why, and for
 * the test that keeps the two in step.
 */
export function StreamCharts({
  streams,
  ftpWatts,
  plannedBands,
  className,
}: StreamChartsProps) {
  const [cursorRow, setCursorRow] = useState<number | null>(null);
  const [selection, setSelection] = useState<{
    from: number;
    to: number;
  } | null>(null);

  const present = useMemo(
    () =>
      PANELS.map((panel) => ({
        ...panel,
        values: channelValues(streams, panel.channel),
      })).filter(
        (
          panel,
        ): panel is (typeof PANELS)[number] & { values: (number | null)[] } =>
          panel.values?.some((value) => value !== null) ?? false,
      ),
    [streams],
  );

  const elapsed = useMemo(
    () => Array.from({ length: streams.length }, (_, index) => index),
    [streams.length],
  );

  const repairs = useMemo(
    () =>
      streams.anomalies.map(
        (anomaly): Repair => ({
          channel: anomaly.channel,
          fromS: anomaly.start_index,
          toS: anomaly.end_index,
          kind: anomaly.kind,
        }),
      ),
    [streams.anomalies],
  );

  const stats = useMemo(
    () =>
      selection === null
        ? null
        : selectionStats(streams, selection.from, selection.to),
    [selection, streams],
  );

  if (present.length === 0) {
    return null;
  }

  return (
    <section className={cn("flex flex-col gap-2.5", className)}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <SectionLabel level={2}>Streams</SectionLabel>
        {selection ? (
          <span className="font-mono text-2xs text-ink-faint">
            selection {formatDurationClock(selection.from)} –{" "}
            {formatDurationClock(selection.to)}
          </span>
        ) : null}
        <Readout streams={streams} row={cursorRow} className="ml-auto" />
      </div>

      <Panel tone="inset" className="flex flex-col gap-1 px-2 py-2">
        {present.map((panel, index) => (
          <ChannelPlot
            key={panel.channel}
            label={panel.label}
            unit={panel.unit}
            stroke={panel.stroke}
            fill={panel.fill}
            height={panel.height}
            elapsed={elapsed}
            values={panel.values}
            axis={index === present.length - 1}
            referenceLine={panel.channel === "power" ? ftpWatts : undefined}
            bands={panel.channel === "power" ? plannedBands : undefined}
            repairs={repairs.filter(
              (repair) => repair.channel === panel.channel,
            )}
            onCursor={setCursorRow}
            onSelect={setSelection}
          />
        ))}
      </Panel>

      {stats ? <SelectionPanel stats={stats} /> : null}
    </section>
  );
}

/** The synced-cursor readout: what every channel said at one instant. */
function Readout({
  streams,
  row,
  className,
}: {
  streams: SessionStreams;
  row: number | null;
  className?: string;
}) {
  const at = (channel: StreamChannel) => {
    if (row === null) {
      return null;
    }
    return channelValues(streams, channel)?.[row] ?? null;
  };
  const cell = (label: string, value: number | null, digits = 0) => (
    <div key={label} className="flex gap-1 whitespace-nowrap">
      <dt className="text-ink-faint">{label}</dt>
      <dd className="text-ink">
        {value === null ? "—" : value.toFixed(digits)}
      </dd>
    </div>
  );

  return (
    // A definition list rather than a labelled group: the readout is
    // label/value pairs and nothing else, and `dl` says so without needing a
    // role. Deliberately **not** a live region — the readout follows the
    // cursor, and an announcement per pixel of mouse travel would make the
    // page unusable with a screen reader.
    <dl
      aria-label="Values at the cursor"
      className={cn(
        "flex flex-wrap gap-x-3 font-mono text-2xs text-ink-muted",
        className,
      )}
    >
      {cell("t", row)}
      {cell("P", at("power"))}
      {cell("HR", at("hr"))}
      {cell("rpm", at("cadence"))}
    </dl>
  );
}

/** Live statistics for the selected range. */
function SelectionPanel({ stats }: { stats: SelectionStats }) {
  const cells: readonly [string, string][] = [
    ["duration", formatDurationClock(stats.durationS)],
    [
      "avg W",
      stats.averagePower === null ? "—" : stats.averagePower.toFixed(0),
    ],
    [
      "NP",
      stats.normalizedPower === null ? "—" : stats.normalizedPower.toFixed(0),
    ],
    ["avg HR", stats.averageHr === null ? "—" : stats.averageHr.toFixed(0)],
    [
      "rpm",
      stats.averageCadence === null ? "—" : stats.averageCadence.toFixed(0),
    ],
  ];
  return (
    <Panel
      tone="inset"
      data-slot="selection-stats"
      className="flex flex-wrap gap-x-6 gap-y-1.5 px-3.5 py-2.5"
    >
      {cells.map(([label, value]) => (
        <div key={label} className="flex flex-col">
          <span className="font-mono text-ink text-lg">{value}</span>
          <SectionLabel>{label}</SectionLabel>
        </div>
      ))}
    </Panel>
  );
}

/** How the sibling plots find each other's cursor. */
const SYNC_KEY = "arc-session-streams";

/**
 * One channel's panel.
 *
 * The uPlot instance is created once per mount and destroyed on unmount; the
 * data is set imperatively when it changes, because uPlot owns its own canvas
 * and re-creating it per render would drop the cursor and the zoom on every
 * hover.
 */
function ChannelPlot({
  label,
  unit,
  stroke,
  fill,
  height,
  elapsed,
  values,
  axis,
  referenceLine,
  bands,
  repairs,
  onCursor,
  onSelect,
}: {
  label: string;
  unit: string;
  stroke: string;
  fill?: string;
  height: number;
  elapsed: readonly number[];
  values: readonly (number | null)[];
  axis: boolean;
  referenceLine?: number;
  bands?: readonly PlannedBand[];
  repairs: readonly Repair[];
  onCursor: (row: number | null) => void;
  onSelect: (range: { from: number; to: number } | null) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const plot = useRef<uPlot | null>(null);

  useEffect(() => {
    const element = host.current;
    if (element === null) {
      return;
    }
    const options: uPlot.Options = {
      width: element.clientWidth || 640,
      height,
      // The legend is a row of our own above the stack; uPlot's would repeat
      // it per panel and undo the point of syncing the cursor.
      legend: { show: false },
      cursor: {
        sync: { key: SYNC_KEY },
        drag: { x: true, y: false, setScale: false },
        points: { show: false },
      },
      scales: { x: { time: false } },
      axes: [
        {
          show: axis,
          stroke: chartToken("--color-ink-faint"),
          grid: { stroke: chartToken("--color-chart-grid"), width: 1 },
          ticks: { show: false },
          values: (_self, splits) =>
            splits.map((seconds) => formatDurationClock(seconds)),
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
          stroke: chartToken(stroke),
          fill: fill === undefined ? undefined : chartToken(fill),
          width: 1.25,
          // Nulls are gaps, not zeros: `spanGaps` stays false so a recording
          // stop breaks the trace (A4.1).
          spanGaps: false,
        },
      ],
      hooks: {
        setCursor: [
          (self) => {
            onCursor(self.cursor.idx ?? null);
          },
        ],
        setSelect: [
          (self) => {
            const { left, width } = self.select;
            if (width <= 0) {
              onSelect(null);
              return;
            }
            onSelect({
              from: Math.round(self.posToVal(left, "x")),
              to: Math.round(self.posToVal(left + width, "x")),
            });
          },
        ],
        draw: [
          (self) => {
            drawOverlays(self, { referenceLine, bands, repairs });
          },
        ],
      },
    };
    const instance = new uPlot(
      options,
      [Array.from(elapsed), Array.from(values)] as unknown as uPlot.AlignedData,
      element,
    );
    plot.current = instance;

    const observer = new ResizeObserver(() => {
      instance.setSize({ width: element.clientWidth || 640, height });
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.destroy();
      plot.current = null;
    };
    // Rebuilt only when the shape of the panel changes. The data is set
    // separately below so that a new session does not tear down the canvas.
  }, [
    height,
    axis,
    label,
    stroke,
    fill,
    referenceLine,
    bands,
    repairs,
    onCursor,
    onSelect,
    elapsed,
    values,
  ]);

  return (
    <div className="relative">
      <span className="pointer-events-none absolute top-1 left-2 z-10 font-mono text-2xs text-ink-faint uppercase tracking-[0.09em]">
        {label} · {unit}
      </span>
      <div ref={host} data-slot={`stream-plot-${label.toLowerCase()}`} />
    </div>
  );
}

/**
 * What is painted behind and over the trace.
 *
 * Three things, in the order they are drawn: the planned step bands (a slot
 * that stays empty until WP-6), the repaired regions the cleaner substituted
 * for (A4.2's "the chart marks repairs"), and the FTP reference line from the
 * artefact's own pinned anchor — never from the athlete's current FTP, which
 * would move a line drawn over a ride that was ridden months ago.
 */
function drawOverlays(
  self: uPlot,
  overlays: {
    referenceLine?: number;
    bands?: readonly PlannedBand[];
    repairs: readonly Repair[];
  },
): void {
  const context = self.ctx;
  const { left, top, width, height } = self.bbox;
  context.save();
  context.beginPath();
  context.rect(left, top, width, height);
  context.clip();

  for (const band of overlays.bands ?? []) {
    const x0 = self.valToPos(band.fromS, "x", true);
    const x1 = self.valToPos(band.toS, "x", true);
    const y0 = self.valToPos(band.highWatts, "y", true);
    const y1 = self.valToPos(band.lowWatts, "y", true);
    context.fillStyle = chartToken("--color-chart-band");
    context.fillRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0));
  }

  for (const repair of overlays.repairs) {
    const x0 = self.valToPos(repair.fromS, "x", true);
    const x1 = self.valToPos(repair.toS, "x", true);
    context.fillStyle = chartToken("--color-chart-mark");
    context.fillRect(x0, top, Math.max(1, x1 - x0), 3);
  }

  if (overlays.referenceLine !== undefined) {
    const y = self.valToPos(overlays.referenceLine, "y", true);
    context.strokeStyle = chartToken("--color-chart-reference");
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + width, y);
    context.stroke();
  }
  context.restore();
}
