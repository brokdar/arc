import type { components } from "@/generated/api/schema";

export type PlanWeek = components["schemas"]["PlanWeekRead"];
export type PlanWeekDay = components["schemas"]["PlanWeekDayRead"];
export type PlanWeekDiscipline =
  components["schemas"]["PlanWeekDisciplineRead"];
export type WeekSession = components["schemas"]["WeekSessionRead"];
type Discipline = components["schemas"]["Discipline"];

/** The vocabulary order the API emits discipline rows in (`app.domain.athlete`). */
const DISCIPLINES: readonly Discipline[] = ["cycling", "strength"];

/**
 * Move a session between days of a cached week, for the optimistic update
 * behind drag-and-drop.
 *
 * Pure and total: a card dropped on a day outside the fetched window simply
 * disappears from it (the server agrees — that week no longer contains it),
 * and an id that is not in the week comes back unchanged rather than throwing
 * in a react-query `onMutate`.
 *
 * **Every total is recomputed, not just the ones that are cheap.** A card
 * carries its own duration, its own predicted load and its own set count, so
 * dropping it out of the week has to take its TSS, its minutes, its sets and
 * both of its coverage pairs with it — on the week *and* on its discipline
 * row. Leaving `planned_load` alone while `session_count` fell would put a
 * number on the rail that no session in the grid contributes to, which is the
 * one thing the rail exists to prevent. The recomputation is the same fold the
 * server does (`app.services.plan`), so the optimistic week and the refetched
 * one differ in nothing but their freshness; the server stays the source of
 * truth, and `onSettled` invalidation is what restores it.
 */
export function moveSessionInWeek(
  week: PlanWeek,
  sessionId: string,
  toDate: string,
): PlanWeek {
  const moved = findSession(week, sessionId);
  if (!moved || moved.date === toDate) {
    return week;
  }

  const relocated: WeekSession = { ...moved, date: toDate };
  const days = week.days.map((day) => {
    if (day.date === toDate) {
      const others = day.sessions.filter((s) => s.id !== sessionId);
      return { ...day, sessions: [...others, relocated] };
    }
    if (day.sessions.some((s) => s.id === sessionId)) {
      return {
        ...day,
        sessions: day.sessions.filter((s) => s.id !== sessionId),
      };
    }
    return day;
  });

  return withTotals(week, days);
}

/**
 * The completed columns of one discipline's row, as the server last sent them.
 *
 * An optimistic edit to the plan may not invent, drop or recompute what was
 * recorded — the two sides of the rail are independent, and the refetch a
 * moment later is what updates the completed one.
 */
function completedOf(
  week: PlanWeek,
  discipline: PlanWeekDiscipline["discipline"],
) {
  const existing = week.by_discipline.find(
    (row) => row.discipline === discipline,
  );
  return {
    completed_session_count: existing?.completed_session_count ?? 0,
    completed_duration_s: existing?.completed_duration_s ?? null,
    completed_load: existing?.completed_load ?? null,
    completed_load_sessions_counted:
      existing?.completed_load_sessions_counted ?? 0,
    completed_load_sessions_uncounted:
      existing?.completed_load_sessions_uncounted ?? 0,
  };
}

/** Find a session anywhere in the week, or `undefined`. */
export function findSession(
  week: PlanWeek,
  sessionId: string,
): WeekSession | undefined {
  for (const day of week.days) {
    const found = day.sessions.find((session) => session.id === sessionId);
    if (found) {
      return found;
    }
  }
  return undefined;
}

/**
 * Recompute every derived figure on the week from the cards that remain.
 *
 * `session_count` is taken from the cards rather than carried over: the
 * server's own count can exceed them when `MAX_WEEK_SESSIONS` truncated the
 * window, but a truncated week cannot be optimistically edited *and* keep
 * claiming a total it never rendered — and the refetch a moment later restores
 * the server's answer either way.
 */
function withTotals(week: PlanWeek, days: readonly PlanWeekDay[]): PlanWeek {
  const sessions = days.flatMap((day) => day.sessions);
  return {
    ...week,
    days: [...days],
    session_count: sessions.length,
    ...totals(sessions),
    by_discipline: DISCIPLINES.flatMap((discipline) => {
      const group = sessions.filter(
        (session) => session.discipline === discipline,
      );
      const completed = completedOf(week, discipline);
      // A discipline row exists for *either* side. The backend emits one with
      // no planned sessions and a recorded ride in it, and dropping such a
      // row here — because the optimistic rebuild only knows about planned
      // cards — deleted what actually happened from the cache until the
      // refetch put it back.
      if (group.length === 0 && completed.completed_session_count === 0) {
        return [];
      }
      const sets = group.filter((session) => session.total_sets !== null);
      const row: PlanWeekDiscipline = {
        discipline,
        session_count: group.length,
        ...totals(group),
        // Moving a *planned* session between days changes nothing about what
        // was recorded, so the completed columns are carried over rather than
        // recomputed. A discipline that had no row before the move has
        // nothing recorded either, which is what the fallback says.
        ...completed,
        total_sets: sets.length
          ? sets.reduce((sum, session) => sum + (session.total_sets ?? 0), 0)
          : null,
      };
      return [row];
    }),
  };
}

/** The four counters and two totals every level of the projection carries. */
function totals(sessions: readonly WeekSession[]) {
  const timed = sessions.filter((s) => s.planned_duration_s !== null);
  const predicted = sessions.filter((s) => s.predicted_load !== null);
  return {
    // Null, never 0 — the contract the server holds to, mirrored here so a
    // drag cannot turn a week of lifts into a rest week for one request.
    planned_duration_s: timed.length
      ? timed.reduce((sum, s) => sum + (s.planned_duration_s ?? 0), 0)
      : null,
    duration_sessions_counted: timed.length,
    duration_sessions_uncounted: sessions.length - timed.length,
    planned_load: predicted.length
      ? predicted.reduce((sum, s) => sum + (s.predicted_load ?? 0), 0)
      : null,
    load_sessions_counted: predicted.length,
    load_sessions_uncounted: sessions.length - predicted.length,
  };
}
