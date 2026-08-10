"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import type * as React from "react";
import { useEffect, useId, useState } from "react";

import { SessionCoachNotes } from "@/components/coach/coach-notes";
import { Field } from "@/components/design/field";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { DisciplineIcon } from "@/components/icons";
import { MatchPanel } from "@/components/sessions/match-panel";
import { ScoringPanel } from "@/components/sessions/scoring-panel";
import { SessionAnalysis } from "@/components/sessions/session-analysis";
import { MatchBadge } from "@/components/sessions/session-list";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  CLASSIFICATION_LABELS,
  DISCIPLINE_LABELS,
  disciplineIconName,
  RECORDING_KIND_LABELS,
  type Recording,
  SESSIONS_QUERY_PREFIX,
  type Session,
  type SessionDiscipline,
} from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { weekdayLabel } from "@/lib/dates";
import {
  formatDayMonthYear,
  formatDurationClock,
  formatDurationHm,
  localStamp,
} from "@/lib/format";
import { isUuid } from "@/lib/ids";

export interface SessionDetailProps {
  /** The id in the path. Checked before it is spent on a request. */
  readonly sessionId: string;
}

/**
 * One completed session: what it cost, what it looked like, and what it says
 * about its own numbers.
 *
 * The analysis comes first (WP-5): the header metrics, the stacked stream
 * charts and the intervals detected in them. Underneath it the WP-4 sections
 * stay, and they are not decoration — which meter produced the power and how
 * that was decided, what was subtracted from elapsed time to get the duration
 * the load was computed over, how irregular the file was, how many samples the
 * cleaner had to repair. A chart cannot say any of that, and every one of them
 * is a reason a number is the number.
 *
 * Every missing value holds its slot with the reason it is missing
 * (`NotAssessed`, UI convention 4) — a session with no power meter is not a
 * session that recorded zero watts.
 *
 * The mockup's right-hand column is filled in by work package: WP-7's
 * execution axes and the verdict the athlete declares on them are the
 * `ScoringPanel` below the link, and WP-8's coach evaluation is the
 * `SessionCoachNotes` under that — which draws nothing at all when no agent
 * has written about this session, because a stubbed panel would be a claim
 * about a session nothing has evaluated.
 */
export function SessionDetail({ sessionId }: SessionDetailProps) {
  const valid = isUuid(sessionId);
  const {
    data: session,
    isPending,
    error,
  } = $api.useQuery(
    "get",
    "/api/v1/sessions/{session_id}",
    { params: { path: { session_id: sessionId } } },
    { enabled: valid },
  );

  if (!valid || error) {
    return (
      <Missing
        detail={
          valid
            ? (apiErrorMessages(error)[0] ??
              "This link names a session that is not in the log.")
            : "That is not a session id."
        }
      />
    );
  }

  if (isPending || !session) {
    return (
      <>
        <Toolbar>
          <h1 className="font-semibold text-lg tracking-[-0.01em]">Session</h1>
        </Toolbar>
        <PageBody>
          <p className="text-ink-muted text-sm">Loading the session…</p>
        </PageBody>
      </>
    );
  }

  const icon = disciplineIconName(session.discipline);
  const start = localStamp(session.start_time, session.timezone);
  const end = localStamp(session.end_time, session.timezone);
  // Wall clock, and derived rather than read: `duration_s` is the *recording*
  // time for a device session (`app.api.routes.activity._duration`), so
  // printing it under "Duration" beside "Recording time" showed one number
  // twice in two formats and hid the pauses. End minus start is the other
  // number — and the two now differ by exactly the paused total the recording
  // panel prints below (D101: elapsed − recording = Σ stop rows).
  const elapsedS =
    (Date.parse(session.end_time) - Date.parse(session.start_time)) / 1000;

  return (
    <>
      <Toolbar>
        <Link
          href="/sessions"
          className="text-ink-muted text-sm underline-offset-2 hover:text-ink hover:underline"
        >
          Sessions
        </Link>
        <h1 className="flex items-center gap-2 font-semibold text-lg tracking-[-0.01em]">
          {icon ? <DisciplineIcon discipline={icon} size={14} /> : null}
          {DISCIPLINE_LABELS[session.discipline]}
        </h1>
        <span className="font-mono text-ink-muted text-sm">
          {weekdayLabel(session.local_date)}{" "}
          {formatDayMonthYear(session.local_date)}
        </span>
        <div className="ml-auto">
          <MatchBadge status={session.status} link={session.match} />
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-5">
        <SessionAnalysis
          sessionId={session.id}
          // `?? null` rather than the field itself: a response that omits the
          // key entirely — an older client, a hand-written fake — means the
          // same thing as an explicit null, and the page has an answer for
          // that. Crashing on a missing key would be a worse answer.
          metrics={session.metrics ?? null}
          hasRecording={session.recordings.length > 0}
        />

        {/* Directly under the numbers, and above the file's own account of
            itself: what a session *was* — the planned session it answered, or
            the fact that it answered none — is the first question asked of a
            ride that has just landed, and a pending proposal is the one thing
            on this page waiting on an answer. */}
        <MatchPanel session={session} />

        {/* Directly under the link, because the judgement is *about* the link:
            a session is scored against the prescription it answered, and the
            panel's own empty state is the sentence explaining that an
            unanswered proposal is a question rather than a link. */}
        <ScoringPanel session={session} />

        {/* And directly under the judgement, because it is a *second opinion*
            on it: the axes above are computed and the note below is written,
            and the coach's purple is what says which is which. Absent when
            the coach has said nothing (D183). */}
        <SessionCoachNotes sessionId={session.id} />

        <section className="flex flex-col gap-2.5">
          <SectionLabel level={2}>Session</SectionLabel>
          <Panel className="px-5 py-4">
            <MetricGrid>
              <Metric label="Local date">
                {formatDayMonthYear(session.local_date)}
              </Metric>
              <Metric label="Started">
                {start ? (
                  start.time
                ) : (
                  <NotAssessed reason="The stored timezone cannot be resolved" />
                )}
              </Metric>
              <Metric label="Ended">
                {end ? (
                  end.time
                ) : (
                  <NotAssessed reason="The stored timezone cannot be resolved" />
                )}
              </Metric>
              <Metric label="Timezone" mono={false}>
                {session.timezone}
              </Metric>
              <Metric label="Duration">{formatDurationHm(elapsedS)}</Metric>
              <Metric label="Recording time">
                {session.recording_time_s === null ? (
                  <NotAssessed reason="Entered by hand — there were no pauses to subtract" />
                ) : (
                  formatDurationClock(session.recording_time_s)
                )}
              </Metric>
              <Metric label="RPE">
                {session.rpe === null ? (
                  <NotAssessed reason="No RPE logged for this session" />
                ) : (
                  `${session.rpe}/10`
                )}
              </Metric>
              <Metric label="Source" mono={false}>
                {RECORDING_KIND_LABELS[session.recording_kind]}
              </Metric>
            </MetricGrid>
            <p className="mt-3.5 border-hairline border-t pt-3 text-ink-muted text-sm">
              Classified as{" "}
              {DISCIPLINE_LABELS[session.discipline].toLowerCase()} by{" "}
              {CLASSIFICATION_LABELS[session.classification_source]}
              {session.discipline_overridden ? " — you corrected this." : "."}
            </p>
          </Panel>
        </section>

        <section className="flex flex-col gap-2.5">
          <SectionLabel level={2}>Recording</SectionLabel>
          {session.recordings.length === 0 ? (
            <Panel className="px-5 py-4 text-ink-muted text-base">
              No device file: this session was entered by hand, so there are no
              sources, pauses or repairs to account for.
            </Panel>
          ) : (
            session.recordings.map((recording) => (
              <RecordingPanel key={recording.id} recording={recording} />
            ))
          )}
        </section>

        {session.recording_kind === "manual" ||
        session.logged_sets.length > 0 ? (
          <LoggedSets sets={session.logged_sets} />
        ) : null}

        <section className="flex flex-col gap-2.5">
          <SectionLabel level={2}>Notes</SectionLabel>
          <Panel className="px-5 py-4 text-base text-ink-secondary leading-relaxed">
            {session.notes ?? (
              <span className="text-ink-muted">
                Nothing was recorded about this session.
              </span>
            )}
          </Panel>
        </section>

        <Corrections session={session} />
      </PageBody>
    </>
  );
}

/** What a link that resolves to nothing says instead of nothing. */
function Missing({ detail }: { detail: string }) {
  return (
    <>
      <Toolbar>
        <Link
          href="/sessions"
          className="text-ink-muted text-sm underline-offset-2 hover:text-ink hover:underline"
        >
          Sessions
        </Link>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          Could not open this session
        </h1>
      </Toolbar>
      <PageBody>
        <Panel className="flex flex-col items-start gap-2.5 px-5 py-6">
          <p role="alert" className="max-w-[52ch] text-ink-muted text-base">
            {detail}
          </p>
          <Button
            size="sm"
            render={<Link href="/sessions">Back to the log</Link>}
          />
        </Panel>
      </PageBody>
    </>
  );
}

/** The metric grid: fixed positions, mono numerals. */
function MetricGrid({ children }: { children: React.ReactNode }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-4">
      {children}
    </dl>
  );
}

function Metric({
  label,
  mono = true,
  children,
}: {
  label: string;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt>
        <SectionLabel>{label}</SectionLabel>
      </dt>
      <dd
        className={`text-base text-ink ${mono ? "font-mono" : "text-ink-secondary"}`}
      >
        {children}
      </dd>
    </div>
  );
}

/**
 * One device file's account of the session.
 *
 * The paused total is **derived** from the stop ranges rather than read from a
 * field: a stop is a half-open row range on the 1 Hz grid (D89), so its length
 * in seconds is `end - start`, and their sum is exactly what separates elapsed
 * from recording time. Printing it is how the page shows its arithmetic
 * instead of asserting it.
 */
function RecordingPanel({ recording }: { recording: Recording }) {
  const paused = recording.recording_stops.reduce(
    (total, stop) => total + (stop.end_index - stop.start_index),
    0,
  );

  return (
    <Panel className="flex flex-col gap-3.5 px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <span className="font-medium text-base text-ink uppercase">
          {recording.original_ext.replace(".", "")}
        </span>
        <span
          title={`sha256 ${recording.file_hash}`}
          className="font-mono text-ink-faint text-sm"
        >
          {recording.file_hash.slice(0, 12)}
        </span>
        <span className="font-mono text-2xs text-ink-faint">
          sport {recording.file_sport_index}
          {recording.sport ? ` · ${recording.sport}` : ""}
        </span>
      </div>

      <MetricGrid>
        <Metric label="Elapsed">
          {formatDurationClock(recording.elapsed_time_s)}
        </Metric>
        <Metric label="Recording">
          {formatDurationClock(recording.recording_time_s)}
        </Metric>
        <Metric label="Moving">
          {formatDurationClock(recording.moving_time_s)}
        </Metric>
        <Metric label="Stops">
          {recording.recording_stops.length === 0
            ? "0"
            : `${recording.recording_stops.length} · ${formatDurationClock(paused)} paused`}
        </Metric>
        <Metric label="Sample gap">
          {`${recording.median_time_delta_s.toFixed(1)} s median`}
        </Metric>
        <Metric label="Repairs">{`${recording.anomaly_count}`}</Metric>
      </MetricGrid>

      <div className="flex flex-col gap-2.5 border-hairline border-t pt-3.5">
        <SourceLine
          channel="Power"
          source={recording.power_source}
          rule={recording.power_source_rule}
          candidates={recording.power_source_candidates}
          absent="No power in this recording"
        />
        <SourceLine
          channel="Heart rate"
          source={recording.hr_source}
          rule={recording.hr_source_rule}
          candidates={recording.hr_source_candidates}
          absent="No heart rate in this recording"
        />
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-hairline border-t pt-3.5">
        <SectionLabel className="mr-1">Channels</SectionLabel>
        {recording.channels.length === 0 ? (
          <NotAssessed reason="The file carried no channels" />
        ) : (
          recording.channels.map((channel) => (
            <span
              key={channel}
              className="rounded-badge border border-hairline px-1.5 py-0.5 font-mono text-2xs text-ink-muted"
            >
              {channel}
            </span>
          ))
        )}
      </div>
    </Panel>
  );
}

/**
 * Which meter produced one channel, and how that was decided.
 *
 * The rule is printed beside the source because FIT names every candidate and
 * nothing that chose between them (D96): "only candidate" and "the one the
 * ride was recorded with" are different claims, and a page that showed just
 * the winner would make them look like the same one.
 */
function SourceLine({
  channel,
  source,
  rule,
  candidates,
  absent,
}: {
  channel: string;
  source: string | null;
  rule: string | null;
  candidates: readonly string[];
  absent: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <SectionLabel className="w-[76px] shrink-0">{channel}</SectionLabel>
      {source === null ? (
        <NotAssessed reason={absent} />
      ) : (
        <>
          <span className="font-mono text-base text-ink">{source}</span>
          {rule ? (
            <span className="text-ink-muted text-sm">chosen: {rule}</span>
          ) : null}
          {candidates.length > 1 ? (
            <span className="font-mono text-2xs text-ink-faint">
              of {candidates.join(", ")}
            </span>
          ) : null}
        </>
      )}
    </div>
  );
}

/** The sets a hand-entered strength session recorded. */
function LoggedSets({ sets }: { sets: Session["logged_sets"] }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Logged sets</SectionLabel>
      {sets.length === 0 ? (
        <Panel className="px-5 py-4 text-ink-muted text-base">
          No sets were logged for this session.
        </Panel>
      ) : (
        <Panel className="overflow-hidden">
          <table className="w-full border-collapse text-base">
            <thead>
              <tr className="border-hairline border-b text-left">
                <th
                  scope="col"
                  className="w-[44px] px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                >
                  Set
                </th>
                <th
                  scope="col"
                  className="px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                >
                  Exercise
                </th>
                <th
                  scope="col"
                  className="w-[72px] px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                >
                  Reps
                </th>
                <th
                  scope="col"
                  className="w-[88px] px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                >
                  Load
                </th>
                <th
                  scope="col"
                  className="w-[64px] px-3.5 py-2 font-semibold text-ink-faint text-label uppercase tracking-[0.09em]"
                >
                  RIR
                </th>
              </tr>
            </thead>
            <tbody>
              {sets.map((set) => (
                <tr
                  key={set.id}
                  className="border-hairline-faint border-b last:border-b-0"
                >
                  <td className="px-3.5 py-2 font-mono text-ink-faint text-sm">
                    {set.set_index + 1}
                  </td>
                  <td className="px-3.5 py-2 text-ink-secondary text-sm">
                    {set.exercise_name}
                    {set.notes ? (
                      <span className="text-ink-faint"> · {set.notes}</span>
                    ) : null}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-ink text-sm">
                    {set.reps}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-ink text-sm">
                    {set.load_kg === null ? (
                      <NotAssessed reason="Bodyweight, or no load recorded" />
                    ) : (
                      `${set.load_kg} kg`
                    )}
                  </td>
                  <td className="px-3.5 py-2 font-mono text-ink text-sm">
                    {set.rir === null ? (
                      <NotAssessed reason="No reps-in-reserve recorded" />
                    ) : (
                      set.rir
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </section>
  );
}

/**
 * The two things about a session the athlete may correct.
 *
 * Both are overrides of something a file implied, and both are separate
 * submits rather than one Save: they are independent corrections, and a single
 * button would send a timezone nobody touched every time the discipline was
 * fixed. Each control disables itself until its own value actually differs —
 * the same shape the session sheet's move and copy pickers take.
 *
 * The timezone is a free text field because the backend accepts exactly three
 * forms and there are six hundred of the third; the hint names all three, and
 * an unresolvable one comes back as the API's own sentence rather than being
 * guessed at here.
 */
function Corrections({ session }: { session: Session }) {
  const base = useId();
  const queryClient = useQueryClient();
  const [discipline, setDiscipline] = useState<SessionDiscipline>(
    session.discipline,
  );
  const [timezone, setTimezone] = useState(session.timezone);
  const [saved, setSaved] = useState<string | null>(null);

  // A correction that landed is the new truth: follow the session rather than
  // keep the value that was typed, or the field would go on offering a change
  // that has already been made.
  useEffect(() => {
    setDiscipline(session.discipline);
  }, [session.discipline]);
  useEffect(() => {
    setTimezone(session.timezone);
  }, [session.timezone]);

  const detailKey = $api.queryOptions("get", "/api/v1/sessions/{session_id}", {
    params: { path: { session_id: session.id } },
  }).queryKey;

  const update = $api.useMutation("patch", "/api/v1/sessions/{session_id}", {
    onSuccess: (updated) => {
      // The PATCH answers with the whole session, so the page can show the
      // re-derived date immediately; the log behind it is a different key and
      // only knows it is stale.
      queryClient.setQueryData(detailKey, updated);
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_PREFIX });
      setSaved(
        `Saved — ${DISCIPLINE_LABELS[updated.discipline]} on ${formatDayMonthYear(updated.local_date)}.`,
      );
    },
  });

  const problems = apiErrorMessages(update.error);
  const path = { params: { path: { session_id: session.id } } };

  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Corrections</SectionLabel>
      <Panel className="flex flex-col gap-3.5 px-5 py-4">
        <p className="max-w-[62ch] text-ink-muted text-sm">
          Both of these correct what the file implied. Changing the timezone
          re-derives the session's date, which is what puts a late-evening ride
          back on the day it belongs to.
        </p>

        <form
          className="flex flex-wrap items-end gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            setSaved(null);
            update.mutate({ ...path, body: { discipline } });
          }}
        >
          <Field label="Discipline" htmlFor={`${base}-discipline`}>
            <NativeSelect
              id={`${base}-discipline`}
              value={discipline}
              onChange={(event) =>
                setDiscipline(event.target.value as SessionDiscipline)
              }
            >
              {(
                Object.keys(DISCIPLINE_LABELS) as readonly SessionDiscipline[]
              ).map((value) => (
                <NativeSelectOption key={value} value={value}>
                  {DISCIPLINE_LABELS[value]}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
          <Button
            type="submit"
            variant="secondary"
            disabled={discipline === session.discipline || update.isPending}
          >
            Set discipline
          </Button>
        </form>

        <form
          className="flex flex-wrap items-end gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            setSaved(null);
            update.mutate({ ...path, body: { timezone } });
          }}
        >
          <Field
            label="Timezone"
            hint="UTC, UTC+02:00, or Europe/Zurich"
            htmlFor={`${base}-timezone`}
            className="min-w-[220px] flex-1"
          >
            <Input
              id={`${base}-timezone`}
              value={timezone}
              className="font-mono"
              onChange={(event) => setTimezone(event.target.value)}
            />
          </Field>
          <Button
            type="submit"
            variant="secondary"
            disabled={
              timezone.trim() === "" ||
              timezone === session.timezone ||
              update.isPending
            }
          >
            Set timezone
          </Button>
        </form>

        {saved ? (
          <p role="status" className="text-sm text-status-completed">
            {saved}
          </p>
        ) : null}

        {problems.length > 0 ? (
          <ul
            role="alert"
            className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
          >
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}
      </Panel>
    </section>
  );
}
