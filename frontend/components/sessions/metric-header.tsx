"use client";

import { ProvenanceMark } from "@/components/design/anchor-provenance";
import { Explained, MetricValue } from "@/components/design/metric-explanation";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { formatDurationClock } from "@/lib/format";
import {
  LOAD_BASIS_LABELS,
  loadCounterfactual,
  pinOf,
  type SessionMetrics,
  type TimeInZone,
  zoneBands,
} from "@/lib/metrics";
import { cn } from "@/lib/utils";

export interface MetricHeaderProps {
  readonly metrics: SessionMetrics;
  /**
   * What the plan asked for, once a session is matched to one.
   *
   * `undefined` until WP-6 — the slot is declared now so that adding the
   * comparison is passing a prop rather than re-laying out the header, and
   * so that the row is laid out at its final density from the first render.
   */
  readonly plannedDurationS?: number;
  readonly plannedLoad?: number;
}

/**
 * The header row of the analysis page: the eight numbers a session is judged by.
 *
 * Every one of them is a slot that either holds a number *carrying its own
 * explanation* or holds the reason it does not (UI convention 4). The
 * explanation is the point rather than a nicety: NP over a ride with a coffee
 * stop, an IF against an FTP that was an estimate, an average power that is
 * work-over-recording-time and therefore lower than the head unit's — each is
 * a number an athlete would otherwise report as a bug.
 */
export function MetricHeader({
  metrics,
  plannedDurationS,
  plannedLoad,
}: MetricHeaderProps) {
  const ftp = pinOf(metrics, "ftp");
  const load = metrics.load;
  const counterfactual = loadCounterfactual(load);

  return (
    <Panel
      className="grid grid-cols-2 gap-x-5 gap-y-4 px-5 py-4 sm:grid-cols-4 xl:grid-cols-7"
      aria-label="Session metrics"
    >
      <Stat
        label="Duration"
        value={
          <span className="font-mono text-ink text-xl">
            {formatDurationClock(metrics.recording_time_s)}
          </span>
        }
        note={
          plannedDurationS === undefined
            ? "recording time"
            : `plan ${formatDurationClock(plannedDurationS)}`
        }
      />

      <Stat
        label="NP"
        unit="W"
        value={<MetricValue metric={metrics.power.normalized_power} />}
        note={
          <>
            avg <MetricValue metric={metrics.power.average_power} /> W
          </>
        }
      />

      <Stat
        label="IF"
        value={
          <MetricValue
            metric={metrics.power.intensity_factor}
            format={(value) => value.toFixed(2)}
          />
        }
        note={
          ftp ? (
            <>
              FTP {ftp.value.toFixed(0)}
              {ftp.ci_low !== null && ftp.ci_high !== null
                ? ` ±${Math.round((ftp.ci_high - ftp.ci_low) / 2)}`
                : ""}{" "}
              · <ProvenanceMark provenance={ftp.provenance} />
            </>
          ) : (
            "no FTP in force"
          )
        }
      />

      <Stat
        label="Load"
        unit={load.load_basis ? "TSS" : undefined}
        value={
          load.not_assessed ? (
            <NotAssessed reason={load.not_assessed} />
          ) : (
            <Explained explanation={load.explanation ?? null}>
              {Math.round(load.training_load ?? 0)}
            </Explained>
          )
        }
        note={
          load.load_basis
            ? `from ${LOAD_BASIS_LABELS[load.load_basis]}${
                plannedLoad === undefined
                  ? ""
                  : ` · plan ${Math.round(plannedLoad)}`
              }`
            : undefined
        }
      />

      <Stat
        label="Work"
        unit="kJ"
        value={<MetricValue metric={metrics.power.work_kj} />}
      />

      <Stat
        label="Avg HR"
        unit="bpm"
        value={<MetricValue metric={metrics.heart_rate.average_hr} />}
        note={
          <>
            max <MetricValue metric={metrics.heart_rate.max_hr} />
          </>
        }
      />

      <div className="col-span-2 flex min-w-0 flex-col gap-1.5 sm:col-span-4 xl:col-span-1">
        <SectionLabel>Time in zone</SectionLabel>
        <ZoneBar
          distribution={metrics.time_in_zone.power}
          fallback={metrics.time_in_zone.hr}
        />
      </div>

      {counterfactual ? (
        <p className="col-span-2 border-hairline border-t pt-3 text-ink-muted text-sm sm:col-span-4 xl:col-span-7">
          Load {Math.round(load.training_load ?? 0)}, from{" "}
          {load.load_basis ? LOAD_BASIS_LABELS[load.load_basis] : "—"}.{" "}
          {counterfactual}
        </p>
      ) : null}
    </Panel>
  );
}

/** One stat of the header row: label, number, and a line of context. */
function Stat({
  label,
  unit,
  value,
  note,
}: {
  label: string;
  unit?: string;
  value: React.ReactNode;
  note?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <SectionLabel>{label}</SectionLabel>
      <div className="flex items-baseline gap-1 font-mono text-ink text-xl">
        {value}
        {unit ? (
          <span className="font-mono text-ink-faint text-sm">{unit}</span>
        ) : null}
      </div>
      <div className="truncate font-mono text-2xs text-ink-faint">
        {note ?? " "}
      </div>
    </div>
  );
}

/**
 * The zone mini-bar: one stacked strip, painted from the shared ramp.
 *
 * Plain flexbox rather than a charting runtime (D113): one horizontal stacked
 * bar does not justify a second chart library beside uPlot, and the same
 * vocabulary already draws `WorkoutProfileBars`. Colours come only from
 * `--color-zone-*`, so a zone means one colour everywhere in the product.
 *
 * Falls back to the heart-rate distribution when there is no power one — a
 * ride with no meter still has a shape worth seeing — and says which channel
 * it drew, because the two are not the same measurement.
 */
export function ZoneBar({
  distribution,
  fallback,
  className,
}: {
  distribution: TimeInZone;
  fallback?: TimeInZone;
  className?: string;
}) {
  const shown =
    distribution.not_assessed === null ||
    distribution.not_assessed === undefined
      ? distribution
      : (fallback ?? distribution);
  if (shown.not_assessed) {
    return <NotAssessed reason={shown.not_assessed} className={className} />;
  }
  const bands = zoneBands(shown);
  const channel = shown.zone_model === "lthr_5" ? "heart rate" : "power";

  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <div
        role="img"
        aria-label={`Time in zone by ${channel}: ${bands
          .map((band) => `Z${band.index} ${(band.fraction * 100).toFixed(0)}%`)
          .join(", ")}`}
        className="flex h-5 w-full overflow-hidden rounded-button border border-hairline-faint"
      >
        {bands.map((band) => (
          <span
            key={band.index}
            title={band.title}
            style={{
              backgroundColor: band.color,
              // `flexGrow` rather than a width: the bands then divide the
              // strip exactly, with no rounding gap at the right-hand end.
              flexGrow: band.fraction,
              flexBasis: 0,
            }}
          />
        ))}
      </div>
      <div className="flex justify-between font-mono text-2xs text-ink-faint">
        {bands.map((band) => (
          <span key={band.index}>Z{band.index}</span>
        ))}
      </div>
    </div>
  );
}
