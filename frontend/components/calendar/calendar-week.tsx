"use client";

import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  PlanStateBanner,
  PlanStateToggle,
} from "@/components/calendar/plan-state";
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
import { isUuid } from "@/lib/ids";
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
 * **Where you are lives in the URL** — both facets of it. Which week is shown
 * is `?week=2026-08-03` (D77) and which session is open is `?session=<id>`
 * (D88), because a session someone is reading is a place they would bookmark,
 * reload or send to their coach, and state that survives a reload has to be
 * addressable (UI convention 1). The week param is taken literally, not
 * snapped to a Monday — the same rule the endpoint follows (D55) — so a link
 * to a Wednesday shows the seven days from that Wednesday. Anything
 * unreadable, and anything at all missing, means this week.
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
   * Move the address bar: set the named params, drop the ones set to `null`,
   * and carry every other one through untouched.
   *
   * The native History API rather than `router.push` / `router.replace`,
   * because those two **cannot drop a search param** in this Next major:
   * navigating from `/calendar?week=…` to `/calendar` is a silent no-op
   * (verified against a production build, D77), which would strand both "This
   * week" and closing the sheet on whatever the URL last said. `pushState` /
   * `replaceState` are Next's own documented escape hatch for updating the URL
   * without navigating, and they sync `usePathname` / `useSearchParams`, so
   * the component still re-renders off the address bar.
   *
   * Carrying the untouched params through is the point of taking a patch
   * rather than a whole query string: this page's address has two facets
   * already, and rebuilding it from either one alone would silently drop the
   * other.
   */
  const writeUrl = (
    changes: Readonly<Record<string, string | null>>,
    how: "push" | "replace",
  ) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    }
    const query = params.toString();
    const url = query ? `${pathname}?${query}` : pathname;
    if (how === "push") {
      window.history.pushState(null, "", url);
    } else {
      window.history.replaceState(null, "", url);
    }
  };

  /**
   * Show `next`, and close whatever sheet was open.
   *
   * **Replace, not push**: stepping a week adjusts the view of one page, and
   * pushing would make the back button mean "undo one of my last eleven
   * clicks" instead of "leave the calendar", eleven entries deep after a
   * minute of paging. The URL is a real address either way — a bookmark and a
   * shared link both work — so only the history stack is at stake (D77).
   *
   * This week is the bare `/calendar`, never `?week=<this monday>`: a URL
   * whose meaning is "the week I am in" is still right tomorrow, so the
   * address someone bookmarks does not quietly become last week's.
   *
   * The open sheet is left exactly as it is — deliberately, and it costs
   * nothing: the sheet is a modal, so these controls are inert while one is
   * open and this cannot run underneath it. The two params are independent
   * facets of one address, and `?week=…&session=…` naming a session outside
   * that week is a link this page honours (D88).
   */
  const showWeek = (next: string) => {
    writeUrl({ week: next === thisWeek ? null : next }, "replace");
  };

  /**
   * Which session is open, read off the URL rather than held beside it.
   *
   * Derived, not duplicated: a copy in state would be the thing the address
   * bar disagrees with the moment the athlete presses Back, and Back closing
   * the sheet is the whole point of pushing an entry when it opens.
   */
  const sessionParam = searchParams.get("session");
  const openSessionId = isUuid(sessionParam) ? sessionParam : null;

  // A `session` that is not an id names no session. Treated as absent and
  // swept out of the address bar, rather than spent on
  // `GET /planned-sessions/<garbage>` or left in a URL the page is ignoring.
  // `writeUrl` is left out of the deps deliberately: it closes over this
  // render's params, and re-running on every render would have this sweep
  // fight whatever else has since written to them.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the param is the input
  useEffect(() => {
    if (sessionParam !== null && !isUuid(sessionParam)) {
      writeUrl({ session: null }, "replace");
    }
  }, [sessionParam]);

  const openSession = (sessionId: string) =>
    // Push, so the browser's Back gesture closes the sheet — the one thing
    // every athlete already knows how to do on a phone.
    writeUrl({ session: sessionId }, "push");

  const closeSession = () =>
    // Replace, so opening and closing a dozen cards does not bury the page
    // the athlete arrived from under a dozen identical entries.
    writeUrl({ session: null }, "replace");

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

  /**
   * The card behind the open sheet, when the week on screen carries one.
   *
   * `null` is a normal answer, not a failure: a link to a session on another
   * week arrives with no card at all, and the sheet renders itself from the
   * session it fetches instead. Handing it over when we do have it is what
   * keeps the header on screen from the first frame rather than after a
   * request (D55).
   */
  const openCard =
    week.data?.days
      .flatMap((day) => day.sessions)
      .find((session) => session.id === openSessionId) ?? null;

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

  // One action's outcome at a time, and no outcome outlives the sheet it was
  // reported in. The close handler used to do this; the URL closes the sheet
  // now, and a Back press calls no handler at all.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the open session is the input
  useEffect(() => {
    setCopiedTo(null);
    remove.reset();
    copy.reset();
  }, [openSessionId]);

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
                onOpen={(session) => openSession(session.id)}
                onMove={moveSession}
                onPlan={(date) => setPlanning({ date })}
              />
            </div>
          </div>
        )}
      </PageBody>

      <SessionSheet
        sessionId={openSessionId}
        card={openCard}
        busy={busy}
        problems={sheetProblems}
        notice={copiedTo ? `Copied to ${formatDayMonthYear(copiedTo)}.` : null}
        onClose={closeSession}
        onMove={(sessionId, toDate) => {
          // The optimistic update lands the card immediately, so the sheet has
          // nothing left to say; a refusal surfaces in the page's strip.
          moveSession(sessionId, toDate);
          closeSession();
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
            { onSuccess: closeSession },
          );
        }}
        onEdit={(sessionId, date) => {
          setPlanning({ date, sessionId });
          closeSession();
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
