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
import {
  redirectEligible,
  redirectUriFor,
  rememberAddFlow,
  useBrowserOrigin,
} from "@/lib/dropbox-redirect";

type IntegrationKind = components["schemas"]["IntegrationKind"];

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
 * Two ways through, chosen by where the browser is. Where Dropbox will
 * redirect — https anywhere, or http on the loopback — the athlete comes
 * straight back and never sees a code. Where it will not, which is arc served
 * over plain http at a LAN address, Dropbox shows a code and the athlete
 * pastes it. The paste flow is not a legacy path: it is the only one that
 * works on that deployment, it is offered explicitly on every other one as
 * the way out when a redirect does not arrive, and it is what makes arc
 * connectable from a box nothing on the internet can reach.
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
        <RedirectUriStep />
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
 * The registration step that makes the code disappear: arc’s redirect URI.
 *
 * A checklist item rather than a note, because it is a field on the same
 * console page as everything else here and it is only free to fill in *while*
 * the athlete is on it — coming back to add it later means finding the app
 * again. Shown only where Dropbox would accept it: on a deployment reached
 * over plain http at a LAN address, asking the athlete to register a URI
 * Dropbox refuses would be a step that cannot be completed, so the step says
 * what happens instead.
 *
 * Copyable as text and as a button. The button is the ordinary way; the text
 * is what survives a browser that refuses clipboard access, and it must,
 * because a mistyped redirect URI fails on dropbox.com with an error naming
 * neither arc nor the character that is wrong.
 */
function RedirectUriStep() {
  const origin = useBrowserOrigin();
  const [copied, setCopied] = useState(false);

  if (origin === null) {
    return null;
  }
  if (!redirectEligible(origin)) {
    return (
      <li>
        There is no redirect URI to register. arc is reached at{" "}
        <code className="font-mono">{origin}</code>, and Dropbox only redirects
        to https addresses or to localhost — so it will show you a code to copy
        at the end instead of sending you back here.
      </li>
    );
  }
  const uri = redirectUriFor(origin);
  return (
    <li>
      On the same Settings tab, under <strong>Redirect URIs</strong>, add{" "}
      <code data-testid="redirect-uri" className="font-mono break-all">
        {uri}
      </code>{" "}
      and choose <strong>Add</strong>. That is what lets Dropbox send you back
      to arc instead of showing you a code to copy.{" "}
      <Button
        type="button"
        size="xs"
        variant="secondary"
        onClick={() => {
          void navigator.clipboard?.writeText(uri);
          setCopied(true);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </li>
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
 * The connect ritual: hand the browser to Dropbox and take what comes back.
 *
 * **Where the browser is decides which flow runs**, and the browser is the
 * only thing that knows: arc sits behind Caddy, so the server sees a proxy's
 * idea of its own origin and `X-Forwarded-Host` is written by whoever spoke to
 * the proxy. So this reads `window.location.origin`, sends it as the redirect
 * URI, and the server validates it before Dropbox is ever told about it.
 *
 * On an eligible origin the athlete leaves in **this tab** — not a new one:
 * the redirect has to come back somewhere, and a popup arc cannot get the
 * athlete back out of is the failure mode this replaces. The place they were
 * in the add flow is written to `sessionStorage` first, because the tab is
 * about to be handed to dropbox.com and React state does not survive that.
 *
 * The paste flow is offered on every origin, not only the ineligible ones: a
 * redirect that never arrives — a mistyped redirect URI in the Dropbox
 * console, a browser that blocked the navigation — otherwise leaves the
 * athlete with a working account and no way to finish.
 */
export function DropboxConnectStep({
  onConnected,
  integrationKind = null,
}: {
  readonly onConnected: () => void;
  /** What is being added, so the flow can resume there. */
  readonly integrationKind?: IntegrationKind | null;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  // Set once the athlete asks for the code instead: either because Dropbox
  // will not redirect to this origin at all, or because a redirect they were
  // offered did not arrive.
  const [pasting, setPasting] = useState(false);
  const origin = useBrowserOrigin();
  // `null` until mounted, which is "not decided yet" and not "not eligible":
  // a redirect offered on the strength of a prerender would be offered on
  // every deployment.
  const canRedirect = origin !== null && redirectEligible(origin);
  // Which app key the connect will run on, and from which source. The flow
  // only renders this step once a key exists, so the read is informative
  // rather than gating — but "saved here" versus "from DROPBOX__APP_KEY" is a
  // difference the athlete acts on, and only this endpoint can tell them.
  const setup = $api.useQuery("get", "/api/v1/connections/dropbox/setup");
  const start = $api.useMutation(
    "post",
    "/api/v1/connections/dropbox/authorize",
    {
      onSuccess: (data, variables) => {
        if (variables?.body?.redirect_uri === undefined) {
          return;
        }
        // Written before the navigation, not after: there is no "after" in
        // this tab.
        rememberAddFlow(integrationKind);
        window.location.assign(data.authorize_url);
      },
    },
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

  const beginRedirect = () => {
    // The previous refusal described an attempt that is over; react-query
    // holds it until the next `mutate()`.
    start.reset();
    start.mutate({ body: { redirect_uri: redirectUriFor(origin as string) } });
  };
  const beginPaste = () => {
    setPasting(true);
    start.reset();
    start.mutate({ body: {} });
  };

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

      {canRedirect && !pasting ? (
        <div className="flex flex-col items-start gap-2.5">
          <p className="max-w-[62ch] text-ink-muted text-sm">
            Dropbox will ask you to allow arc to read your files, then send you
            straight back here.
          </p>
          <Button
            type="button"
            disabled={start.isPending}
            onClick={beginRedirect}
          >
            {start.isPending ? "Opening Dropbox…" : "Connect Dropbox"}
          </Button>
          {/* UI convention 3: the way out, named, before it is needed. A
              redirect that does not arrive is otherwise a dead end. */}
          <button
            type="button"
            data-testid="use-paste-flow"
            className="text-ink-muted text-sm underline underline-offset-2"
            onClick={beginPaste}
          >
            Dropbox did not bring you back? Connect with a code instead.
          </button>
        </div>
      ) : start.data ? (
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
        <div className="flex flex-col items-start gap-2.5">
          {origin !== null && !canRedirect ? (
            <p className="max-w-[62ch] text-ink-muted text-sm">
              arc is reached at <code className="font-mono">{origin}</code>, and
              Dropbox only redirects to https addresses or to localhost. It will
              show you a code to copy back here instead — nothing else about
              connecting changes.
            </p>
          ) : null}
          <Button type="button" disabled={start.isPending} onClick={beginPaste}>
            {start.isPending ? "Starting…" : "Connect Dropbox"}
          </Button>
        </div>
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
