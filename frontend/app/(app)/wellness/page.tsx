import { Suspense } from "react";

import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { WellnessView } from "@/components/wellness/wellness-view";

export const metadata = {
  title: "Wellness — arc",
};

/**
 * `WellnessView` reads `?date=` with `useSearchParams`, which a prerendered
 * route cannot know — the same boundary `/calendar` needs, for the same reason:
 * without it the production build fails with "Missing Suspense boundary with
 * useSearchParams", and making the route dynamic would trade a static page for
 * a query string the client resolves in a millisecond.
 */
export default function WellnessPage() {
  return (
    <Suspense fallback={<WellnessFallback />}>
      <WellnessView />
    </Suspense>
  );
}

/** The page's furniture, minus everything that depends on which day. */
function WellnessFallback() {
  return (
    <>
      <Toolbar />
      <PageBody>
        <h1 className="font-semibold text-2xl tracking-[-0.02em]">Wellness</h1>
        <p className="mt-1 text-ink-muted text-base">Loading the day…</p>
      </PageBody>
    </>
  );
}
