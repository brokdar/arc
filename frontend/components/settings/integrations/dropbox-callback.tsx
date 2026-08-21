"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { DropboxConnected } from "@/components/settings/integrations/dropbox-connect-step";
import { PageBody, Toolbar } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { keepAddFlowOpen } from "@/lib/dropbox-redirect";

/**
 * Where Dropbox sends the athlete back to, and the last step of connecting.
 *
 * **A page in arc's frontend, not a route on arc's API.** The conventional
 * shape is a public backend callback, and it is the wrong one here: Dropbox's
 * redirect is a fresh navigation from dropbox.com carrying no session, so such
 * a route would have to sit in `OPEN_PATHS` — an endpoint on the open internet
 * accepting authorization codes, on a box whose entire security posture is
 * that nothing on the internet reaches it. The redirect instead lands in the
 * browser the athlete is already logged in to, and this page hands the code to
 * the session-guarded `POST /connections/dropbox/complete` with their cookie.
 *
 * Four ways it can be opened, and none of them may render a blank page or a
 * spinner that never resolves: with a code (complete it), with an `error`
 * (Dropbox or the athlete said no), with neither (a bookmark, a reload after
 * the code was spent), and with a code the server then refuses (a stale
 * nonce, or a grant that cannot read the athlete's files — the server's
 * sentence is what the athlete reads).
 *
 * A completed connection stops here on the same confirmation the paste flow
 * ends on rather than replacing straight to Settings. The athlete has just
 * spent two minutes on somebody else's site; landing back on a page that
 * silently moved on is how a connect that stored an unusable credential went
 * unnoticed until the folder step failed.
 */
export function DropboxCallback() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const code = params.get("code");
  const state = params.get("state");
  const error = params.get("error");

  const complete = $api.useMutation(
    "post",
    "/api/v1/connections/dropbox/complete",
    {
      onSuccess: () => {
        queryClient.invalidateQueries();
      },
    },
  );

  // One exchange per visit. The code is single-use, so a second `mutate()` —
  // from a re-render, or React re-running the effect — would spend it against
  // Dropbox and turn a working connect into "that code has already been used".
  const spent = useRef(false);
  const exchange = complete.mutate;
  useEffect(() => {
    if (spent.current || code === null || error !== null) {
      return;
    }
    spent.current = true;
    exchange({ body: state === null ? { code } : { code, state } });
  }, [code, state, error, exchange]);

  if (error !== null) {
    return (
      <CallbackFrame>
        <p
          role="alert"
          className="max-w-[62ch] rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
        >
          {error === "access_denied"
            ? "Dropbox did not connect: the request to read your files was turned down. Nothing has changed in arc."
            : `Dropbox did not connect, and said only "${error}". Nothing has changed in arc.`}
        </p>
        <Button
          type="button"
          onClick={() => {
            keepAddFlowOpen();
            router.replace("/settings");
          }}
        >
          Try connecting again
        </Button>
      </CallbackFrame>
    );
  }

  if (code === null) {
    return (
      <CallbackFrame>
        <p className="max-w-[62ch] text-ink-muted text-sm">
          Dropbox did not send anything back to this page. It is the address
          Dropbox returns to at the end of connecting an account, so there is
          nothing to do here on its own — start from Settings.
        </p>
        <BackToSettings />
      </CallbackFrame>
    );
  }

  const problems = apiErrorMessages(complete.error);
  if (problems.length > 0) {
    return (
      <CallbackFrame>
        {problems.map((problem) => (
          <p
            key={problem}
            role="alert"
            className="max-w-[62ch] rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
          >
            {problem}
          </p>
        ))}
        <BackToSettings />
      </CallbackFrame>
    );
  }

  if (complete.data) {
    return (
      <CallbackFrame>
        <DropboxConnected
          connection={complete.data}
          onContinue={() => {
            // The account is connected; the folder is still owed. Landing on
            // a bare Settings page would leave the athlete to work that out.
            keepAddFlowOpen();
            router.replace("/settings");
          }}
        />
      </CallbackFrame>
    );
  }

  return (
    <CallbackFrame>
      <p role="status" className="max-w-[62ch] text-ink-muted text-sm">
        Finishing the connection with Dropbox…
      </p>
    </CallbackFrame>
  );
}

/** The one heading and layout every one of the four answers is shown in. */
function CallbackFrame({ children }: { readonly children: React.ReactNode }) {
  return (
    <>
      <Toolbar>
        <h1 className="font-semibold text-lg tracking-[-0.01em]">
          Connecting Dropbox
        </h1>
      </Toolbar>
      <PageBody
        data-testid="dropbox-callback"
        className="flex flex-col items-start gap-3"
      >
        {children}
      </PageBody>
    </>
  );
}

function BackToSettings() {
  return (
    <Link
      className="text-accent text-sm underline underline-offset-2"
      href="/settings"
    >
      Back to Settings
    </Link>
  );
}
