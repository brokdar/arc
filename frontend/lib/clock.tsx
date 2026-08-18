"use client";

import { createContext, useContext } from "react";

import { $api } from "@/lib/api/client";
import { todayIsoDate } from "@/lib/dates";

/**
 * The athlete's clock, made available to the whole signed-in app.
 *
 * There is one athlete and therefore one local clock — `MATCHING__TIMEZONE`,
 * served by `GET /clock`. Everything in this application that means "today"
 * means today *there*: the plan week the backend resolves when a caller names
 * none, the day a wellness prompt is raised for, the day an anchor becomes
 * effective on.
 *
 * The frontend had no way to learn that zone, so it used the browser's, and
 * `WellnessCard` had to *hide* the standing prompt whenever the two disagreed
 * over a midnight — the athlete silently lost the day's question with nothing
 * on screen to say why (issue #62, finding 3). With one clock there is nothing
 * left to disagree.
 *
 * The zone is read once, at the top of the signed-in tree, and every consumer
 * derives from that one value. A second `useQuery` per component would be the
 * same clock fetched five times; a component computing its own would be the
 * fifth clock this issue exists to remove.
 */
const ClockContext = createContext<string | null>(null);

/**
 * Publish a timezone to the tree below. The plumbing, without the fetch.
 *
 * Separate from `ClockProvider` so a test can render a component under a known
 * zone without an async gate in front of every assertion — and so the zone a
 * test runs under is written in the test, where a reader can see it, rather
 * than implied by a request. The app always uses `ClockProvider`; nothing in
 * `app/` or `components/` may use this one, because a hard-coded zone is the
 * fifth clock all over again.
 */
export function AthleteClock({
  timezone,
  children,
}: {
  timezone: string;
  children: React.ReactNode;
}) {
  return <ClockContext value={timezone}>{children}</ClockContext>;
}

export function ClockProvider({ children }: { children: React.ReactNode }) {
  const { data, isPending, error } = $api.useQuery("get", "/api/v1/clock", {
    // The athlete's zone changes when the operator edits `.env` and restarts,
    // which is not something to poll for. Refetching it per navigation would
    // be a request per page for a value that is constant for the session.
    staleTime: Number.POSITIVE_INFINITY,
  });

  if (isPending) {
    return <p className="p-8 text-muted-foreground">Loading…</p>;
  }
  if (error) {
    // Nothing below this can name a day without it, and guessing the zone is
    // precisely the bug. Say what happened instead — the same choice
    // `AuthGuard` makes about an unreachable API.
    return (
      <p className="p-8 text-destructive">
        Could not read the athlete&apos;s timezone. Is the API reachable?
      </p>
    );
  }

  return <AthleteClock timezone={data.timezone}>{children}</AthleteClock>;
}

/**
 * The athlete's timezone — an IANA name, a fixed offset, or `UTC`.
 *
 * Safe to hand straight to `Intl.DateTimeFormat` as a `timeZone`: the backend
 * refuses to serve a zone-database key `Intl` cannot resolve
 * (`app.domain.activity.parse_timezone`).
 *
 * Throws outside a `ClockProvider` rather than falling back to the browser:
 * a component that quietly used the wrong clock is the failure this replaced,
 * and it failed silently for exactly as long as nobody was looking.
 */
export function useAthleteTimezone(): string {
  const timezone = useContext(ClockContext);
  if (timezone === null) {
    throw new Error(
      "useAthleteTimezone must be used inside a ClockProvider — see lib/clock.tsx",
    );
  }
  return timezone;
}

/**
 * Today, on the athlete's clock, as `YYYY-MM-DD`.
 *
 * Re-derived on every render rather than frozen here: callers that must not
 * change day mid-session capture it in a `useState` initialiser, which is the
 * decision each page makes for itself.
 */
export function useAthleteToday(): string {
  return todayIsoDate(useAthleteTimezone());
}
