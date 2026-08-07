"use client";

import Link from "next/link";
import type * as React from "react";
import { useId, useState } from "react";

import { NotAssessed } from "@/components/design/not-assessed";
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
  const last = Math.min(offset + items.length, total);

  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">Sessions</h1>
        <span className="font-mono text-ink-muted text-sm">
          {sessions.data
            ? total === 0
              ? "none yet"
              : `${offset + 1}–${last} of ${total}`
            : ""}
        </span>
        <div className="ml-auto flex items-center gap-2.5">
          <label htmlFor={filterId} className="text-ink-muted text-xs">
            Discipline
          </label>
          <NativeSelect
            id={filterId}
            size="sm"
            value={discipline}
            onChange={(event) => {
              setDiscipline(event.target.value as SessionDiscipline | "");
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
        </div>
      </Toolbar>

      <PageBody className="flex flex-col gap-3">
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
            <div className="flex items-center justify-end gap-1.5">
              <Button
                size="xs"
                variant="secondary"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Newer
              </Button>
              <Button
                size="xs"
                variant="secondary"
                disabled={last >= total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Older
              </Button>
            </div>
          </>
        )}
      </PageBody>
    </>
  );
}

export interface SessionRowProps {
  readonly session: SessionListItem;
  /**
   * What goes in the load column.
   *
   * A slot, not a number: training load over real streams is WP-5's, and until
   * it lands the column renders the *reason* there is nothing there rather
   * than collapsing — a grid whose columns come and go is a grid a returning
   * eye cannot read (UI convention 4).
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
        {load ?? <NotAssessed reason="Training load arrives with WP-5" />}
      </span>

      <span className="text-ink-muted text-sm">
        {RECORDING_KIND_LABELS[session.recording_kind]}
      </span>

      <MatchBadge status={session.status} />
    </Link>
  );
}

/**
 * Where a session stands relative to the plan.
 *
 * Takes the state as a prop and looks it up: today the API produces exactly
 * one member, and hard-coding "Unmatched" here would make WP-6's first matched
 * session render a lie rather than a compile error.
 */
export function MatchBadge({ status }: { status: SessionMatchStatus }) {
  return (
    <span
      title={MATCH_STATUS_REASONS[status]}
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
