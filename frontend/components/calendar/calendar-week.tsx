"use client";

import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";

import {
  PlanStateBanner,
  PlanStateToggle,
} from "@/components/calendar/plan-state";
import type { WeekSession } from "@/components/calendar/session-card";
import { SessionSheet } from "@/components/calendar/session-sheet";
import { WeekGrid } from "@/components/calendar/week-grid";
import { WeekRail } from "@/components/calendar/week-rail";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import { SessionForm } from "@/components/plan/session-form";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import {
  addDays,
  isIsoDate,
  isoWeekNumber,
  mondayOf,
  todayIsoDate,
} from "@/lib/dates";
import {
  formatDayMonth,
  formatDayMonthYear,
  formatDurationHm,
} from "@/lib/format";
import { moveSessionInWeek, type PlanWeek } from "@/lib/plan-week";

/** Every cached week, whichever `start` it was fetched with. */
const WEEK_QUERY_PREFIX = ["get", "/api/v1/plan/week"] as const;

/**
 * The calendar week: seven columns, drag to move, click for the full session.
 *
 * The client owns which week is shown — it computes Monday starts and passes
 * `start=` — because the endpoint takes whatever date it is given literally
 * (D55). Everything else is the server's: this component holds no copy of the
 * plan beyond react-query's cache.
 *
 * **Which week is shown lives in the URL** (`/calendar?week=2026-08-03`), not
 * in component state: it is the one thing on this page a person would bookmark
 * or send to someone, and state that survives a reload has to be addressable
 * (UI convention 1). The param is taken literally, not snapped to a Monday —
 * the same rule the endpoint follows (D55) — so a link to a Wednesday shows
 * the seven days from that Wednesday. Anything unreadable, and anything at
 * all missing, means this week (D77).
 *
 * **No mutation fails quietly.** A move that the server refuses rolls the grid
 * back *and* says so in a strip on the page; a delete keeps the sheet open
 * with the refusal in it. A card that silently reappeared where it started
 * would read as a bug in the drag, and a session that silently survived being
 * deleted is worse than one that could not be deleted at all.
 */
export function CalendarWeek() {
  // Read once, on mount. `todayIsoDate()` is the *browser's* today, and
  // re-reading it mid-render would let a page left open overnight disagree
  // with itself.
  const [today] = useState(todayIsoDate);
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const thisWeek = mondayOf(today);
  const requested = searchParams.get("week");
  const start = isIsoDate(requested) ? requested : thisWeek;

  /**
   * Show `next`, by moving the address bar.
   *
   * `window.history.replaceState` rather than `router.replace`, for two
   * reasons that happen to agree.
   *
   * The choice: **replace, not push**. Stepping a week adjusts the view of one
   * page; pushing would make the back button mean "undo one of my last eleven
   * clicks" instead of "leave the calendar", eleven entries deep after a
   * minute of paging. The URL is a real address either way — a bookmark and a
   * shared link both work — so only the history stack is at stake, and that is
   * what replacing protects.
   *
   * The constraint: `router.replace` and `router.push` **cannot drop a search
   * param** in this Next major. Navigating from `/calendar?week=…` to
   * `/calendar` is a silent no-op (verified against a production build), which
   * would strand "This week" on whatever week was last shown. The native
   * History API is Next's own documented escape hatch for updating the URL
   * without navigating, and it syncs `usePathname` / `useSearchParams`, so the
   * component still re-renders off the address bar.
   *
   * This week is the bare `/calendar`, never `?week=<this monday>`: a URL
   * whose meaning is "the week I am in" is still right tomorrow, so the
   * address someone bookmarks does not quietly become last week's.
   *
   * Every *other* param is carried through untouched. The week is one facet of
   * this page's address and rebuilding the query string from it alone would
   * silently drop whatever the next facet turns out to be.
   */
  const showWeek = (next: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next === thisWeek) {
      params.delete("week");
    } else {
      params.set("week", next);
    }
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      query ? `${pathname}?${query}` : pathname,
    );
  };

  const [openSession, setOpenSession] = useState<WeekSession | null>(null);
  // The plan form is one component in two modes: `{ date }` plans a new
  // session on that day, `{ date, sessionId }` revises an existing one.
  const [planning, setPlanning] = useState<{
    date: string;
    sessionId?: string;
  } | null>(null);
  /** A refused move, kept until the athlete dismisses it. */
  const [moveFailure, setMoveFailure] = useState<readonly string[] | null>(
    null,
  );
  /** The date a copy landed on, for the confirmation in the sheet. */
  const [copiedTo, setCopiedTo] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const weekInit = { params: { query: { start } } };
  const weekKey = $api.queryOptions(
    "get",
    "/api/v1/plan/week",
    weekInit,
  ).queryKey;

  const week = $api.useQuery("get", "/api/v1/plan/week", weekInit, {
    // Paging a week keeps the week you were looking at on screen until the
    // next one arrives. Without this the grid unmounts to "Loading the week…"
    // on every click of the arrows, which on a fast connection is a flash of
    // nothing and on a slow one is the page disappearing under the cursor.
    placeholderData: keepPreviousData,
  });
  // True while showing a week the server has not confirmed yet: the previous
  // week's data, kept deliberately. Said with opacity rather than a spinner —
  // the numbers are real, they are just not this week's yet.
  const stale = week.isPlaceholderData;

  const invalidateWeeks = () =>
    queryClient.invalidateQueries({ queryKey: WEEK_QUERY_PREFIX });

  const move = $api.useMutation(
    "post",
    "/api/v1/planned-sessions/{planned_session_id}/move",
    {
      // Dragging a card should land where it was dropped, not a request later.
      onMutate: async (variables) => {
        setMoveFailure(null);
        await queryClient.cancelQueries({ queryKey: weekKey });
        const previous = queryClient.getQueryData<PlanWeek>(weekKey);
        if (previous) {
          queryClient.setQueryData<PlanWeek>(
            weekKey,
            moveSessionInWeek(
              previous,
              variables.params.path.planned_session_id,
              variables.body.date,
            ),
          );
        }
        return { previous };
      },
      onError: (error, _variables, context) => {
        if (context?.previous) {
          queryClient.setQueryData(weekKey, context.previous);
        }
        // The rollback alone is indistinguishable from a drag that did not
        // take: the card slides back and nothing says why.
        setMoveFailure(apiErrorMessages(error));
      },
      // Both weeks are stale when a card leaves this one, so drop them all.
      onSettled: invalidateWeeks,
    },
  );

  const copy = $api.useMutation(
    "post",
    "/api/v1/planned-sessions/{planned_session_id}/copy",
    { onSuccess: invalidateWeeks },
  );

  const remove = $api.useMutation(
    "delete",
    "/api/v1/planned-sessions/{planned_session_id}",
    { onSuccess: invalidateWeeks },
  );

  function moveSession(sessionId: string, toDate: string) {
    move.mutate({
      params: { path: { planned_session_id: sessionId } },
      body: { date: toDate },
    });
  }

  const end = addDays(start, 6);
  const busy = move.isPending || copy.isPending || remove.isPending;
  // Copy and delete are the two actions that keep the sheet open, so their
  // refusals belong in it rather than behind it.
  const sheetProblems = apiErrorMessages(remove.error ?? copy.error);

  /**
   * The day "Plan a session" opens on: today, when today is on screen.
   *
   * Paging to a week and planning into it should not silently write the
   * session into *this* week — the athlete is looking at October and the card
   * would appear nowhere they can see.
   */
  const planningDate = today >= start && today <= end ? today : start;

  return (
    <>
      <Toolbar>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Previous week"
            onClick={() => showWeek(addDays(start, -7))}
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Next week"
            onClick={() => showWeek(addDays(start, 7))}
          >
            <ChevronRightIcon />
          </Button>
        </div>
        <div className="flex items-baseline gap-2.5">
          <span className="font-semibold text-lg tracking-[-0.01em]">
            Week {isoWeekNumber(start)}
          </span>
          <span className="font-mono text-ink-muted text-sm">
            {formatDayMonth(start)} – {formatDayMonthYear(end)}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-ink-muted"
          onClick={() => showWeek(thisWeek)}
        >
          This week
        </Button>
        <div className="ml-auto flex items-center gap-2">
          <PlanStateToggle />
          <Button size="sm" onClick={() => setPlanning({ date: planningDate })}>
            Plan a session
          </Button>
        </div>
      </Toolbar>

      <PageBody>
        <PlanStateBanner />

        <div className="mb-4">
          <h1 className="font-semibold text-2xl tracking-[-0.02em]">
            Calendar
          </h1>
          <p className="mt-1 text-ink-muted text-base">
            {week.data
              ? `${week.data.session_count} planned · ${formatDurationHm(
                  week.data.planned_duration_s,
                )} prescribed`
              : " "}
          </p>
        </div>

        {moveFailure ? (
          <div
            role="alert"
            className="mb-4 flex items-start gap-3 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
          >
            <ul className="flex flex-1 flex-col gap-1">
              {moveFailure.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
            <Button
              type="button"
              size="xs"
              variant="ghost"
              className="text-destructive"
              onClick={() => setMoveFailure(null)}
            >
              Dismiss
            </Button>
          </div>
        ) : null}

        {week.isPending ? (
          <p className="text-ink-muted text-sm">Loading the week…</p>
        ) : week.error ? (
          <p role="alert" className="text-destructive text-sm">
            Could not load this week. Is the API reachable?
          </p>
        ) : (
          // The rail sits left of the grid on a wide screen and above it on a
          // narrow one: seven 134px columns already scroll horizontally, and
          // stealing 200px from them to keep the rail beside them would make
          // the days unreadable before it made the totals inconvenient.
          <div
            data-testid="week-body"
            data-stale={stale ? "true" : undefined}
            aria-busy={stale || undefined}
            className={`flex flex-col gap-3 transition-opacity xl:flex-row xl:items-start ${
              stale ? "opacity-50" : ""
            }`}
          >
            <WeekRail week={week.data} className="xl:w-[212px] xl:shrink-0" />
            <div className="min-w-0 flex-1">
              <WeekGrid
                days={week.data.days}
                today={today}
                onOpen={setOpenSession}
                onMove={moveSession}
                onPlan={(date) => setPlanning({ date })}
              />
            </div>
          </div>
        )}
      </PageBody>

      <SessionSheet
        session={openSession}
        busy={busy}
        problems={sheetProblems}
        notice={copiedTo ? `Copied to ${formatDayMonthYear(copiedTo)}.` : null}
        onClose={() => {
          setOpenSession(null);
          setCopiedTo(null);
          remove.reset();
          copy.reset();
        }}
        onMove={(sessionId, toDate) => {
          // The optimistic update lands the card immediately, so the sheet has
          // nothing left to say; a refusal surfaces in the page's strip.
          moveSession(sessionId, toDate);
          setOpenSession(null);
        }}
        onCopy={(sessionId, toDate) => {
          // One action's outcome at a time: the sheet has one status line and
          // one error list, and a stale one beside a fresh one reads as both
          // having just happened.
          setCopiedTo(null);
          remove.reset();
          copy.mutate(
            {
              params: { path: { planned_session_id: sessionId } },
              body: { date: toDate },
            },
            { onSuccess: () => setCopiedTo(toDate) },
          );
        }}
        onDelete={(sessionId) => {
          setCopiedTo(null);
          copy.reset();
          // Closed on success only: a sheet that vanished the instant Delete
          // was pressed would take the server's refusal with it.
          remove.mutate(
            { params: { path: { planned_session_id: sessionId } } },
            { onSuccess: () => setOpenSession(null) },
          );
        }}
        onEdit={(session) => {
          setPlanning({ date: session.date, sessionId: session.id });
          setOpenSession(null);
        }}
      />

      {planning ? (
        <SessionForm
          date={planning.date}
          sessionId={planning.sessionId ?? null}
          onClose={() => setPlanning(null)}
        />
      ) : null}
    </>
  );
}
