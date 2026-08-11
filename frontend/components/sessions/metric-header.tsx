"use client";

import { ProvenanceMark } from "@/components/design/anchor-provenance";
import { Explained, MetricValue } from "@/components/design/metric-explanation";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { formatDurationClock } from "@/lib/format";
import {
  LOAD_BASIS_LABELS,
  LOAD_BASIS_UNITS,
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
 * The header of the analysis page, in two rows that answer two questions.
 *
 * **What the session cost** comes first — duration, NP, IF, load, work, heart
 * rate, the shape of the time in zone — because those are the numbers a
 * session is judged by, and they are what the rest of the product is
 * denominated in. **What the ride was** follows under a rule: distance, speed,
 * climbing, cadence, how long the athlete stood still, how warm it was. Those
 * judge nothing; they are how a ride is recognised six months later, and a
 * training application that cannot say how far you went is missing the fact
 * every other one leads with.
 *
 * Every one of them is a slot that either holds a number *carrying its own
 * explanation* or holds the reason it does not (UI convention 4). The
 * explanation is the point rather than a nicety: NP over a ride with a coffee
 * stop, an IF against an FTP that was an estimate, an average power divided by
 * moving time while the load beside it is divided by recording time (D194) —
 * each is a number an athlete would otherwise report as a bug.
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
      className="flex flex-col gap-4 px-5 py-4"
      aria-label="Session metrics"
    >
      <StatRow>
        <Stat
          label="Duration"
          value={<Duration metrics={metrics} />}
          note={
            plannedDurationS === undefined
              ? durationBasis(metrics)
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
          // TSS is the *power* model's scale; the heart-rate model's is HRSS.
          // Both are calibrated so an hour at threshold is 100, so an
          // HRSS value stamped "TSS" looks entirely plausible — which is
          // exactly why it has to be labelled by its basis.
          unit={load.load_basis ? LOAD_BASIS_UNITS[load.load_basis] : undefined}
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
          note={
            <>
              above FTP <MetricValue metric={metrics.power.work_above_ftp_kj} />{" "}
              kJ
            </>
          }
        />

        <Stat
          label="Avg HR"
          unit="bpm"
          value={<MetricValue metric={metrics.heart_rate.average_hr} />}
          note={
            <>
              max <MetricValue metric={metrics.heart_rate.max_hr} /> · EF{" "}
              <MetricValue
                metric={metrics.heart_rate.efficiency_factor}
                format={(value) => value.toFixed(2)}
              />
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
      </StatRow>

      <StatRow className="border-hairline border-t pt-3.5">
        <Stat
          label="Distance"
          unit="km"
          value={
            <MetricValue
              metric={metrics.speed?.distance_km}
              format={(value) => value.toFixed(1)}
            />
          }
          note="from the speed channel"
        />

        <Stat
          label="Avg speed"
          unit="km/h"
          value={
            <MetricValue
              metric={metrics.speed?.average_speed_kmh}
              format={(value) => value.toFixed(1)}
            />
          }
          note={
            <>
              max{" "}
              <MetricValue
                metric={metrics.speed?.max_speed_kmh}
                format={(value) => value.toFixed(1)}
              />
            </>
          }
        />

        <Stat
          label="Climbing"
          unit="m"
          value={<MetricValue metric={metrics.elevation_gain_m} />}
          note="rises under 2 m are barometric noise"
        />

        <Stat
          label="VI"
          value={
            <MetricValue
              metric={metrics.power.variability_index}
              format={(value) => value.toFixed(2)}
            />
          }
          note="NP ÷ average power"
        />

        <Stat
          label="Cadence"
          unit="rpm"
          value={<MetricValue metric={metrics.cadence.average_cadence} />}
          note={
            <>
              max <MetricValue metric={metrics.cadence.max_cadence} />
            </>
          }
        />

        {/* Standing still and freewheeling are different facts and sit side by
            side on purpose: one is time the ride was not moving, the other is
            time the legs were not — and only the first is what makes a
            90-minute ride take two hours. */}
        <Stat
          label="Stopped"
          value={
            <MetricValue
              metric={metrics.stopped_time_s}
              format={formatDurationClock}
            />
          }
          note={
            <>
              coasting{" "}
              <MetricValue
                metric={metrics.power.coasting_time_s}
                format={formatDurationClock}
              />
            </>
          }
        />

        <Stat
          label="Temperature"
          unit="°C"
          value={<MetricValue metric={metrics.temperature?.average_temp_c} />}
          note={
            <>
              <MetricValue metric={metrics.temperature?.min_temp_c} /> –{" "}
              <MetricValue metric={metrics.temperature?.max_temp_c} />
            </>
          }
        />
      </StatRow>

      {counterfactual ? (
        <p className="border-hairline border-t pt-3 text-ink-muted text-sm">
          Load {Math.round(load.training_load ?? 0)}, from{" "}
          {load.load_basis ? LOAD_BASIS_LABELS[load.load_basis] : "—"}.{" "}
          {counterfactual}
        </p>
      ) : null}
    </Panel>
  );
}

/**
 * One row of stat slots, laid out identically to every other row.
 *
 * Seven columns at the widest, so the two rows' slots line up vertically and
 * a returning eye finds "avg speed" under "NP" every time (UI convention 4).
 */
function StatRow({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4 xl:grid-cols-7",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * How long the session lasted, and never a zero standing in for absent.
 *
 * `recording_time_s` is the load-bearing duration for a *device* session —
 * elapsed with the pauses removed (A4.4) — and it is **0.0** on every
 * stream-free artefact, because a typed-in gym session has no recording and
 * therefore no recording time. Printing it there rendered "0:00" under a
 * session the athlete spent an hour on. So the elapsed time is the answer
 * when there is no recording, the note says which of the two is on screen,
 * and a session with neither holds the slot with its reason.
 */
function Duration({ metrics }: { metrics: SessionMetrics }) {
  const seconds =
    metrics.recording_time_s > 0
      ? metrics.recording_time_s
      : metrics.elapsed_time_s;
  if (seconds <= 0) {
    return <NotAssessed reason="This session records no duration to report" />;
  }
  return (
    <span className="font-mono text-ink text-xl">
      {formatDurationClock(seconds)}
    </span>
  );
}

/** Which duration the stat above is showing. The two are different numbers. */
function durationBasis(metrics: SessionMetrics): string {
  if (metrics.recording_time_s > 0) {
    return "recording time";
  }
  return metrics.elapsed_time_s > 0 ? "elapsed" : " ";
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
