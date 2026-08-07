import type { components } from "@/generated/api/schema";

export type PlanWeek = components["schemas"]["PlanWeekRead"];
export type PlanWeekDay = components["schemas"]["PlanWeekDayRead"];
export type WeekSession = components["schemas"]["WeekSessionRead"];

/**
 * Move a session between days of a cached week, for the optimistic update
 * behind drag-and-drop.
 *
 * Pure and total: a card dropped on a day outside the fetched window simply
 * disappears from it (the server agrees — that week no longer contains it),
 * and an id that is not in the week comes back unchanged rather than throwing
 * in a react-query `onMutate`. The week's own totals are recomputed here too,
 * so the header does not disagree with the grid for the length of a request.
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

  return withTotals({ ...week, days });
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

/** Recompute the week's derived counters from its days. */
function withTotals(week: PlanWeek): PlanWeek {
  let sessionCount = 0;
  let counted = 0;
  let plannedDurationS = 0;
  for (const day of week.days) {
    for (const session of day.sessions) {
      sessionCount += 1;
      if (session.planned_duration_s !== null) {
        counted += 1;
        plannedDurationS += session.planned_duration_s;
      }
    }
  }
  return {
    ...week,
    session_count: sessionCount,
    // Null, never 0 — the contract the server holds to, mirrored here so a
    // drag cannot turn a week of lifts into a rest week for one request.
    planned_duration_s: counted ? plannedDurationS : null,
    duration_sessions_counted: counted,
    duration_sessions_uncounted: sessionCount - counted,
  };
}
