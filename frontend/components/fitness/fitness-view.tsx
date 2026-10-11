"use client";

import Link from "next/link";
import { useState } from "react";

import { Explained } from "@/components/design/metric-explanation";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { FitnessChart } from "@/components/fitness/fitness-chart";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { loadFailureMessage } from "@/lib/api-errors";
import { useAthleteTimezone } from "@/lib/clock";
import { addDays, todayIsoDate } from "@/lib/dates";
import { formatDayMonth } from "@/lib/format";

type Trend = components["schemas"]["FitnessTrendRead"];

/** How far back the page reads: a season, and the run-in to it. */
const WINDOW_DAYS = 365;

/**
 * What the display-only badge says. Exported so the test asserts the same
 * sentence the page renders, and the wording is changed in one place.
 */
export const DISPLAY_ONLY_SENTENCE =
  "arc will never change your plan because of this number";

/**
 * `/fitness`: fitness, fatigue and form over the rides arc already holds.
 *
 * Two things this page says before any number, because both are ways the
 * same curve could be read as more than it is:
 *
 * * **It describes; it never decides.** The badge is on screen whatever the
 *   data is — loading, failed, empty — because it is a statement about what
 *   kind of number lives here, not about this athlete's numbers
 *   (`app.domain.authority`).
 * * **A young series is mostly its starting zero.** While CTL is maturing the
 *   page says so with the API's own day count, rather than printing a
 *   confidently low fitness for an athlete whose history began in arc.
 */
export function FitnessView() {
  // The athlete's clock, read once on mount — see `lib/clock.tsx`.
  const timezone = useAthleteTimezone();
  const [today] = useState(() => todayIsoDate(timezone));
  const from = addDays(today, -(WINDOW_DAYS - 1));
  const trend = $api.useQuery("get", "/api/v1/fitness", {
    params: { query: { from } },
  });

  return (
    <>
      <Toolbar />
      <PageBody className="flex flex-col gap-[18px]">
        <header className="flex flex-col gap-1">
          <h1 className="font-semibold text-2xl tracking-[-0.02em]">Fitness</h1>
          <p className="text-base text-ink-muted">
            Fitness, fatigue and form from the sessions arc holds.
          </p>
        </header>
        <DisplayOnlyBadge />
        {trend.data ? (
          <FitnessBody trend={trend.data} />
        ) : trend.error ? (
          <p role="alert" className="text-base text-status-missed">
            {loadFailureMessage(trend.error, "the fitness series")}
          </p>
        ) : (
          <p className="text-base text-ink-muted">Loading the series…</p>
        )}
      </PageBody>
    </>
  );
}

/** The standing statement of what kind of number this page shows. */
function DisplayOnlyBadge() {
  return (
    <p
      role="note"
      aria-label="Display-only"
      className="self-start rounded-badge border border-hairline-card bg-inset px-2.5 py-1 text-ink-secondary text-sm"
    >
      <span className="font-semibold text-ink">Display-only.</span> These
      numbers describe your training — {DISPLAY_ONLY_SENTENCE}.
    </p>
  );
}

/** Why a readout slot is empty when nothing behind it was priced. */
const UNPRICED_REASON = "No session has a priced load yet";

function FitnessBody({ trend }: { trend: Trend }) {
  if (trend.points.length === 0) {
    return trend.maturity.first_session === null ? (
      <Panel className="px-5 py-4">
        <RecordARide />
      </Panel>
    ) : (
      <p className="text-base text-ink-muted">
        No days in this range — the series starts on{" "}
        <span className="font-mono">
          {formatDayMonth(trend.maturity.first_session)}
        </span>
        .
      </p>
    );
  }
  // Sessions, but none either load model could price: the averages are 0
  // with no load behind them, which is no data, not zero fitness.
  if (trend.maturity.first_priced_session === null) {
    return (
      <Panel className="flex flex-col gap-4 px-5 py-4">
        <SectionLabel level={2}>Fitness, fatigue and form</SectionLabel>
        <Readout point={null} trend={trend} />
        <RecordARide lead="None of your sessions so far has a power or heart-rate load." />
      </Panel>
    );
  }
  const latest = trend.points[trend.points.length - 1];
  return (
    <Panel className="flex flex-col gap-4 px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <SectionLabel level={2}>Fitness, fatigue and form</SectionLabel>
        <MaturityNote trend={trend} />
      </div>
      {latest ? <Readout point={latest} trend={trend} /> : null}
      <FitnessChart points={trend.points} />
    </Panel>
  );
}

/** The remedy for a series with nothing priced behind it. */
function RecordARide({ lead }: { lead?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="text-base text-ink-secondary">
        {lead ? `${lead} ` : null}Record a ride with power or heart rate to see
        fitness, fatigue and form.
      </p>
      <Link
        href="/inbox"
        className="rounded-button bg-accent px-3 py-1.5 font-medium text-accent-ink text-sm"
      >
        Upload a ride
      </Link>
    </div>
  );
}

/**
 * Whether each average still mostly describes its starting zero.
 *
 * The day count and the day it matures on are the API's, never derived here:
 * a second copy of the maturity rule in the UI is how the two would disagree.
 */
function MaturityNote({ trend }: { trend: Trend }) {
  const { maturity } = trend;
  if (maturity.day === null) {
    return null;
  }
  const notes: string[] = [];
  if (!maturity.ctl.mature) {
    notes.push(
      `Baseline maturing — day ${maturity.day} of ${maturity.ctl.matures_on_day}`,
    );
  }
  if (!maturity.atl.mature) {
    notes.push(
      `Fatigue maturing — day ${maturity.day} of ${maturity.atl.matures_on_day}`,
    );
  }
  if (notes.length === 0) {
    return null;
  }
  return (
    <p className="flex flex-col font-mono text-2xs text-status-under">
      {notes.map((note) => (
        <span key={note}>{note}</span>
      ))}
      <span className="font-sans text-ink-muted">
        Until then the curve still carries the zero it started from more than
        your training.
      </span>
    </p>
  );
}

/**
 * The last day's three numbers, each under its series' explanation — or, with
 * no priced load behind them (`point` null), a placeholder in each slot.
 */
function Readout({
  point,
  trend,
}: {
  point: Trend["points"][number] | null;
  trend: Trend;
}) {
  const slots = [
    {
      label: "Fitness",
      value: point?.ctl.value,
      explanation: trend.explanations.ctl,
    },
    {
      label: "Fatigue",
      value: point?.atl.value,
      explanation: trend.explanations.atl,
    },
    {
      label: "Form",
      value: point?.tsb.value,
      explanation: trend.explanations.tsb,
    },
  ];
  return (
    <section aria-label="Today" className="grid grid-cols-3 gap-3 sm:max-w-md">
      {slots.map((slot) => (
        <div key={slot.label} className="flex flex-col gap-0.5">
          <SectionLabel>{slot.label}</SectionLabel>
          {slot.value === undefined ? (
            <NotAssessed reason={UNPRICED_REASON} className="text-xl" />
          ) : (
            <Explained
              explanation={slot.explanation}
              className="font-mono text-ink text-xl"
            >
              {slot.value.toFixed(0)}
            </Explained>
          )}
        </div>
      ))}
    </section>
  );
}
