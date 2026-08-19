"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field } from "@/components/design/field";
import { SectionLabel } from "@/components/design/section-label";
import { Problems } from "@/components/settings/integrations/integration-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";

/**
 * The two Dropbox steps an integration's cloud-folder transport needs first.
 *
 * They are steps of *adding a source*, not a panel of their own, which is the
 * whole point of where this file sits. The athlete is adding Wahoo; Dropbox is
 * the road the rides travel down, and it shows up only for as long as it takes
 * to open it.
 *
 * PR-6 owns this file next, when the paste disappears in favour of a real
 * redirect. The paste stays reachable even then — on a plain-HTTP LAN
 * deployment Dropbox refuses to redirect at all.
 */

/**
 * Register a Dropbox app: the step that cannot be done inside arc.
 *
 * arc has no Dropbox app of its own **by design** — a self-hosted install has
 * no shared client to be a client of — so the athlete registers one and hands
 * arc the key. Shown only when `DROPBOX__APP_KEY` is unset, and never
 * re-entered once it is: a completed step re-asked is a step the athlete
 * cannot tell from a failure.
 */
export function DropboxAppKeyStep({
  onRecheck,
  checking,
}: {
  readonly onRecheck: () => void;
  readonly checking: boolean;
}) {
  return (
    <div
      data-testid="app-key-step"
      className="flex flex-col items-start gap-2.5"
    >
      <SectionLabel>Register a Dropbox app</SectionLabel>
      <p className="max-w-[62ch] text-ink-muted text-sm">
        arc talks to your Dropbox as an app you own, so nothing about your files
        passes through anyone else&apos;s server. It is a three-minute job and
        it is done once.
      </p>
      <ol className="flex list-decimal flex-col gap-1 pl-5 text-ink-muted text-sm">
        <li>
          Open{" "}
          <a
            className="text-accent underline underline-offset-2"
            href="https://www.dropbox.com/developers/apps"
            target="_blank"
            rel="noreferrer"
          >
            dropbox.com/developers/apps
          </a>{" "}
          and choose <strong>Create app</strong>.
        </li>
        <li>
          Pick <strong>Scoped access</strong>, then{" "}
          <strong>Full Dropbox</strong> — an App folder app can only see a
          directory it created itself, which is never the one your head unit
          already uploads to.
        </li>
        <li>
          On the app&apos;s <strong>Permissions</strong> tab, tick{" "}
          <code className="font-mono">account_info.read</code>,{" "}
          <code className="font-mono">files.metadata.read</code> and{" "}
          <code className="font-mono">files.content.read</code>, then submit.
        </li>
        <li>
          Copy the <strong>App key</strong> into{" "}
          <code className="font-mono">DROPBOX__APP_KEY</code> in arc&apos;s
          environment and restart arc.
        </li>
      </ol>
      <Button type="button" disabled={checking} onClick={onRecheck}>
        {checking ? "Checking…" : "I have added the app key"}
      </Button>
    </div>
  );
}

/**
 * The connect ritual: render the link, take the code Dropbox showed.
 *
 * Two visible steps because it *is* two steps — there is no redirect and no
 * callback, which is what lets arc connect a cloud account from behind a home
 * router. The pasted code is **kept** when the exchange is refused: a
 * 43-character string copied off another site is exactly the thing a form must
 * not throw away on an error.
 */
export function DropboxConnectStep({
  onConnected,
}: {
  readonly onConnected: () => void;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const start = $api.useMutation(
    "post",
    "/api/v1/connections/dropbox/authorize",
  );
  const complete = $api.useMutation(
    "post",
    "/api/v1/connections/dropbox/complete",
    {
      onSuccess: () => {
        setCode("");
        queryClient.invalidateQueries();
        onConnected();
      },
    },
  );

  return (
    <div
      data-testid="connect-step"
      className="flex flex-col items-start gap-2.5"
    >
      <SectionLabel>Connect the Dropbox account</SectionLabel>
      <p className="max-w-[62ch] text-ink-muted text-sm">
        arc reads the folder your head unit already uploads to, so a ride never
        has to be uploaded by hand.
      </p>
      {start.data ? (
        <form
          className="flex w-full flex-col items-start gap-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            // The previous refusal described a code that is no longer in the
            // field; react-query holds it until the next `mutate()`.
            complete.reset();
            complete.mutate({ body: { code } });
          }}
        >
          <a
            className="text-accent text-sm underline underline-offset-2"
            href={start.data.authorize_url}
            target="_blank"
            rel="noreferrer"
          >
            Open Dropbox to authorise arc
          </a>
          <p className="max-w-[62ch] text-ink-muted text-sm">
            Dropbox will show you a code instead of sending you back here — that
            is what lets arc connect without being reachable from the internet.
            Paste it below.
          </p>
          <Field
            label="Authorisation code"
            htmlFor={`${base}-code`}
            className="w-full max-w-[42ch]"
          >
            <Input
              id={`${base}-code`}
              className="font-mono"
              value={code}
              autoComplete="off"
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
          <Button
            type="submit"
            disabled={complete.isPending || code.trim() === ""}
          >
            {complete.isPending ? "Connecting…" : "Finish connecting"}
          </Button>
        </form>
      ) : (
        <Button
          type="button"
          disabled={start.isPending}
          onClick={() => start.mutate({})}
        >
          {start.isPending ? "Starting…" : "Connect Dropbox"}
        </Button>
      )}
      <Problems
        problems={[
          ...apiErrorMessages(start.error),
          ...apiErrorMessages(complete.error),
        ]}
      />
    </div>
  );
}
