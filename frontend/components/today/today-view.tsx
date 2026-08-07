"use client";

import Link from "next/link";
import { useState } from "react";

import type { WeekSession } from "@/components/calendar/session-card";
import { AnchorProvenance } from "@/components/design/anchor-provenance";
import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { ResolvedStepList } from "@/components/design/resolved-steps";
import { SectionLabel } from "@/components/design/section-label";
import { StatusDot } from "@/components/design/status-dot";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import { SessionForm } from "@/components/plan/session-form";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { useExercises } from "@/components/workouts/exercise-catalogue";
import { $api } from "@/lib/api/client";
import { describeCriterion } from "@/lib/criteria";
import { mondayOf, todayIsoDate, weekdayLabel } from "@/lib/dates";
import { formatDayMonthYear, formatDurationHm, formatSets } from "@/lib/format";
import { purposeLabel } from "@/lib/purpose";
import { sessionHeadline } from "@/lib/session-headline";
import {
  channelBands,
  channelLabel,
  describePrescribed,
  describeSpan,
  profileLegend,
  resolveBand,
} from "@/lib/targets";
import { ZONE_COLORS } from "@/lib/workout-profile";

/**
 * Today: what to do, why, and how it is judged.
 *
 * Deliberately narrower than the mockup's Today screen. Weather, readiness,
 * TSB, TSS, RPE logging and the coach's proposals all belong to work packages
 * that do not exist yet, and drawing them from nothing would make the page a
 * picture of an application rather than the application. What is here is
 * everything the *plan* knows: the frozen prescription, its targets, its
 * criteria, and where the day sits in the week.
 *
 * Every absolute number on this page comes from the session's own
 * `resolved_steps` — resolved by the backend against the anchor versions the
 * intent **pinned** (D49), never against whatever anchor is in force now. The
 * page therefore says what the plan said on the day it was written, and the
 * provenance line under the profile says whose FTP that was.
 */
export function TodayView() {
  // Read once on mount: re-deriving "today" mid-render would let a page left
  // open overnight disagree with itself.
  const [today] = useState(todayIsoDate);
  const [start] = useState(() => mondayOf(today));
  const [planning, setPlanning] = useState(false);

  const week = $api.useQuery("get", "/api/v1/plan/week", {
    params: { query: { start } },
  });

  const days = week.data?.days ?? [];
  const todaySessions = days.find((day) => day.date === today)?.sessions ?? [];
  const ordered = [...todaySessions].sort(relevance);

  return (
    <>
      <Toolbar>
        {/* The page's one `h1`. Today can hold two sessions, and two `h1`s
            would leave a screen reader with two documents on one screen —
            so the session headlines below are `h2`s under this. */}
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Today</h1>
        <span className="font-mono text-ink-muted text-sm">
          {weekdayLabel(today)} {formatDayMonthYear(today)}
        </span>
        <div className="ml-auto">
          <Button size="sm" onClick={() => setPlanning(true)}>
            Plan a session
          </Button>
        </div>
      </Toolbar>

      <PageBody className="flex flex-wrap items-start gap-[18px]">
        <div className="flex min-w-0 flex-[1_1_620px] flex-col gap-3.5">
          {week.isPending ? (
            <p className="text-ink-muted text-sm">Loading today…</p>
          ) : week.error ? (
            <p role="alert" className="text-destructive text-sm">
              Could not load today. Is the API reachable?
            </p>
          ) : ordered.length === 0 ? (
            <RestDay onPlan={() => setPlanning(true)} />
          ) : (
            ordered.map((session) => (
              <SessionPanel key={session.id} session={session} today={today} />
            ))
          )}
        </div>

        <aside className="flex w-full max-w-[300px] flex-[1_1_280px] flex-col gap-3">
          <ThisWeek days={days} today={today} />
        </aside>
      </PageBody>

      {planning ? (
        <SessionForm date={today} onClose={() => setPlanning(false)} />
      ) : null}
    </>
  );
}

/**
 * Which of today's sessions to lead with.
 *
 * Still-to-do before already-happened, then the longer one: two sessions on
 * one day is a ride and a lift, and the one that has not been done yet is the
 * one the athlete opened the page for.
 */
function relevance(a: WeekSession, b: WeekSession): number {
  const pending = (session: WeekSession) =>
    session.status === "planned" ? 0 : 1;
  const byStatus = pending(a) - pending(b);
  if (byStatus !== 0) {
    return byStatus;
  }
  return (b.planned_duration_s ?? 0) - (a.planned_duration_s ?? 0);
}

function SessionPanel({
  session,
  today,
}: {
  session: WeekSession;
  today: string;
}) {
  const [editing, setEditing] = useState(false);
  const detail = $api.useQuery(
    "get",
    "/api/v1/planned-sessions/{planned_session_id}",
    { params: { path: { planned_session_id: session.id } } },
  );
  const { nameOf } = useExercises();

  const intent = detail.data?.intent;
  const structure = intent?.structure ?? null;
  // The session's own pins and the steps the backend resolved against them.
  // Nothing on this panel reaches for `/anchors/current`: the prescription was
  // frozen against these versions and must keep reading that way.
  const pinned = detail.data?.pinned_anchors ?? [];
  const resolvedSteps = detail.data?.resolved_steps ?? [];
  const headline = sessionHeadline({
    purpose: session.purpose,
    structure,
    plannedDurationS: session.planned_duration_s,
    totalSets: session.total_sets,
  });
  const bands = channelBands(structure);
  const legend = profileLegend(structure);
  const strength = structure?.discipline === "strength" ? structure : null;

  return (
    <>
      <Panel tone="card" className="overflow-hidden rounded-shell">
        <header className="flex flex-col gap-2.5 border-hairline border-b px-[22px] py-5">
          <div className="flex flex-wrap items-center gap-2.5">
            <PurposeBadge purpose={session.purpose} size="md" />
            <span className="font-mono text-ink-faint text-xs">
              {weekdayLabel(today)} {formatDayMonthYear(today)}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <StatusDot
                status={session.status}
                outline={session.status === "planned"}
              />
              <span className="font-mono text-ink-faint text-xs">
                {session.discipline === "strength"
                  ? formatSets(session.total_sets)
                  : `planned ${formatDurationHm(session.planned_duration_s)}`}
                {/* The prescription's own predicted load, when the backend
                    could compute one. Absent rather than zero when it could
                    not: a session with no power target has no TSS, and "0
                    TSS" would be a claim the arithmetic never made. */}
                {session.predicted_load !== null
                  ? ` · ${Math.round(session.predicted_load)} TSS`
                  : ""}
              </span>
            </span>
          </div>
          <h2 className="max-w-[760px] font-semibold text-4xl tracking-[-0.025em]">
            {headline}
          </h2>
          {intent?.intent_text ? (
            <p className="max-w-[640px] text-ink-muted text-lg leading-relaxed">
              {intent.intent_text}
            </p>
          ) : null}
        </header>

        {structure?.discipline === "cycling" ? (
          <div className="flex flex-col gap-2.5 px-[22px] py-5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <SectionLabel level={3}>Workout profile</SectionLabel>
              {legend.length > 0 ? (
                <ul className="flex flex-wrap gap-3 font-mono text-ink-faint text-2xs">
                  {legend.map((entry, index) => {
                    const span = resolveBand(entry, pinned, resolvedSteps);
                    return (
                      <li
                        // A zone can carry more than one band once a session
                        // prescribes two anchors on one channel; the list is
                        // replaced wholesale, so position is the identity.
                        // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                        key={index}
                        className="flex items-center gap-1.5"
                      >
                        <span
                          aria-hidden
                          className="size-2 rounded-[2px]"
                          style={{ backgroundColor: ZONE_COLORS[entry.zone] }}
                        />
                        {entry.zoneLabel}{" "}
                        {span ? describeSpan(span) : describePrescribed(entry)}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
            <WorkoutProfileBars structure={structure} size="detail" />
            <ResolvedStepList steps={resolvedSteps} />
            <AnchorProvenance anchors={pinned} />
          </div>
        ) : null}

        {strength ? (
          <div className="flex flex-col gap-2 px-[22px] py-5">
            <SectionLabel level={3}>Prescription</SectionLabel>
            {strength.groups.map((group, groupIndex) => (
              <div
                // Groups are ordered positions in the prescription, not entities.
                // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                key={groupIndex}
                className="flex flex-col gap-1 rounded-button border border-hairline-faint bg-inset px-3 py-2.5"
              >
                {group.items.length > 1 || group.label ? (
                  <SectionLabel>{group.label ?? "Superset"}</SectionLabel>
                ) : null}
                {group.items.map((item, itemIndex) => (
                  <div
                    // Lines are ordered positions in a group, not entities: the
                    // same movement twice is a legal prescription and the group
                    // is replaced wholesale.
                    // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                    key={itemIndex}
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    <span className="text-ink-secondary">
                      {nameOf(item.exercise_id)}
                    </span>
                    <span className="font-mono text-ink text-xs">
                      {item.sets}×{item.reps}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : null}
      </Panel>

      <div className="flex flex-wrap items-start gap-3.5">
        <Panel className="flex min-w-0 flex-[1_1_260px] flex-col gap-3 px-4 py-3.5">
          <SectionLabel level={3}>Targets</SectionLabel>
          {bands.length === 0 ? (
            <p className="text-ink-muted text-sm">
              {strength
                ? "This session is prescribed in sets and reps, not in channels."
                : "No channel targets on this prescription."}
            </p>
          ) : (
            <ul className="flex flex-col">
              {bands.map((band, index) => {
                const span = resolveBand(band, pinned, resolvedSteps);
                const prescribed = describePrescribed(band);
                return (
                  <li
                    // One channel can hold several bands — `85 % LTHR` and
                    // `75 % max HR` are two rows, never one average — so the
                    // channel is not a key. The list is replaced wholesale.
                    // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                    key={index}
                    className="flex items-center justify-between gap-3 border-hairline border-b py-2 last:border-b-0"
                  >
                    <span className="text-ink-secondary text-sm">
                      {channelLabel(band.channel)}
                    </span>
                    <span className="flex flex-col items-end">
                      <span className="font-mono font-medium text-base">
                        {span ? describeSpan(span) : prescribed}
                      </span>
                      {/* Both forms, always, when there are two of them: the
                          percentage is what survives the next FTP test. */}
                      {span ? (
                        <span className="font-mono text-2xs text-ink-faint">
                          {prescribed}
                        </span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="flex flex-col gap-1.5 border-hairline border-t pt-3">
            <SectionLabel>Success criteria</SectionLabel>
            {intent && intent.success_criteria.length > 0 ? (
              <ul className="flex flex-col gap-1.5">
                {intent.success_criteria.map((criterion, index) => (
                  <li
                    // Criteria are ordered values, not entities: two identical
                    // ones are the same rule twice, and the list is replaced
                    // wholesale on every intent version.
                    // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                    key={index}
                    className="flex items-start gap-2 text-ink-secondary text-sm"
                  >
                    <span
                      aria-hidden
                      className="mt-1.5 size-1 shrink-0 rounded-full bg-accent"
                    />
                    {describeCriterion(criterion)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted text-sm">
                {detail.isPending ? "…" : "No criteria on this session."}
              </p>
            )}
          </div>
        </Panel>

        <Panel className="flex min-w-0 flex-[1_1_300px] flex-col gap-2.5 px-4 py-3.5">
          <SectionLabel level={3}>Watch for</SectionLabel>
          {intent?.coach_notes ? (
            <p className="text-ink-secondary text-base leading-relaxed">
              {intent.coach_notes}
            </p>
          ) : (
            <p className="text-ink-muted text-sm">
              Nothing noted. Add what to watch for when you plan or revise the
              session.
            </p>
          )}
          <div className="mt-auto flex items-center gap-2 border-hairline border-t pt-3">
            <span className="font-mono text-2xs text-ink-faint">
              intent v{intent?.version ?? "—"}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="xs"
              className="ml-auto"
              onClick={() => setEditing(true)}
            >
              Edit session
            </Button>
          </div>
        </Panel>
      </div>

      {editing ? (
        <SessionForm
          date={today}
          sessionId={session.id}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </>
  );
}

/** A day with nothing planned is a decision, not a gap — say so. */
function RestDay({ onPlan }: { onPlan: () => void }) {
  return (
    <Panel
      tone="card"
      className="flex flex-col items-start gap-3 rounded-shell px-[22px] py-8"
    >
      <SectionLabel>Rest day</SectionLabel>
      <h2 className="font-semibold text-4xl tracking-[-0.025em]">
        Nothing planned today.
      </h2>
      <p className="max-w-[52ch] text-ink-muted text-lg leading-relaxed">
        Rest is prescribed as much as anything else is. If that is wrong, plan
        something — it will appear here and on the calendar.
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onPlan}>
          Plan a session
        </Button>
        <Button
          size="sm"
          variant="secondary"
          render={<Link href="/calendar">Open the week</Link>}
        />
      </div>
    </Panel>
  );
}

/** The week around today, as a list — the sidebar module from the mockup. */
function ThisWeek({
  days,
  today,
}: {
  days: readonly { date: string; sessions: readonly WeekSession[] }[];
  today: string;
}) {
  return (
    <Panel className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-2">
        <SectionLabel level={2}>This week</SectionLabel>
        <Link
          href="/calendar"
          className="text-accent text-xs hover:text-accent-hover"
        >
          Calendar
        </Link>
      </div>
      <ul className="flex flex-col gap-2">
        {days.map((day) => {
          const isToday = day.date === today;
          return (
            <li
              key={day.date}
              className={
                isToday
                  ? "-mx-1.5 flex flex-col gap-1 rounded-button bg-accent-wash px-1.5 py-1"
                  : "flex flex-col gap-1"
              }
            >
              {day.sessions.length === 0 ? (
                <span className="flex items-center gap-2.5">
                  <span
                    className={`w-[26px] shrink-0 font-mono text-xs ${
                      isToday ? "text-accent" : "text-ink-faint"
                    }`}
                  >
                    {weekdayLabel(day.date)}
                  </span>
                  <span className="text-ink-faint text-sm">Rest</span>
                </span>
              ) : (
                day.sessions.map((session) => (
                  <span key={session.id} className="flex items-center gap-2.5">
                    <span
                      className={`w-[26px] shrink-0 font-mono text-xs ${
                        isToday ? "text-accent" : "text-ink-faint"
                      }`}
                    >
                      {weekdayLabel(day.date)}
                    </span>
                    <StatusDot
                      status={session.status}
                      outline={isToday && session.status === "planned"}
                    />
                    <span
                      className={`flex-1 truncate text-sm ${
                        isToday ? "font-medium text-ink" : "text-ink-secondary"
                      }`}
                    >
                      {session.title ?? purposeLabel(session.purpose)}
                    </span>
                    <span className="font-mono text-ink-muted text-xs">
                      {session.discipline === "strength" &&
                      session.total_sets !== null
                        ? formatSets(session.total_sets)
                        : formatDurationHm(session.planned_duration_s)}
                    </span>
                  </span>
                ))
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
