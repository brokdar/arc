"use client";

import { Td, Th } from "@/components/design/data-table";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { weekdayLabel } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";
import {
  confounderLabel,
  MARKER_FIELDS,
  SLEEP_FIELDS,
  toInputValue,
  type WellnessDay,
} from "@/lib/wellness";

/** The four columns worth scanning down. Whole days are one click away. */
const COLUMNS = [
  SLEEP_FIELDS[0],
  ...MARKER_FIELDS.filter((spec) =>
    ["resting_hr_bpm", "hrv_ms", "weight_kg"].includes(spec.field),
  ),
] as const;

/**
 * Every recorded day over a range, with the absences visible as absences.
 *
 * The gaps are the point. A table of only the days the athlete answered would
 * read as an unbroken record; this one lists **every** date in the range and
 * renders the unanswered ones with the `not-assessed` placeholder in their
 * fixed slots (UI conventions 3 and 4), so a fortnight of silence looks like a
 * fortnight of silence rather than like a shorter month.
 *
 * A day whose device numbers a confounder voided is marked on the same row as
 * the numbers, for the reason the API puts the standing on the same object:
 * a reader who has to look elsewhere for last night's beer will one day not.
 */
export function WellnessHistory({
  dates,
  days,
  selected,
  onSelect,
  className,
}: {
  /** Every date in the range, oldest first — including the unanswered ones. */
  readonly dates: readonly string[];
  /** The recorded days, keyed by date. */
  readonly days: ReadonlyMap<string, WellnessDay>;
  readonly selected: string;
  readonly onSelect: (date: string) => void;
  readonly className?: string;
}) {
  return (
    <Panel className={className}>
      <div className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-2">
        <SectionLabel level={2}>History</SectionLabel>
        <span className="font-mono text-ink-faint text-2xs">
          {`${days.size} of ${dates.length} days recorded`}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-hairline border-b text-left">
              <Th>Day</Th>
              {COLUMNS.map((spec) => (
                <Th key={spec.field} className="text-right">
                  {`${spec.label} (${spec.hint})`}
                </Th>
              ))}
              <Th>Fatigue</Th>
              <Th>Note</Th>
            </tr>
          </thead>
          <tbody>
            {[...dates].reverse().map((date) => {
              const day = days.get(date);
              return (
                <tr
                  key={date}
                  aria-current={date === selected ? "true" : undefined}
                  className={
                    date === selected
                      ? "border-hairline border-b bg-accent-wash"
                      : "border-hairline border-b hover:bg-card-hover"
                  }
                >
                  <Td>
                    <button
                      type="button"
                      onClick={() => onSelect(date)}
                      className="font-mono text-accent text-xs hover:text-accent-hover"
                    >
                      {weekdayLabel(date)} {formatDayMonth(date)}
                    </button>
                  </Td>
                  {COLUMNS.map((spec) => {
                    const shown = toInputValue(day, spec);
                    return (
                      <Td key={spec.field} className="text-right font-mono">
                        {shown === "" ? (
                          <NotAssessed
                            reason={
                              day
                                ? `No ${spec.label.toLowerCase()} recorded on ${date}`
                                : `Nothing recorded on ${date}`
                            }
                          />
                        ) : (
                          shown
                        )}
                      </Td>
                    );
                  })}
                  <Td className="font-mono">
                    {day?.fatigue ?? (
                      <NotAssessed reason={`No fatigue rating on ${date}`} />
                    )}
                  </Td>
                  <Td className="text-ink-muted">
                    <DayMarks day={day} date={date} />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/** The two things about a day that change how its numbers read. */
function DayMarks({
  day,
  date,
}: {
  readonly day: WellnessDay | undefined;
  readonly date: string;
}) {
  if (!day) {
    return <NotAssessed reason={`Nothing recorded on ${date}`} />;
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {day.markers.actionable ? null : (
        <span
          className="rounded-badge bg-danger-surface px-1.5 py-0.5 text-2xs text-destructive"
          title={day.markers.statement}
        >
          not actionable:{" "}
          {day.markers.invalidated_by.map(confounderLabel).join(", ")}
        </span>
      )}
      {day.subjective_recalled ? (
        <span
          className="rounded-badge bg-inset px-1.5 py-0.5 text-2xs text-ink-faint"
          title="Entered more than two days after the day it describes, so the ratings are recall rather than report. The device numbers are not discounted for it."
        >
          recalled
        </span>
      ) : null}
      {day.note ? <span className="truncate">{day.note}</span> : null}
    </span>
  );
}
