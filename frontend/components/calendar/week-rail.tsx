import type * as React from "react";

import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { DisciplineIcon } from "@/components/icons";
import { formatDurationHm } from "@/lib/format";
import type { PlanWeek } from "@/lib/plan-week";
import { disciplineLabel } from "@/lib/purpose";
import { cn } from "@/lib/utils";

/**
 * The week's totals, beside the seven days they summarise.
 *
 * Left of the grid rather than above it (F3): paging weeks keeps the numbers
 * adjacent to the days that produced them, and a header band would put a whole
 * grid's height between "289 TSS" and the sessions it came from.
 *
 * Two rules run through everything below.
 *
 * **Never a total without its coverage.** `planned_load` is the sum over the
 * sessions that could be predicted, and a week of six sessions where two were
 * predictable is not a light week — it is a week two thirds of which is
 * unknown. So the count travels with the number, always, and a week with
 * nothing predictable renders `NotAssessed`, never `0`.
 *
 * **TSS and kilograms are different axes.** They get their own columns and are
 * never summed (spec v2 §5.4, §8.3); a discipline that has no figure for a
 * column gets the placeholder rather than the other discipline's number.
 */
export interface WeekRailProps {
  readonly week: PlanWeek;
  readonly className?: string;
  /**
   * What was actually done, once anything is ingested (WP-4).
   *
   * These are declared now and rendered only when defined, so the rail is laid
   * out at its final density rather than being re-laid-out twice. They are
   * deliberately *not* on the API schema: a wall of nulls in the contract is
   * noise until something can fill them (B4).
   */
  readonly completedDurationS?: number;
  readonly completedLoad?: number;
  /** The PMC series (MMP): fitness, fatigue, form, and the weekly ramp. */
  readonly fitness?: number;
  readonly fatigue?: number;
  readonly form?: number;
  readonly ramp?: number;
}

export function WeekRail({
  week,
  className,
  completedDurationS,
  completedLoad,
  fitness,
  fatigue,
  form,
  ramp,
}: WeekRailProps) {
  const counted = week.load_sessions_counted;
  const total = counted + week.load_sessions_uncounted;
  const hasCompleted =
    completedDurationS !== undefined || completedLoad !== undefined;
  const hasForm = [fitness, fatigue, form, ramp].some(
    (value) => value !== undefined,
  );

  return (
    <Panel
      // A landmark rather than a plain box: it is a supporting summary of the
      // grid beside it, and naming it is what lets a screen reader skip to or
      // past the week's totals.
      role="complementary"
      aria-label="Week totals"
      className={cn("flex flex-col gap-3.5 px-3.5 py-3", className)}
    >
      <section className="flex flex-col gap-2.5">
        <SectionLabel level={2}>Planned</SectionLabel>
        <Metric
          label="Time"
          value={formatDurationHm(week.planned_duration_s)}
        />
        <Metric
          label="Load"
          unit="TSS"
          value={
            week.planned_load === null ? (
              <NotAssessed reason="No session this week carries a predictable power target" />
            ) : (
              Math.round(week.planned_load)
            )
          }
          note={`${counted} of ${total} ${total === 1 ? "session" : "sessions"}`}
        />
      </section>

      {hasCompleted ? (
        <section className="flex flex-col gap-2.5 border-hairline border-t pt-3">
          <SectionLabel level={2}>Completed</SectionLabel>
          {completedDurationS === undefined ? null : (
            <Metric label="Time" value={formatDurationHm(completedDurationS)} />
          )}
          {completedLoad === undefined ? null : (
            <Metric label="Load" unit="TSS" value={Math.round(completedLoad)} />
          )}
        </section>
      ) : null}

      {hasForm ? (
        <section className="flex flex-col gap-2.5 border-hairline border-t pt-3">
          <SectionLabel level={2}>Trend</SectionLabel>
          {fitness === undefined ? null : (
            <Metric label="Fitness" value={Math.round(fitness)} />
          )}
          {fatigue === undefined ? null : (
            <Metric label="Fatigue" value={Math.round(fatigue)} />
          )}
          {form === undefined ? null : (
            <Metric label="Form" value={Math.round(form)} />
          )}
          {ramp === undefined ? null : (
            <Metric label="Ramp" unit="%" value={Math.round(ramp)} />
          )}
        </section>
      ) : null}

      <section className="flex flex-col gap-2.5 border-hairline border-t pt-3">
        <SectionLabel level={2}>By discipline</SectionLabel>
        {week.by_discipline.length === 0 ? (
          <p className="text-ink-muted text-sm">Nothing planned this week.</p>
        ) : (
          week.by_discipline.map((row) => (
            <div key={row.discipline} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="flex items-center gap-1.5 text-ink-secondary text-sm">
                  <DisciplineIcon discipline={row.discipline} size={12} />
                  {disciplineLabel(row.discipline)}
                </span>
                <span className="font-mono text-2xs text-ink-faint">
                  {row.session_count}{" "}
                  {row.session_count === 1 ? "session" : "sessions"}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                <Cell
                  label="Time"
                  value={formatDurationHm(row.planned_duration_s)}
                />
                <Cell
                  label="TSS"
                  value={
                    row.planned_load === null ? (
                      <NotAssessed reason="Strength volume is measured in kilograms, not TSS" />
                    ) : (
                      Math.round(row.planned_load)
                    )
                  }
                />
                <Cell
                  label="Sets"
                  value={
                    row.total_sets === null ? (
                      <NotAssessed reason="A ride is prescribed in time, not in sets" />
                    ) : (
                      row.total_sets
                    )
                  }
                />
              </div>
            </div>
          ))
        )}
      </section>
    </Panel>
  );
}

/** A label, its figure, and — when the figure needs one — its coverage. */
function Metric({
  label,
  value,
  unit,
  note,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-ink-muted text-xs">{label}</span>
        <span className="flex items-baseline gap-1">
          <span className="font-mono font-medium text-ink text-lg">
            {value}
          </span>
          {unit ? (
            <span className="text-ink-faint text-2xs">{unit}</span>
          ) : null}
        </span>
      </div>
      {note ? (
        <span className="self-end font-mono text-2xs text-ink-faint">
          {note}
        </span>
      ) : null}
    </div>
  );
}

/** One column of a discipline row. The three columns never merge. */
function Cell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-button border border-hairline bg-inset px-2 py-1.5">
      <span className="text-2xs text-ink-faint uppercase tracking-[0.08em]">
        {label}
      </span>
      <span className="truncate font-mono text-ink-secondary text-xs">
        {value}
      </span>
    </div>
  );
}
