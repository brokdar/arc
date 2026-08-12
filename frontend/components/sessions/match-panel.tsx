"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import type * as React from "react";
import { useState } from "react";

import { ConfirmButton, InlineConfirm } from "@/components/design/confirm";
import { NotAssessed } from "@/components/design/not-assessed";
import { Panel } from "@/components/design/panel";
import { PurposeBadge } from "@/components/design/purpose-badge";
import { SectionLabel } from "@/components/design/section-label";
import { DisciplineIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import {
  DISCIPLINE_LABELS,
  SESSIONS_QUERY_PREFIX,
  type Session,
} from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { addDays, mondayOf, weekdayLabel } from "@/lib/dates";
import { formatDayMonthYear, formatDurationHm } from "@/lib/format";
import {
  COMPONENT_DESCRIPTIONS,
  COMPONENT_LABELS,
  CONFIRMED_EXPLANATION,
  DISPLACED_EXPLANATION,
  describeScore,
  formatComponentValue,
  formatSimilarity,
  formatWeight,
  isDisplacementScore,
  MATCH_LINK_LABELS,
  MATCH_LINK_REASONS,
  MATCH_QUERY_PREFIX,
  MATCHES_QUERY_PREFIX,
  type Match,
  type MatchComponent,
  NO_SCORE_REASON,
  PLAN_WEEK_QUERY_PREFIX,
  PLANNED_SESSIONS_QUERY_PREFIX,
  type PlannedSessionListItem,
} from "@/lib/matching";
import { purposeTone } from "@/lib/purpose";

/**
 * How far either side of a session the picker looks for a planned session.
 *
 * Wider than the machine's window (`CANDIDATE_WINDOW_DAYS` is ±1) and
 * deliberately: the API does **not** check the date on a manual link, because
 * a manual link is the athlete overruling the machine and "that ride on
 * Saturday was Thursday's session" is a legitimate thing to say. Three days
 * covers a whole weekend's worth of that without turning the picker into the
 * plan.
 */
const LINK_WINDOW_DAYS = 3;

/** The order the three components are always shown in, whatever the API sent. */
const COMPONENT_ORDER: readonly MatchComponent[] = [
  "duration",
  "intensity",
  "structure",
];

/** The breakdown's columns, once, so the header and every row share a layout. */
const BREAKDOWN_COLUMNS =
  "grid grid-cols-[minmax(84px,1.2fr)_58px_58px_minmax(72px,1fr)_minmax(72px,1fr)] items-baseline gap-x-3 gap-y-1";

export interface MatchPanelProps {
  readonly session: Session;
}

/**
 * Where one recorded session stands against the plan, and what to do about it.
 *
 * The whole of WP-6's manual vocabulary lives here — confirm, reject, link,
 * swap, unlink, mark unplanned, merge — because they are all answers to one
 * question the athlete asks in one place: *was this the session I had planned?*
 *
 * Two things it refuses to do. It never states a similarity it was not given:
 * a score of `null` is "nothing could be compared", not zero, and the panel
 * says so rather than rendering 0%. And it never hides what a score was made
 * of: the components that could not be assessed are shown in their own slots
 * with the sentence the domain wrote about each, because a similarity of 60%
 * over one component is a different claim from 60% over three.
 */
export function MatchPanel({ session }: MatchPanelProps) {
  const link = session.match ?? null;
  const {
    data: match,
    isPending,
    error,
  } = $api.useQuery(
    "get",
    "/api/v1/matches/{match_id}",
    { params: { path: { match_id: link?.id ?? "" } } },
    { enabled: link !== null },
  );

  return (
    <section className="flex flex-col gap-2.5">
      <SectionLabel level={2}>Plan</SectionLabel>
      {link === null ? (
        <Unlinked session={session} />
      ) : error ? (
        <Panel className="px-5 py-4">
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(error, "this match")}
          </p>
        </Panel>
      ) : isPending || !match ? (
        <Panel className="px-5 py-4 text-ink-muted text-sm">
          Loading the match…
        </Panel>
      ) : (
        <Linked session={session} match={match} />
      )}
      <MergePanel session={session} />
    </section>
  );
}

/**
 * Invalidate everything a link is visible in.
 *
 * A match changes three resources at once — the session's own status, the
 * planned session's, and the week the calendar draws from them — so a mutation
 * that refreshed only what it POSTed to would leave the log and the calendar
 * showing the state before the click.
 */
function useRefresh(sessionId: string): () => void {
  const queryClient = useQueryClient();
  const detailKey = $api.queryOptions("get", "/api/v1/sessions/{session_id}", {
    params: { path: { session_id: sessionId } },
  }).queryKey;

  return () => {
    queryClient.invalidateQueries({ queryKey: detailKey });
    queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: MATCHES_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: MATCH_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: PLANNED_SESSIONS_QUERY_PREFIX });
    queryClient.invalidateQueries({ queryKey: PLAN_WEEK_QUERY_PREFIX });
  };
}

/** The whole panel's failures, in one place, phrased as the API phrased them. */
function Problems({ errors }: { errors: readonly unknown[] }) {
  const messages = errors.flatMap((error) => apiErrorMessages(error));
  if (messages.length === 0) {
    return null;
  }
  return (
    <ul
      role="alert"
      className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
    >
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

// --- a session that is linked -------------------------------------------------

/**
 * The link, its score, and the actions that revise it.
 *
 * A **pending** link is a question and is presented as one: the two answers
 * are the only prominent controls, and neither the session nor the planned
 * session has moved a millimetre until one of them is clicked.
 */
function Linked({ session, match }: { session: Session; match: Match }) {
  const refresh = useRefresh(session.id);
  const [swapping, setSwapping] = useState(false);
  const path = { params: { path: { match_id: match.id } } };

  const confirm = $api.useMutation(
    "post",
    "/api/v1/matches/{match_id}/confirm",
    {
      onSuccess: refresh,
    },
  );
  const reject = $api.useMutation("post", "/api/v1/matches/{match_id}/reject", {
    onSuccess: refresh,
  });
  const unlink = $api.useMutation("delete", "/api/v1/matches/{match_id}", {
    onSuccess: refresh,
  });
  const swap = $api.useMutation("patch", "/api/v1/matches/{match_id}", {
    onSuccess: () => {
      setSwapping(false);
      refresh();
    },
  });
  const busy =
    confirm.isPending || reject.isPending || unlink.isPending || swap.isPending;
  const pending = match.status === "pending";

  return (
    <Panel className="flex flex-col gap-3.5 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <LinkBadge status={match.status} />
        <span className="text-ink-secondary text-sm">
          {pending
            ? "arc thinks this session answered the planned session below. Nothing has changed until you say so."
            : match.status === "displaced"
              ? "Recorded as done instead of the planned session below."
              : "Linked to the planned session below."}
        </span>
      </div>

      <PlannedSide
        date={match.planned_session.date}
        purpose={match.planned_session.purpose}
        discipline={match.planned_session.discipline}
        intentText={match.planned_session.intent_text}
      />

      <Similarity breakdown={match.breakdown} score={match.similarity} />

      {match.status === "confirmed" && isDisplacementScore(match.similarity) ? (
        <DisplacementOffer match={match} />
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-hairline border-t pt-3.5">
        {pending ? (
          <>
            <Button disabled={busy} onClick={() => confirm.mutate(path)}>
              Yes, this was that session
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => reject.mutate(path)}
            >
              No, it was not
            </Button>
          </>
        ) : (
          <>
            {match.status === "auto_high" ? (
              <Button disabled={busy} onClick={() => confirm.mutate(path)}>
                Confirm this link
              </Button>
            ) : null}
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => setSwapping((open) => !open)}
            >
              {swapping ? "Cancel swap" : "Swap to another session"}
            </Button>
            <ConfirmButton
              label="Unlink"
              question="Unlink, putting both back as they were?"
              confirmLabel="Unlink"
              disabled={busy}
              onConfirm={() => unlink.mutate(path)}
            />
          </>
        )}
      </div>

      {pending ? (
        <p className="max-w-[68ch] text-ink-muted text-sm">
          Answering “no” leaves this session unplanned and puts the planned
          session back where it was — it does not delete anything.
        </p>
      ) : null}

      {swapping ? (
        <PlannedPicker
          session={session}
          heading="Point this link at another planned session"
          note="The one it is on now goes back to exactly the state it was in before, and the new one takes the link."
          excludeId={match.planned_session_id}
          busy={busy}
          actions={(planned) => (
            <Button
              size="xs"
              disabled={busy}
              onClick={() =>
                swap.mutate({
                  ...path,
                  body: { planned_session_id: planned.id },
                })
              }
            >
              Move link here
            </Button>
          )}
        />
      ) : null}

      <Problems
        errors={[confirm.error, reject.error, unlink.error, swap.error]}
      />
    </Panel>
  );
}

// --- a session that is not linked ---------------------------------------------

/**
 * What an unlinked session offers, which depends on whether that is a decision.
 *
 * `unmatched` and `unplanned` are the pair the API is careful about, so the
 * panel is too: the first means nothing has been decided — matching has not
 * run, or it ran and proposed nothing — and the second means the athlete (or
 * the machine) has said there was nothing on the calendar this could be. Both
 * name what is missing and the control that supplies it (UI convention 3).
 */
function Unlinked({ session }: { session: Session }) {
  const refresh = useRefresh(session.id);
  const [linking, setLinking] = useState(false);
  const path = { params: { path: { session_id: session.id } } };

  const rematch = $api.useMutation(
    "post",
    "/api/v1/sessions/{session_id}/rematch",
    {
      onSuccess: refresh,
    },
  );
  const unplanned = $api.useMutation(
    "post",
    "/api/v1/sessions/{session_id}/unplanned",
    { onSuccess: refresh },
  );
  // The link's own score is not shown here, and cannot be: the API computes a
  // similarity as part of *creating* a link and offers no way to score a
  // candidate on its own. What the link turned out to score is on the panel
  // this one is replaced by, the moment it succeeds.
  const create = $api.useMutation("post", "/api/v1/matches", {
    onSuccess: () => {
      setLinking(false);
      refresh();
    },
  });
  const busy = rematch.isPending || unplanned.isPending || create.isPending;
  const decided = session.status === "unplanned";
  const outcome = rematch.data;

  return (
    <Panel className="flex flex-col gap-3.5 px-5 py-4">
      <SectionLabel level={3}>
        {decided ? "Nothing was planned" : "Not linked to the plan"}
      </SectionLabel>
      <p className="max-w-[68ch] text-ink-muted text-base">
        {decided
          ? "This session stands on its own: nothing on the calendar was what it answered, so it is scored on its own terms."
          : "Nothing on the calendar is linked to this session yet. Run matching again if the numbers have changed since it was ingested, or say which planned session this was."}
      </p>

      {outcome ? (
        <p role="status" className="text-ink-secondary text-sm">
          {outcome.sticky
            ? "Left alone: this link is yours, and re-running matching never revises one you made."
            : outcome.match
              ? `Matched, at ${outcome.match.similarity === null ? "no comparable score" : formatSimilarity(outcome.match.similarity)}, against ${outcome.candidates} candidate${outcome.candidates === 1 ? "" : "s"}.`
              : `Nothing linked: ${outcome.candidates} planned session${outcome.candidates === 1 ? " was" : "s were"} in range, and none was close enough to propose.`}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          disabled={busy}
          onClick={() => setLinking((open) => !open)}
          variant={decided ? "secondary" : "default"}
        >
          {linking ? "Cancel" : "Link to a planned session"}
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => rematch.mutate(path)}
        >
          Run matching again
        </Button>
        {decided ? null : (
          <Button
            variant="ghost"
            className="text-ink-muted"
            disabled={busy}
            onClick={() => unplanned.mutate(path)}
          >
            Nothing was planned
          </Button>
        )}
      </div>

      {linking ? (
        <PlannedPicker
          session={session}
          heading="Which planned session was this?"
          note="Linking by hand is sticky: no later run of matching will revise or remove it."
          busy={busy}
          actions={(planned) => (
            <>
              <Button
                size="xs"
                disabled={busy}
                title={CONFIRMED_EXPLANATION}
                onClick={() =>
                  create.mutate({
                    body: {
                      session_id: session.id,
                      planned_session_id: planned.id,
                      displaced: false,
                    },
                  })
                }
              >
                This was it
              </Button>
              <Button
                size="xs"
                variant="secondary"
                disabled={busy}
                title={DISPLACED_EXPLANATION}
                onClick={() =>
                  create.mutate({
                    body: {
                      session_id: session.id,
                      planned_session_id: planned.id,
                      displaced: true,
                    },
                  })
                }
              >
                Done instead of this
              </Button>
            </>
          )}
        >
          <dl className="flex flex-col gap-1.5 text-sm">
            <div>
              <dt className="inline font-medium text-ink-secondary">
                This was it —{" "}
              </dt>
              <dd className="inline text-ink-muted">{CONFIRMED_EXPLANATION}</dd>
            </div>
            <div>
              <dt className="inline font-medium text-ink-secondary">
                Done instead of this —{" "}
              </dt>
              <dd className="inline text-ink-muted">{DISPLACED_EXPLANATION}</dd>
            </div>
          </dl>
        </PlannedPicker>
      ) : null}

      <Problems errors={[rematch.error, unplanned.error, create.error]} />
    </Panel>
  );
}

/**
 * A confirmed link that scored below what arc would have proposed at, named.
 *
 * The similarity cannot be shown *before* a link is made — the API computes it
 * as part of creating one, and offers no way to score a candidate on its own —
 * so this is where a low score gets said out loud, with the reading that
 * usually fits it. A 32% link is rarely "the session, ridden badly"; it is
 * usually "I trained, and it was not this", which is a different fact about
 * the planned session and a different way of scoring the ride (WP-6.4).
 *
 * It stands as long as the link does rather than only just after it was made:
 * the offer is about what the link *is*, and a page reloaded tomorrow should
 * make the same offer it made today.
 */
function DisplacementOffer({ match }: { match: Match }) {
  const refresh = useRefresh(match.session_id);
  // Two calls rather than one, because the API has no "change this link's
  // kind": `displaced` is decided when a link is created (`MatchCreate`), so
  // converting one means taking it off and putting the other on. The second is
  // chained off the first's success, so a failed unlink cannot leave two —
  // and the unlink refreshes on its own success, so a failed *create* leaves
  // the panel showing the true intermediate state (unlinked) rather than a
  // link that no longer exists.
  const unlink = $api.useMutation("delete", "/api/v1/matches/{match_id}", {
    onSuccess: refresh,
  });
  const create = $api.useMutation("post", "/api/v1/matches", {
    onSuccess: refresh,
  });

  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-card border border-warn-border bg-warn-surface px-3.5 py-2.5"
    >
      <p className="max-w-[68ch] text-ink-secondary text-sm">
        This link scored{" "}
        <span className="font-mono">
          {match.similarity === null
            ? "no comparable score"
            : formatSimilarity(match.similarity)}
        </span>
        , low enough that arc would not have proposed it on its own.{" "}
        {DISPLACED_EXPLANATION}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={unlink.isPending || create.isPending}
          onClick={() =>
            unlink.mutate(
              { params: { path: { match_id: match.id } } },
              {
                onSuccess: () =>
                  create.mutate({
                    body: {
                      session_id: match.session_id,
                      planned_session_id: match.planned_session_id,
                      displaced: true,
                    },
                  }),
              },
            )
          }
        >
          Record it as done instead
        </Button>
      </div>
      <Problems errors={[unlink.error, create.error]} />
    </div>
  );
}

// --- the two sides, rendered --------------------------------------------------

/** The planned session a link points at, and a way back to the week it sits in. */
function PlannedSide({
  date,
  purpose,
  discipline,
  intentText,
}: {
  date: string;
  purpose: PlannedSessionListItem["intent"]["purpose"];
  discipline: PlannedSessionListItem["discipline"];
  intentText: string | null;
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-hairline-faint bg-inset px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <DisciplineIcon discipline={discipline} size={12} />
        <span className="font-mono text-ink text-sm">
          {weekdayLabel(date)} {formatDayMonthYear(date)}
        </span>
        <PurposeBadge purpose={purpose} />
        <Link
          href={`/calendar?week=${mondayOf(date)}`}
          className="ml-auto text-accent text-sm underline-offset-2 hover:underline"
        >
          Open the week
        </Link>
      </div>
      <p className="text-base text-ink-secondary">
        {intentText ?? (
          <span className="text-ink-muted">
            No intent was written for this session.
          </span>
        )}
      </p>
    </div>
  );
}

/** The score, what it is made of, and what the number would have meant. */
function Similarity({
  breakdown,
  score,
}: {
  breakdown: Match["breakdown"];
  score: number | null;
}) {
  const assessed = new Map(
    breakdown.components.map((part) => [part.component, part] as const),
  );
  const absent = new Map(
    breakdown.not_assessed.map((part) => [part.component, part] as const),
  );

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <SectionLabel>Similarity</SectionLabel>
        <span className="font-mono text-ink text-lg">
          {score === null ? (
            <NotAssessed reason={NO_SCORE_REASON} symbol="?" />
          ) : (
            formatSimilarity(score)
          )}
        </span>
        <span className="max-w-[54ch] text-ink-muted text-sm">
          {describeScore(score)}
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className={`${BREAKDOWN_COLUMNS} px-0.5`} aria-hidden>
          <SectionLabel>Component</SectionLabel>
          <SectionLabel>Score</SectionLabel>
          <SectionLabel>Counted</SectionLabel>
          <SectionLabel>Prescribed</SectionLabel>
          <SectionLabel>Recorded</SectionLabel>
        </div>
        <ul className="flex flex-col gap-1.5">
          {COMPONENT_ORDER.map((component) => {
            const part = assessed.get(component);
            const missing = absent.get(component);
            return (
              <li
                key={component}
                className="flex flex-col gap-0.5 border-hairline-faint border-t pt-1.5 first:border-t-0 first:pt-0"
              >
                <div className={BREAKDOWN_COLUMNS}>
                  <span className="flex min-w-0 flex-col">
                    <span className="text-ink-secondary text-sm">
                      {COMPONENT_LABELS[component]}
                    </span>
                    <span className="text-2xs text-ink-faint">
                      {part?.basis ?? COMPONENT_DESCRIPTIONS[component]}
                    </span>
                  </span>
                  <span className="font-mono text-ink text-sm">
                    {part ? (
                      formatSimilarity(part.score)
                    ) : (
                      <NotAssessed
                        reason={
                          missing?.reason ??
                          "This component is not in the stored breakdown"
                        }
                        symbol="?"
                      />
                    )}
                  </span>
                  {/* The weight actually applied, not the nominal one: when a
                      component is left out the others are scaled up to cover
                      it, and a column showing 40/30/30 against a score made of
                      two of them would be describing a different sum. */}
                  <span className="font-mono text-ink-muted text-sm">
                    {part ? (
                      formatWeight(part.weight)
                    ) : (
                      <NotAssessed
                        reason={`Left out of the score; its ${formatWeight(
                          missing?.nominal_weight ??
                            breakdown.weights[component] ??
                            0,
                        )} was shared out over the components that could be assessed`}
                      />
                    )}
                  </span>
                  <span className="font-mono text-ink text-sm">
                    {part ? (
                      formatComponentValue(component, part.basis, part.planned)
                    ) : (
                      <NotAssessed
                        reason={missing?.reason ?? "Not compared"}
                        symbol="?"
                      />
                    )}
                  </span>
                  <span className="font-mono text-ink text-sm">
                    {part ? (
                      formatComponentValue(component, part.basis, part.actual)
                    ) : (
                      <NotAssessed
                        reason={missing?.reason ?? "Not compared"}
                        symbol="?"
                      />
                    )}
                  </span>
                </div>
                {missing ? (
                  <p className="max-w-[68ch] text-ink-muted text-xs">
                    Not compared: {missing.reason}.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// --- picking a planned session ------------------------------------------------

interface PlannedPickerProps {
  readonly session: Session;
  readonly heading: string;
  readonly note: string;
  /** The planned session already on the link, which cannot be its own target. */
  readonly excludeId?: string;
  readonly busy: boolean;
  readonly actions: (planned: PlannedSessionListItem) => React.ReactNode;
  /** Copy shown above the list — the explanation of what the actions mean. */
  readonly children?: React.ReactNode;
}

/**
 * The planned sessions near this one that are free to be linked.
 *
 * Filtered to what the API would actually accept: a planned session that
 * already carries a link answers a link attempt with a 409, and one in the
 * other discipline with a 422 — so offering either would be offering a refusal.
 * A session recorded as `other` can be linked to nothing at all until its
 * discipline is corrected, and the empty state says exactly that rather than
 * "no candidates".
 */
function PlannedPicker({
  session,
  heading,
  note,
  excludeId,
  busy,
  actions,
  children,
}: PlannedPickerProps) {
  const start = addDays(session.local_date, -LINK_WINDOW_DAYS);
  const end = addDays(session.local_date, LINK_WINDOW_DAYS);
  const planned = $api.useQuery("get", "/api/v1/planned-sessions", {
    params: { query: { start, end, limit: 50 } },
  });

  const linkable = session.discipline === "other" ? null : session.discipline;
  const candidates = (planned.data?.items ?? []).filter(
    (item) =>
      item.id !== excludeId &&
      item.discipline === linkable &&
      (item.match ?? null) === null,
  );

  return (
    <div
      data-slot="planned-picker"
      className="flex flex-col gap-2.5 rounded-card border border-hairline-card bg-inset px-3.5 py-3"
    >
      <SectionLabel level={3}>{heading}</SectionLabel>
      <p className="max-w-[68ch] text-ink-muted text-sm">{note}</p>
      {children}

      {planned.isPending ? (
        <p className="text-ink-muted text-sm">Loading the plan…</p>
      ) : planned.error ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(planned.error, "the plan")}
        </p>
      ) : linkable === null ? (
        <p className="max-w-[68ch] text-ink-muted text-sm">
          This session is classified as {DISCIPLINE_LABELS.other.toLowerCase()},
          which is not a discipline anything is planned in. Correct its
          discipline below and it can be linked.
        </p>
      ) : candidates.length === 0 ? (
        <p className="max-w-[68ch] text-ink-muted text-sm">
          Nothing free to link to between{" "}
          <span className="font-mono">{formatDayMonthYear(start)}</span> and{" "}
          <span className="font-mono">{formatDayMonthYear(end)}</span>. Plan a
          session on the calendar, or unlink the one that is already taken.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {candidates.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center gap-2.5 rounded-card border border-hairline-faint bg-card px-3 py-2"
            >
              <span className="font-mono text-ink text-sm">
                {weekdayLabel(item.date)} {formatDayMonthYear(item.date)}
              </span>
              <PurposeBadge purpose={item.intent.purpose} />
              <span className="min-w-0 flex-1 truncate text-ink-secondary text-sm">
                {item.intent.intent_text ??
                  purposeTone(item.intent.purpose).label}
              </span>
              <span className="font-mono text-ink-muted text-sm">
                {item.intent.summary.total_sets === null
                  ? formatDurationHm(item.intent.summary.total_duration_s)
                  : `${item.intent.summary.total_sets} sets`}
              </span>
              <span className="flex items-center gap-1.5">{actions(item)}</span>
            </li>
          ))}
        </ul>
      )}
      {busy ? <p className="sr-only">Working…</p> : null}
    </div>
  );
}

// --- merging two recordings of one ride ---------------------------------------

/**
 * The garage-door case (WP-6.5): one ride, two files, two sessions.
 *
 * Only offered where it could work — a session with no recording has nothing
 * to merge — and only over the sessions it could work *with*: the same day,
 * the same discipline, and a device file of their own. The confirmation says
 * what the operation does to both rows, because it is the one action here that
 * removes something: the absorbed session row goes, and only its recordings
 * survive on this one.
 */
function MergePanel({ session }: { session: Session }) {
  const refresh = useRefresh(session.id);
  const [chosen, setChosen] = useState<string | null>(null);

  const sameDay = $api.useQuery("get", "/api/v1/sessions", {
    params: {
      query: {
        start: session.local_date,
        end: session.local_date,
        discipline: session.discipline,
        limit: 50,
      },
    },
  });
  const merge = $api.useMutation(
    "post",
    "/api/v1/sessions/{session_id}/merge",
    {
      onSuccess: () => {
        setChosen(null);
        refresh();
      },
    },
  );

  if (session.recordings.length === 0) {
    return null;
  }

  const others = (sameDay.data?.items ?? []).filter(
    (item) => item.id !== session.id && item.recording_kind === "device",
  );
  const selected = others.find((item) => item.id === chosen) ?? null;

  return (
    <Panel className="flex flex-col gap-3 px-5 py-4">
      <SectionLabel level={3}>One ride recorded twice</SectionLabel>
      <p className="max-w-[68ch] text-ink-muted text-base">
        A head unit stopped and restarted leaves two files and half a ride each.
        Merging keeps both recordings and folds them into this session, so the
        metrics and the match are computed over the whole ride.
      </p>

      {others.length === 0 ? (
        <p className="max-w-[68ch] text-ink-muted text-sm">
          No other device session was recorded on{" "}
          <span className="font-mono">
            {formatDayMonthYear(session.local_date)}
          </span>
          , so there is nothing to merge into this one. Upload the second file
          from the inbox first.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {others.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center gap-2.5 rounded-card border border-hairline-faint bg-inset px-3 py-2"
            >
              <Link
                href={`/sessions/${item.id}`}
                className="font-mono text-accent text-sm underline-offset-2 hover:underline"
              >
                {formatDayMonthYear(item.local_date)}
              </Link>
              <span className="text-ink-secondary text-sm">
                {DISCIPLINE_LABELS[item.discipline]}
              </span>
              <span className="font-mono text-ink text-sm">
                {formatDurationHm(item.duration_s)}
              </span>
              <Button
                size="xs"
                variant="secondary"
                className="ml-auto"
                disabled={merge.isPending}
                onClick={() => setChosen(item.id)}
              >
                Merge into this session
              </Button>
            </li>
          ))}
        </ul>
      )}

      {selected ? (
        <InlineConfirm
          question={`Merge the ${formatDurationHm(
            selected.duration_s,
          )} session from ${formatDayMonthYear(
            selected.local_date,
          )} into this one? Its recordings move here and its own session row is removed; this session's times widen to cover both and its metrics are recomputed.`}
          confirmLabel="Merge them"
          cancelLabel="Keep them separate"
          disabled={merge.isPending}
          onCancel={() => setChosen(null)}
          onConfirm={() =>
            merge.mutate({
              params: { path: { session_id: session.id } },
              body: { absorbed_session_id: selected.id },
            })
          }
        />
      ) : null}

      <Problems errors={[merge.error]} />
    </Panel>
  );
}

// --- the badge ----------------------------------------------------------------

/** What one link claims, as a chip. */
export function LinkBadge({
  status,
  className,
}: {
  status: Match["status"];
  className?: string;
}) {
  const attention = status === "pending";
  return (
    <span
      title={MATCH_LINK_REASONS[status]}
      className={`justify-self-start rounded-badge border px-1.5 py-0.5 text-2xs ${
        attention
          ? "border-accent-border bg-accent-surface text-accent"
          : "border-hairline text-ink-muted"
      } ${className ?? ""}`}
    >
      {MATCH_LINK_LABELS[status]}
    </span>
  );
}
