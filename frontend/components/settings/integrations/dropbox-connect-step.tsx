"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field } from "@/components/design/field";
import { SectionLabel } from "@/components/design/section-label";
import { Problems } from "@/components/settings/integrations/integration-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";

const setupKey = $api.queryOptions(
  "get",
  "/api/v1/connections/dropbox/setup",
).queryKey;
const catalogueKey = $api.queryOptions(
  "get",
  "/api/v1/integration-catalogue",
).queryKey;

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
 * arc the key, pasted straight into the field below rather than into `.env`:
 * `PUT /connections/dropbox/app` stores it and the very next authorize call
 * carries it, with no restart. Shown only while arc holds no app key in
 * either source, and never re-entered once it does: a completed step re-asked
 * is a step the athlete cannot tell from a failure.
 */
export function DropboxAppKeyStep({
  onRecheck,
  checking,
}: {
  readonly onRecheck: () => void;
  readonly checking: boolean;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [appKey, setAppKey] = useState("");
  const save = $api.useMutation("put", "/api/v1/connections/dropbox/app", {
    onSuccess: () => {
      setAppKey("");
      queryClient.invalidateQueries({ queryKey: setupKey });
      // The catalogue's `app_configured` is what decides this step is done —
      // rechecking it is what moves the flow on to connecting the account.
      onRecheck();
    },
  });

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
          already uploads to. Dropbox cannot change this afterwards; getting it
          wrong means registering another app.
        </li>
        <li>
          On the app&apos;s <strong>Permissions</strong> tab, tick{" "}
          <code className="font-mono">account_info.read</code>,{" "}
          <code className="font-mono">files.metadata.read</code> and{" "}
          <code className="font-mono">files.content.read</code>, then submit.
        </li>
        <li>
          Copy the <strong>App key</strong> from the app&apos;s Settings tab and
          paste it below. There is no app secret to copy: arc connects with
          PKCE.
        </li>
      </ol>
      <form
        className="flex w-full flex-col items-start gap-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          // The previous refusal described a key that is no longer in the
          // field; react-query holds it until the next `mutate()`.
          save.reset();
          save.mutate({ body: { app_key: appKey } });
        }}
      >
        <Field
          label="Dropbox app key"
          htmlFor={`${base}-app-key`}
          className="w-full max-w-[42ch]"
        >
          <Input
            id={`${base}-app-key`}
            className="font-mono"
            value={appKey}
            autoComplete="off"
            onChange={(event) => setAppKey(event.target.value)}
          />
        </Field>
        <Button
          type="submit"
          disabled={save.isPending || checking || appKey.trim() === ""}
        >
          {save.isPending || checking ? "Saving…" : "Save app key"}
        </Button>
      </form>
      <Problems problems={apiErrorMessages(save.error)} />
    </div>
  );
}

/**
 * Which app key arc is connecting with, and whether Settings can undo it.
 *
 * Named rather than reduced to "an app key is set", because the two sources
 * are fixed in different places: a stored key is removed from here, while
 * `DROPBOX__APP_KEY` needs an edit and a restart. A remove control offered
 * against an environment key would appear to do nothing.
 */
function AppKeyInForce({
  source,
}: {
  readonly source: components["schemas"]["SettingSource"] | null;
}) {
  const queryClient = useQueryClient();
  const clear = $api.useMutation("delete", "/api/v1/connections/dropbox/app", {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: setupKey });
      // The catalogue too: with no environment seed behind the stored key the
      // flow owes the athlete the registration step again, and the catalogue's
      // `app_configured` is what says so.
      queryClient.invalidateQueries({ queryKey: catalogueKey });
    },
  });

  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <p className="max-w-[62ch] text-ink-muted text-sm">
        {source === "stored"
          ? "arc is using the Dropbox app key saved here."
          : "arc is using the app key from DROPBOX__APP_KEY."}
      </p>
      {source === "stored" ? (
        <Button
          type="button"
          size="xs"
          variant="secondary"
          disabled={clear.isPending}
          onClick={() => clear.mutate({})}
        >
          Use a different app
        </Button>
      ) : null}
      <Problems problems={apiErrorMessages(clear.error)} />
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
  // Which app key the connect will run on, and from which source. The flow
  // only renders this step once a key exists, so the read is informative
  // rather than gating — but "saved here" versus "from DROPBOX__APP_KEY" is a
  // difference the athlete acts on, and only this endpoint can tell them.
  const setup = $api.useQuery("get", "/api/v1/connections/dropbox/setup");
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
      {setup.data?.app_key_set ? (
        <AppKeyInForce source={setup.data.source} />
      ) : null}
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
