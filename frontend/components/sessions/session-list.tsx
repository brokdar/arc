"use client";

import Link from "next/link";
import type * as React from "react";
import { useId, useState } from "react";

import { NotAssessed } from "@/components/design/not-assessed";
import { Pager } from "@/components/design/pager";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { DisciplineIcon } from "@/components/icons";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import {
  DISCIPLINE_LABELS,
  disciplineIconName,
  MATCH_STATUS_LABELS,
  MATCH_STATUS_REASONS,
  RECORDING_KIND_LABELS,
  type SessionDiscipline,
  type SessionListItem,
  type SessionMatchStatus,
} from "@/lib/activity";
import { $api } from "@/lib/api/client";
import { formatDayMonthYear, formatDurationHm } from "@/lib/format";
import {
  MATCH_LINK_LABELS,
  MATCH_LINK_REASONS,
  type MatchSummary,
} from "@/lib/matching";
import { LOAD_BASIS_LABELS } from "@/lib/metrics";

/** How many rows one page of the log holds. */
const PAGE = 25;

/**
 * The columns, once, so the header and every row are laid out by one string.
 *
 * A grid rather than a table because the load column is a *slot* that has no
 * value yet (UI convention 4): the position is reserved now and WP-5 fills it,
 * and a column that appears later would move every number one place to the
 * left on the day it arrives.
 */
const COLUMNS =
  "grid grid-cols-[92px_minmax(96px,1fr)_72px_72px_76px_96px] items-center gap-3";

/**
 * The session log: what actually happened, newest first.
 *
 * The opposite reading direction from the calendar, and deliberately — what is
 * planned is read forwards, what happened is read backwards. The filter is
 * local state rather than a query parameter of the page's own URL: a filtered
 * log is not a place you bookmark (UI convention 1), and the API takes the
 * discipline as a query parameter, so filtering happens on the server and
 * keeps working past the first page.
 */
export function SessionList() {
  const filterId = useId();
  const [discipline, setDiscipline] = useState<SessionDiscipline | "">("");
  const [offset, setOffset] = useState(0);

  const sessions = $api.useQuery("get", "/api/v1/sessions", {
    params: {
      query: {
        ...(discipline ? { discipline } : {}),
        offset,
        limit: PAGE,
      },
    },
  });

  const items = sessions.data?.items ?? [];
  const total = sessions.data?.total ?? 0;

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Sessions</h1>
        <span className="font-mono text-ink-muted text-sm">
          {sessions.data && total === 0 ? "none yet" : ""}
        </span>
      </Toolbar>

      <PageBody className="flex flex-col gap-3">
        {/* The range, the two steps and the filter on one line, from the
            shared pager — the log, the inbox queue and the anchor history all
            page the same way, and three hand-rolled copies of
            `Math.min(offset + items.length, total)` were three chances to get
            the last page's range wrong. */}
        <Pager
          heading="Log"
          subject="sessions"
          offset={offset}
          onPage={items.length}
          total={total}
          pageSize={PAGE}
          onOffsetChange={setOffset}
        >
          <label htmlFor={filterId} className="text-ink-muted text-xs">
            Discipline
          </label>
          <NativeSelect
            id={filterId}
            size="sm"
            value={discipline}
            onChange={(event) => {
              setDiscipline(event.target.value as SessionDiscipline | "");
              // Back to the first page: page three of "all" is not page three
              // of "cycling".
              setOffset(0);
            }}
          >
            <NativeSelectOption value="">All</NativeSelectOption>
            {(
              Object.keys(DISCIPLINE_LABELS) as readonly SessionDiscipline[]
            ).map((value) => (
              <NativeSelectOption key={value} value={value}>
                {DISCIPLINE_LABELS[value]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Pager>

        {sessions.isPending ? (
          <p className="text-ink-muted text-sm">Loading the log…</p>
        ) : sessions.error ? (
          <p role="alert" className="text-destructive text-sm">
            Could not load the log. Is the API reachable?
          </p>
        ) : items.length === 0 ? (
          <EmptyLog filtered={discipline !== ""} />
        ) : (
          <>
            <div
              className={`${COLUMNS} px-3.5 pb-1`}
              // The header is the grid's first row, not a row of the list:
              // announcing it as a list item would put a heading inside the
              // log a screen reader is walking.
              aria-hidden
            >
              <SectionLabel>Date</SectionLabel>
              <SectionLabel>Discipline</SectionLabel>
              <SectionLabel>Duration</SectionLabel>
              <SectionLabel>Load</SectionLabel>
              <SectionLabel>Source</SectionLabel>
              <SectionLabel>Plan</SectionLabel>
            </div>
            <ul className="flex flex-col gap-1">
              {items.map((session) => (
                <li key={session.id}>
                  <SessionRow session={session} />
                </li>
              ))}
            </ul>
          </>
        )}
      </PageBody>
    </>
  );
}

export interface SessionRowProps {
  readonly session: SessionListItem;
  /**
   * What goes in the load column, when a caller wants to override it.
   *
   * The row fills the column from the session's own current metric artefact.
   * The prop stays because the column is a **slot**: a row with no artefact
   * yet, or one whose load could not be computed from either model, renders
   * the reason rather than collapsing — a grid whose columns come and go is a
   * grid a returning eye cannot read (UI convention 4).
   */
  readonly load?: React.ReactNode;
}

/** One session, as a row of the log. */
export function SessionRow({ session, load }: SessionRowProps) {
  const icon = disciplineIconName(session.discipline);
  return (
    <Link
      href={`/sessions/${session.id}`}
      className={`${COLUMNS} rounded-card border border-hairline-card bg-card px-3.5 py-2.5 transition-colors hover:bg-card-hover focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2`}
    >
      <span className="font-mono text-ink text-sm">
        {formatDayMonthYear(session.local_date)}
      </span>

      <span className="flex min-w-0 items-center gap-1.5 text-ink-secondary text-sm">
        {icon ? <DisciplineIcon discipline={icon} size={12} /> : null}
        <span className="truncate">
          {DISCIPLINE_LABELS[session.discipline]}
        </span>
        {session.discipline_overridden ? (
          <span
            title="You corrected this discipline"
            className="rounded-badge border border-hairline px-1 text-2xs text-ink-faint"
          >
            edited
          </span>
        ) : null}
      </span>

      <span className="font-mono text-ink text-sm">
        {formatDurationHm(session.duration_s)}
      </span>

      <span className="font-mono text-sm">
        {load ?? <SessionLoad session={session} />}
      </span>

      <span className="text-ink-muted text-sm">
        {RECORDING_KIND_LABELS[session.recording_kind]}
      </span>

      <MatchBadge status={session.status} link={session.match} />
    </Link>
  );
}

/**
 * The load column: the number and which model produced it, or the reason.
 *
 * The basis is beside the number rather than only in a tooltip because a load
 * from heart rate and a load from power are not the same measurement (A5.2),
 * and a column that showed only the figure would invite comparing them.
 */
function SessionLoad({ session }: { session: SessionListItem }) {
  if (session.load === null || session.load_basis === null) {
    return (
      <NotAssessed reason="No training load: nothing has been computed for this session, or neither the power nor the heart-rate model could be" />
    );
  }
  return (
    <span className="flex items-baseline gap-1">
      {Math.round(session.load)}
      <span className="text-2xs text-ink-faint">
        {LOAD_BASIS_LABELS[session.load_basis]}
      </span>
    </span>
  );
}

/**
 * Where a session stands relative to the plan.
 *
 * Takes the state as a prop and looks it up: today the API produces exactly
 * one member, and hard-coding "Unmatched" here would make WP-6's first matched
 * session render a lie rather than a compile error.
 *
 * The **link** is a second input because the session's own status does not
 * carry the state that most needs an athlete: a pending proposal leaves the
 * session `unmatched` on purpose (D140 — a proposal is a question, and nothing
 * moves until it is answered), so a badge reading only the session would say
 * "Unmatched" about the one row on the page that is waiting for a click. When
 * a proposal is open the badge says so, in the accent, and every other state
 * is the session's own.
 */
export function MatchBadge({
  status,
  link,
}: {
  status: SessionMatchStatus;
  /** The link the session carries, when it carries one. */
  link?: MatchSummary | null;
}) {
  if (link?.status === "pending") {
    return (
      <span
        title={MATCH_LINK_REASONS.pending}
        className="justify-self-start rounded-badge border border-accent-border bg-accent-surface px-1.5 py-0.5 text-2xs text-accent"
      >
        {MATCH_LINK_LABELS.pending}
      </span>
    );
  }
  return (
    <span
      title={
        link
          ? `${MATCH_STATUS_REASONS[status]} — ${MATCH_LINK_REASONS[link.status]}`
          : MATCH_STATUS_REASONS[status]
      }
      className="justify-self-start rounded-badge border border-hairline px-1.5 py-0.5 text-2xs text-ink-muted"
    >
      {MATCH_STATUS_LABELS[status]}
    </span>
  );
}

/** An empty state names the missing input and the control that supplies it. */
function EmptyLog({ filtered }: { filtered: boolean }) {
  return (
    <Panel className="flex flex-col items-start gap-2.5 px-5 py-6">
      <SectionLabel level={2}>
        {filtered ? "Nothing in that discipline" : "No sessions yet"}
      </SectionLabel>
      <p className="max-w-[52ch] text-ink-muted text-base">
        {filtered
          ? "No session of that discipline has been recorded. Clear the filter to see the whole log."
          : "A session arrives from a device file. Drop FIT, TCX or GPX files into the inbox folder, or upload one from the inbox."}
      </p>
      {filtered ? null : (
        <Button size="sm" render={<Link href="/inbox">Open the inbox</Link>} />
      )}
    </Panel>
  );
}
