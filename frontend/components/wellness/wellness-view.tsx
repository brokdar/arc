"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { WellnessForm } from "@/components/wellness/wellness-form";
import { WellnessHistory } from "@/components/wellness/wellness-history";
import {
  CHARTED_METRICS,
  WellnessTrajectories,
} from "@/components/wellness/wellness-trajectories";
import { $api } from "@/lib/api/client";
import { loadFailureMessage } from "@/lib/api-errors";
import { useAthleteTimezone } from "@/lib/clock";
import { addDays, isIsoDate, todayIsoDate } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";

/** How far back the history table looks. Long enough to see a training block. */
const HISTORY_DAYS = 28;

/**
 * `/wellness`: the day, and the days before it.
 *
 * A real route rather than a panel on Today, because a person would bookmark
 * it (UI convention 1) and because `?date=` has to be addressable: correcting
 * last Tuesday is a thing the athlete does, and it should be a link they can
 * follow rather than a mode they have to get into.
 *
 * What is *not* here is any interpretation. There is no readiness score, no
 * verdict, no "you should rest" — arc stores what the athlete reported and
 * describes it, and reading it is the coach's job. What the page *does* derive
 * is descriptive: the confounder standing, which restates what the athlete
 * themselves declared, and the trajectory block, which says what is normal for
 * this athlete and how far the last week sits from it — or abstains, in the
 * API's own words, when the series is too short to bear a normal at all.
 */
export function WellnessView() {
  const router = useRouter();
  const params = useSearchParams();
  // The athlete's clock, read once on mount — see `lib/clock.tsx`.
  const timezone = useAthleteTimezone();
  const [today] = useState(() => todayIsoDate(timezone));
  const requested = params.get("date");
  // A pasted `?date=` is checked before it is used: `2026-02-31` parses and
  // rolls over, and a range built from a rolled-over date silently asks about
  // the wrong month.
  const date = isIsoDate(requested) ? requested : today;

  const start = addDays(date, -(HISTORY_DAYS - 1));
  // Half-open, like every range in this application: `end` is the first day
  // *after* the one the athlete asked about.
  const end = addDays(date, 1);

  const inputs = $api.useQuery("get", "/api/v1/wellness/inputs");
  const series = $api.useQuery("get", "/api/v1/wellness/days", {
    params: { query: { start, end, limit: HISTORY_DAYS } },
  });
  // The same window as the table, so the chart and the rows are one picture.
  // The baseline behind it still reaches sixty days back — the read decides
  // that, not the range asked for, which is what lets a four-week page carry a
  // mature normal range.
  const trend = $api.useQuery("get", "/api/v1/wellness/trend", {
    params: {
      query: {
        start,
        end,
        metric: CHARTED_METRICS.map((charted) => charted.metric),
      },
    },
  });

  const select = useCallback(
    (next: string) => {
      // The URL is the state: a reload, a back button and a shared link all
      // land on the same day.
      router.push(next === today ? "/wellness" : `/wellness?date=${next}`);
    },
    [router, today],
  );

  const days = new Map(
    (series.data?.items ?? []).map((item) => [item.local_date, item]),
  );
  // How the athlete's most recent HRV reading was taken, so a new one inherits
  // it instead of being stamped with a guess. Newest-first because the series
  // is oldest-first.
  const lastHrv =
    [...(series.data?.items ?? [])]
      .reverse()
      .flatMap((item) =>
        item.hrv_metric && item.hrv_context
          ? [{ hrv_metric: item.hrv_metric, hrv_context: item.hrv_context }]
          : [],
      )[0] ?? null;
  const dates = Array.from({ length: HISTORY_DAYS }, (_, index) =>
    addDays(start, index),
  );

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Wellness</h1>
        <span className="text-ink-muted text-sm">
          One touchpoint a day. Nothing here is required.
        </span>
        <div className="ml-auto flex items-center gap-2 font-mono text-xs">
          <button
            type="button"
            onClick={() => select(addDays(date, -1))}
            className="text-accent hover:text-accent-hover"
          >
            ← {formatDayMonth(addDays(date, -1))}
          </button>
          {date === today ? null : (
            <button
              type="button"
              onClick={() => select(today)}
              className="text-accent hover:text-accent-hover"
            >
              Today
            </button>
          )}
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-[18px]">
        {series.error && !series.data ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(series.error, "the wellness series")}
          </p>
        ) : null}

        {/* The form is not rendered until the series has settled. Its fields
            seed from the stored day *at mount*, so mounting it against an
            undefined day and reconciling later is the shape of a form that
            overwrites what the athlete is typing when the fetch lands. */}
        {series.isPending ? (
          <Panel className="px-5 py-4">
            <SectionLabel level={2}>Loading the day…</SectionLabel>
          </Panel>
        ) : (
          <>
            <WellnessForm
              date={date}
              day={days.get(date) ?? null}
              inputs={inputs.data}
              lastHrv={lastHrv}
            />
            <WellnessTrajectories trend={trend.data} />
            <WellnessHistory
              dates={dates}
              days={days}
              selected={date}
              onSelect={select}
            />
          </>
        )}
      </PageBody>
    </>
  );
}
