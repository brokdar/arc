"use client";

import { useQueryClient } from "@tanstack/react-query";
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
   */
  const showWeek = (next: string) =>
    window.history.replaceState(
      null,
      "",
      next === thisWeek ? pathname : `${pathname}?week=${next}`,
    );

  const [openSession, setOpenSession] = useState<WeekSession | null>(null);
  // The plan form is one component in two modes: `{ date }` plans a new
  // session on that day, `{ date, sessionId }` revises an existing one.
  const [planning, setPlanning] = useState<{
    date: string;
    sessionId?: string;
  } | null>(null);

  const queryClient = useQueryClient();
  const weekInit = { params: { query: { start } } };
  const weekKey = $api.queryOptions(
    "get",
    "/api/v1/plan/week",
    weekInit,
  ).queryKey;

  const week = $api.useQuery("get", "/api/v1/plan/week", weekInit);

  const invalidateWeeks = () =>
    queryClient.invalidateQueries({ queryKey: WEEK_QUERY_PREFIX });

  const move = $api.useMutation(
    "post",
    "/api/v1/planned-sessions/{planned_session_id}/move",
    {
      // Dragging a card should land where it was dropped, not a request later.
      onMutate: async (variables) => {
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
      onError: (_error, _variables, context) => {
        if (context?.previous) {
          queryClient.setQueryData(weekKey, context.previous);
        }
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
          <Button size="sm" onClick={() => setPlanning({ date: today })}>
            Plan a session
          </Button>
        </div>
      </Toolbar>

      <PageBody>
        <PlanStateBanner />

        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h1 className="font-semibold text-2xl tracking-[-0.02em]">
              Calendar
            </h1>
            <p className="mt-1 text-ink-muted text-base">
              {week.data
                ? `${week.data.session_count} planned · ${formatDurationHm(
                    week.data.planned_duration_s,
                  )} prescribed`
                : " "}
            </p>
          </div>
        </div>

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
          <div className="flex flex-col gap-3 xl:flex-row xl:items-start">
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
        onClose={() => setOpenSession(null)}
        onMove={(sessionId, toDate) => {
          moveSession(sessionId, toDate);
          setOpenSession(null);
        }}
        onCopy={(sessionId, toDate) => {
          copy.mutate({
            params: { path: { planned_session_id: sessionId } },
            body: { date: toDate },
          });
          setOpenSession(null);
        }}
        onDelete={(sessionId) => {
          remove.mutate({
            params: { path: { planned_session_id: sessionId } },
          });
          setOpenSession(null);
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
