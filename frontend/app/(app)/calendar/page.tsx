import { Suspense } from "react";

import { CalendarWeek } from "@/components/calendar/calendar-week";
import { PageBody, Toolbar } from "@/components/shell/app-shell";

export const metadata = {
  title: "Calendar — arc",
};

/**
 * `CalendarWeek` reads `?week=` with `useSearchParams`, which a prerendered
 * route cannot know: without this boundary the production build fails with
 * "Missing Suspense boundary with useSearchParams", and the alternative —
 * making the route dynamic — would trade a static page for a query string the
 * client resolves in a millisecond anyway. The fallback is what ships in the
 * prerendered HTML; the grid hydrates over it.
 */
export default function CalendarPage() {
  return (
    <Suspense fallback={<CalendarFallback />}>
      <CalendarWeek />
    </Suspense>
  );
}

/** The page's own furniture, minus everything that depends on which week. */
function CalendarFallback() {
  return (
    <>
      <Toolbar />
      <PageBody>
        <h1 className="font-semibold text-2xl tracking-[-0.02em]">Calendar</h1>
        <p className="mt-1 text-ink-muted text-base">Loading the week…</p>
      </PageBody>
    </>
  );
}
