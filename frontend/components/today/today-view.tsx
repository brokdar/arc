"use client";

import Link from "next/link";
import { useState } from "react";

import type { WeekSession } from "@/components/calendar/session-card";
import { TodayCard } from "@/components/cards/card";
import { RedFlagControl } from "@/components/coach/red-flag";
import { AnchorProvenance } from "@/components/design/anchor-provenance";
import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { ResolvedStepList } from "@/components/design/resolved-steps";
import { SectionLabel } from "@/components/design/section-label";
import { StatusDot } from "@/components/design/status-dot";
import { WorkoutProfileBars } from "@/components/design/workout-profile-bars";
import { SessionForm } from "@/components/plan/session-form";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { InstallHint } from "@/components/shell/install-hint";
import { OfflineReady } from "@/components/shell/offline-ready";
import {
  RecapLink,
  UnplannedLink,
} from "@/components/strength/session-edits/today-links";
import { StartLink } from "@/components/strength/start-link";
import { FirstRunQuestion } from "@/components/today/first-run";
import { OfflineSessions } from "@/components/today/offline-sessions";
import { WellnessCard } from "@/components/today/wellness-card";
import { Button } from "@/components/ui/button";
import { useExercises } from "@/components/workouts/exercise-catalogue";
import { $api } from "@/lib/api/client";
import { useAthleteTimezone } from "@/lib/clock";
import { describeCriterion } from "@/lib/criteria";
import { mondayOf, todayIsoDate } from "@/lib/dates";
import { formatDayMonthYear, formatDurationHm } from "@/lib/format";
import { type I18n, useI18n } from "@/lib/i18n/provider";
import { isUnreachable } from "@/lib/last-contact";
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
import { describeSets, ZONE_COLORS } from "@/lib/workout-profile";

/**
 * Today: what to do, why, and how it is judged.
 *
 * Narrower than the mockup's Today screen. Weather, TSB, TSS and the coach's
 * proposals all belong to work that does not exist yet, and drawing them from
 * nothing would make the page a picture of an application rather than the
 * application. What is here is everything the *plan* knows — the frozen
 * prescription, its targets, its criteria, and where the day sits in the week
 * — plus what the *athlete* has reported about their own morning.
 *
 * The wellness card is a record, not a verdict. It shows what was entered and
 * whether a declared confounder voided the device numbers; it does not score
 * readiness, and nothing on this page decides whether to train. That reading
 * is the coach's, and the baselines it would need do not exist yet.
 *
 * Every absolute number on this page comes from the session's own
 * `resolved_steps` — resolved by the backend against the anchor versions the
 * intent **pinned**, never against whatever anchor is in force now. The
 * page therefore says what the plan said on the day it was written, and the
 * provenance line under the profile says whose FTP that was.
 */
export function TodayView() {
  // The athlete's own clock (`/clock`), not the browser's — a page titled
  // "Today" that showed a different day from the one the backend raised the
  // wellness prompt for is exactly what issue #62 was about. Read once on
  // mount: re-deriving it mid-render would let a page left open overnight
  // disagree with itself.
  const timezone = useAthleteTimezone();
  const [today] = useState(() => todayIsoDate(timezone));
  const [start] = useState(() => mondayOf(today));
  const [planning, setPlanning] = useState(false);
  const { t, weekday } = useI18n();

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
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          {t("today.title")}
        </h1>
        <span className="font-mono text-ink-muted text-sm">
          {t("today.date", {
            weekday: weekday(today),
            date: formatDayMonthYear(today),
          })}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <RedFlagControl />
          <UnplannedLink />
          <Button size="sm" onClick={() => setPlanning(true)}>
            {t("today.plan")}
          </Button>
        </div>
      </Toolbar>

      <PageBody className="flex flex-wrap items-start gap-[18px]">
        {/* Today is the page the installed app opens on, offline included,
            so it is where the phone says whether it can. Full width, above
            both columns; each renders nothing when it has nothing to say. */}
        <div className="flex w-full flex-col gap-2 empty:hidden">
          <OfflineReady />
          <InstallHint />
        </div>
        <div className="flex min-w-0 flex-[1_1_620px] flex-col gap-3.5">
          {/* Above the session, and empty — not even a frame — when there is
              no card or a session is under way (`components/cards/card.tsx`). */}
          {/* Before the card: on a fresh install it is the first thing arc
              needs to know, and it is asked once (`first-run.tsx`). */}
          <FirstRunQuestion />
          <TodayCard />
          {week.isPending ? (
            <p className="text-ink-muted text-sm">{t("today.loading")}</p>
          ) : week.error ? (
            // No server answered: train from the packet the last sync stored
            // (`lib/offline/packet-store.ts`). A server that answered with an
            // error is still an error.
            isUnreachable(week.error) ? (
              <OfflineSessions
                today={today}
                fallback={<LoadFailed message={t("today.loadFailed")} />}
              />
            ) : (
              <LoadFailed message={t("today.loadFailed")} />
            )
          ) : ordered.length === 0 ? (
            <RestDay onPlan={() => setPlanning(true)} />
          ) : (
            ordered.map((session) => (
              <SessionPanel key={session.id} session={session} today={today} />
            ))
          )}
        </div>

        <aside className="flex w-full max-w-[300px] flex-[1_1_280px] flex-col gap-3">
          {/* Above the week, because the question this page answers first is
              "should I do the thing on it", and the athlete's own report of
              how they slept is the input to that — not a footnote under it. */}
          <WellnessCard today={today} />
          <ThisWeek days={days} today={today} />
        </aside>
      </PageBody>

      {planning ? (
        <SessionForm date={today} onClose={() => setPlanning(false)} />
      ) : null}
    </>
  );
}

function LoadFailed({ message }: { message: string }) {
  return (
    <p role="alert" className="text-destructive text-sm">
      {message}
    </p>
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
  const { t, weekday } = useI18n();
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
              {t("today.date", {
                weekday: weekday(today),
                date: formatDayMonthYear(today),
              })}
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <StatusDot
                status={session.status}
                outline={session.status === "planned"}
              />
              <span className="font-mono text-ink-faint text-xs">
                {session.discipline === "strength"
                  ? setCount(t, session.total_sets)
                  : t("today.planned", {
                      duration: formatDurationHm(session.planned_duration_s),
                    })}
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
          {/* The one way into the gym screen: a strength session still to
              do gets Start, and the tap on it is the whole of starting
              (`components/strength/logging-screen.tsx`). The prescription
              below stays read-only; logging happens there, not here. */}
          {session.discipline === "strength" && session.status === "planned" ? (
            <div className="flex flex-wrap gap-2">
              <StartLink id={session.id} name={headline} />
              <RecapLink id={session.id} name={headline} />
            </div>
          ) : null}
          {intent?.intent_text ? (
            <p className="max-w-[640px] text-ink-muted text-lg leading-relaxed">
              {intent.intent_text}
            </p>
          ) : null}
        </header>

        {structure?.discipline === "cycling" ? (
          <div className="flex flex-col gap-2.5 px-[22px] py-5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <SectionLabel level={3}>{t("today.profile")}</SectionLabel>
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
            <SectionLabel level={3}>{t("today.prescription")}</SectionLabel>
            {strength.groups.map((group, groupIndex) => (
              <div
                // Groups are ordered positions in the prescription, not entities.
                // biome-ignore lint/suspicious/noArrayIndexKey: positional by nature
                key={groupIndex}
                className="flex flex-col gap-1 rounded-button border border-hairline-faint bg-inset px-3 py-2.5"
              >
                {group.items.length > 1 || group.label ? (
                  <SectionLabel>
                    {group.label ?? t("today.superset")}
                  </SectionLabel>
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
                      {describeSets(item)}
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
          <SectionLabel level={3}>{t("today.targets")}</SectionLabel>
          {bands.length === 0 ? (
            <p className="text-ink-muted text-sm">
              {strength ? t("today.targetsStrength") : t("today.targetsNone")}
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
            <SectionLabel>{t("today.criteria")}</SectionLabel>
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
                {detail.isPending ? "…" : t("today.criteriaNone")}
              </p>
            )}
          </div>
        </Panel>

        {/*
          A plain Panel in the ordinary ink, not the violet coach tokens the
          mockup draws here. `--color-coach` marks text an *agent* wrote, so
          that computed findings and generated prose are never mistaken for one
          another; `SessionIntent.coach_notes` is written by the athlete when
          planning. Tinting it would spend the one signal that distinguishes
          agent output on the athlete's own words, and leave nothing to say it
          with when a real agent note lands on this page.
        */}
        <Panel className="flex min-w-0 flex-[1_1_300px] flex-col gap-2.5 px-4 py-3.5">
          <SectionLabel level={3}>{t("today.watchFor")}</SectionLabel>
          {intent?.coach_notes ? (
            <p className="text-ink-secondary text-base leading-relaxed">
              {intent.coach_notes}
            </p>
          ) : (
            <p className="text-ink-muted text-sm">{t("today.watchForNone")}</p>
          )}
          <div className="mt-auto flex items-center gap-2 border-hairline border-t pt-3">
            <span className="font-mono text-2xs text-ink-faint">
              {t("today.intentVersion", { version: intent?.version ?? "—" })}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="xs"
              className="ml-auto"
              onClick={() => setEditing(true)}
            >
              {t("today.edit")}
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
  const { t } = useI18n();
  return (
    <Panel
      tone="card"
      className="flex flex-col items-start gap-3 rounded-shell px-[22px] py-8"
    >
      <SectionLabel>{t("today.restDay")}</SectionLabel>
      <h2 className="font-semibold text-4xl tracking-[-0.025em]">
        {t("today.restTitle")}
      </h2>
      <p className="max-w-[52ch] text-ink-muted text-lg leading-relaxed">
        {t("today.restBody")}
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onPlan}>
          {t("today.plan")}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          render={<Link href="/calendar">{t("today.openWeek")}</Link>}
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
  const { t, weekday } = useI18n();
  return (
    <Panel className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-2">
        <SectionLabel level={2}>{t("today.thisWeek")}</SectionLabel>
        <Link
          href="/calendar"
          className="text-accent text-xs hover:text-accent-hover"
        >
          {t("today.calendar")}
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
                    {weekday(day.date)}
                  </span>
                  <span className="text-ink-faint text-sm">
                    {t("today.rest")}
                  </span>
                </span>
              ) : (
                day.sessions.map((session) => (
                  <span key={session.id} className="flex items-center gap-2.5">
                    <span
                      className={`w-[26px] shrink-0 font-mono text-xs ${
                        isToday ? "text-accent" : "text-ink-faint"
                      }`}
                    >
                      {weekday(day.date)}
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
                        ? setCount(t, session.total_sets)
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

/**
 * `3 sets` / `1 set` in the athlete's language — `formatSets`, translated.
 * An em dash when the count is unknown, so the slot keeps its shape.
 */
function setCount(t: I18n["t"], sets: number | null | undefined): string {
  if (sets === null || sets === undefined) return "—";
  return t(sets === 1 ? "units.setsOne" : "units.setsOther", { count: sets });
}
