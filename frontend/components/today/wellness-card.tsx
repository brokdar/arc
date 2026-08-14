"use client";

import Link from "next/link";

import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { addDays, weekdayLabel } from "@/lib/dates";
import {
  confounderLabel,
  MARKER_FIELDS,
  SLEEP_FIELDS,
  toInputValue,
  type WellnessDay,
} from "@/lib/wellness";

/** How many days of the series the card shows behind today. */
const RECENT_DAYS = 5;

/** The three figures worth a slot on a page about today's session. */
const SHOWN = [
  SLEEP_FIELDS[0],
  ...MARKER_FIELDS.filter((spec) =>
    ["resting_hr_bpm", "hrv_ms"].includes(spec.field),
  ),
] as const;

/**
 * Today's wellness, and the few days behind it.
 *
 * One card, because the increment's promise is **one consolidated touchpoint
 * per day** and a page that scattered sleep, markers and mood across three
 * panels would have broken it on the page the athlete actually opens.
 *
 * A day nobody has answered is an *empty state with the action beside it*
 * (UI convention 3), not a dash: "Nothing recorded today" is a dead end, and
 * "Record this morning" is the whole feature.
 */
export function WellnessCard({
  today,
  className,
}: {
  readonly today: string;
  readonly className?: string;
}) {
  const start = addDays(today, -(RECENT_DAYS - 1));
  const series = $api.useQuery("get", "/api/v1/wellness/days", {
    // Half-open: `end` is the first day after today.
    params: { query: { start, end: addDays(today, 1), limit: RECENT_DAYS } },
  });

  const days = new Map(
    (series.data?.items ?? []).map((item) => [item.local_date, item]),
  );
  const day = days.get(today) ?? null;

  return (
    <Panel className={className}>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <SectionLabel level={2}>Wellness</SectionLabel>
          <Link
            href="/wellness"
            className="text-accent text-xs hover:text-accent-hover"
          >
            History
          </Link>
        </div>

        {series.isPending ? (
          <p className="text-ink-muted text-sm">Loading…</p>
        ) : day ? (
          <RecordedDay day={day} />
        ) : (
          <NothingYet />
        )}

        <ul className="flex flex-col gap-1 border-hairline border-t pt-2.5">
          {Array.from({ length: RECENT_DAYS - 1 }, (_, index) =>
            addDays(today, -(index + 1)),
          ).map((date) => (
            <li key={date} className="flex items-center gap-2.5 text-sm">
              <span className="w-[26px] shrink-0 font-mono text-ink-faint text-xs">
                {weekdayLabel(date)}
              </span>
              <PastDay date={date} day={days.get(date)} />
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}

function RecordedDay({ day }: { readonly day: WellnessDay }) {
  return (
    <div className="flex flex-col gap-2.5">
      {/* A fixed grid whose slots hold their positions: a missing marker
          renders the placeholder rather than collapsing the row, because
          position is how a returning eye finds a number (UI convention 4). */}
      <dl className="grid grid-cols-3 gap-2">
        {SHOWN.map((spec) => {
          const shown = toInputValue(day, spec);
          return (
            <div key={spec.field} className="flex flex-col gap-0.5">
              <dt className="text-ink-faint text-2xs">
                {spec.label} <span className="text-ink-faint">{spec.hint}</span>
              </dt>
              <dd className="font-mono text-base">
                {shown === "" ? (
                  <NotAssessed
                    reason={`No ${spec.label.toLowerCase()} today`}
                  />
                ) : (
                  shown
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      {day.markers.actionable ? null : (
        // On the same card as the numbers, deliberately: a coach — or an
        // athlete — who has to look somewhere else for last night's beer will
        // one day not look, and the numbers above are then read as evidence.
        <p className="rounded-button bg-danger-surface px-2.5 py-1.5 text-destructive text-xs">
          Recorded, but not actionable today:{" "}
          {day.markers.invalidated_by.map(confounderLabel).join(", ")}. The
          numbers are real; they just do not say anything about readiness this
          morning.
        </p>
      )}

      <Button
        size="xs"
        variant="secondary"
        className="self-start"
        render={<Link href="/wellness">Edit today</Link>}
      />
    </div>
  );
}

function NothingYet() {
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-ink-muted text-sm">
        Nothing recorded this morning. Sleep, resting heart rate, HRV, weight
        and how you feel — whatever you have.
      </p>
      <Button
        size="sm"
        render={<Link href="/wellness">Record this morning</Link>}
      />
    </div>
  );
}

function PastDay({
  date,
  day,
}: {
  readonly date: string;
  readonly day: WellnessDay | undefined;
}) {
  if (!day) {
    return (
      <span className="text-ink-faint">
        <NotAssessed reason={`Nothing recorded on ${date}`} /> not recorded
      </span>
    );
  }
  const parts = SHOWN.map((spec) => toInputValue(day, spec)).filter(Boolean);
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="truncate font-mono text-ink-secondary text-xs">
        {parts.length > 0 ? parts.join(" · ") : "recorded"}
      </span>
      {day.markers.actionable ? null : (
        <span
          role="img"
          aria-label={`Markers not actionable: ${day.markers.invalidated_by.join(", ")}`}
          title={day.markers.statement}
          className="text-destructive text-2xs"
        >
          ⚠
        </span>
      )}
    </span>
  );
}
