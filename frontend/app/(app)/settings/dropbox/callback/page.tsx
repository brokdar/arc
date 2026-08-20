import { Suspense } from "react";

import { DropboxCallback } from "@/components/settings/integrations/dropbox-callback";
import { PageBody, Toolbar } from "@/components/shell/app-shell";

export const metadata = {
  title: "Connecting Dropbox — arc",
};

/**
 * The address Dropbox redirects to at the end of connecting an account.
 *
 * Inside `(app)`, so the athlete's session guard and the app shell wrap it:
 * the code arrives in a browser that is already logged in, which is what lets
 * the exchange happen over the session-guarded API instead of over a public
 * backend callback (see `DropboxCallback`).
 *
 * `DropboxCallback` reads `?code=`, `?state=` and `?error=` with
 * `useSearchParams`, which a prerendered route cannot know — the same boundary
 * `/calendar` and `/wellness` need, and without it the production build fails
 * with "Missing Suspense boundary with useSearchParams".
 */
export default function DropboxCallbackPage() {
  return (
    <Suspense fallback={<CallbackFallback />}>
      <DropboxCallback />
    </Suspense>
  );
}

/** The furniture, minus everything that depends on what Dropbox sent back. */
function CallbackFallback() {
  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          Connecting Dropbox
        </h1>
      </Toolbar>
      <PageBody>
        <p className="text-ink-muted text-sm">
          Reading what Dropbox sent back…
        </p>
      </PageBody>
    </>
  );
}
