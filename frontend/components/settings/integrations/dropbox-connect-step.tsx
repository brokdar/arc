"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { Field } from "@/components/design/field";
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
import { cn } from "@/lib/utils";

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
 *
 * Neither step carries its own heading: both are rendered as rows of the add
 * flow's step map (`CloudFolderSteps`), which names them there. A second
 * heading under the row's own would read as a second step.
 */

/**
 * Which side of the dropbox.com/arc boundary an instruction belongs to.
 *
 * Copy, not decoration. This checklist is the only place in arc that asks the
 * athlete to leave and do something in somebody else's application, and the
 * run-through this work came from had them hunting arc's Settings for a field
 * that lives in the Dropbox console. **No item may carry two locations** —
 * "copy the App key and paste it below" was one instruction on both sites, and
 * is two items now.
 */
type Where = "dropbox" | "arc";

const WHERE_WORDS: Readonly<Record<Where, string>> = {
  dropbox: "on dropbox.com",
  arc: "in arc",
};

/** One instruction, with the side it happens on in a fixed right-hand slot. */
function ChecklistItem({
  where,
  children,
}: {
  readonly where: Where;
  readonly children: React.ReactNode;
}) {
  return (
    <li className="text-ink-muted text-sm">
      <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-2.5">
        <span className="max-w-[62ch]">{children}</span>
        <span
          data-testid="where"
          data-where={where}
          className={cn(
            "shrink-0 rounded-badge px-1.5 py-0.5 font-semibold text-2xs uppercase tracking-[0.04em]",
            where === "arc"
              ? "bg-accent-wash text-accent-quiet"
              : "bg-well text-ink-faint",
          )}
        >
          {WHERE_WORDS[where]}
        </span>
      </div>
    </li>
  );
}

/**
 * What arc can honestly say about an app key before it has spent one.
 *
 * Shape only: a Dropbox App key is a short run of letters and digits, and the
 * only thing that can tell a real one from a plausible one is an OAuth round
 * trip. So the field catches the two paste accidents it can actually see — a
 * value with whitespace in it, and the address of the console page the key was
 * printed on — and it never tells the athlete a key looks right. Claiming a
 * check that did not happen is the defect this whole feature exists to undo.
 */
const APP_KEY_SHAPE = /^[A-Za-z0-9]+$/;

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
 *
 * The saved key is handed back to the flow rather than merely announced,
 * because the flow's step map summarises this step once it is done and arc
 * can never read a stored key back: `GET /dropbox/setup` answers whether one
 * is set and from where, not what it is.
 */
export function DropboxAppKeyStep({
  onSaved,
  checking,
}: {
  /** The key that was stored, so the flow can recheck and summarise it. */
  readonly onSaved: (appKey: string) => void;
  readonly checking: boolean;
}) {
  const base = useId();
  const queryClient = useQueryClient();
  const [appKey, setAppKey] = useState("");
  const save = $api.useMutation("put", "/api/v1/connections/dropbox/app", {
    onSuccess: (_data, variables) => {
      setAppKey("");
      queryClient.invalidateQueries({ queryKey: setupKey });
      // The catalogue's `app_configured` is what decides this step is done —
      // rechecking it is what moves the flow on to connecting the account.
      onSaved(variables?.body?.app_key ?? "");
    },
  });

  const trimmed = appKey.trim();
  const shaped = APP_KEY_SHAPE.test(trimmed);
  // Silent on an untouched field: a hint under an empty input is a refusal of
  // something nobody typed.
  const hinting = trimmed !== "" && !shaped;

  return (
    <div
      data-testid="app-key-step"
      className="flex flex-col items-start gap-2.5"
    >
      <p className="max-w-[62ch] text-ink-muted text-sm">
        arc talks to your Dropbox as an app you own, so nothing about your files
        passes through anyone else&apos;s server. It is a three-minute job and
        it is done once.
      </p>
      <ol className="flex list-decimal flex-col gap-1.5 pl-5">
        <ChecklistItem where="dropbox">
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
        </ChecklistItem>
        <ChecklistItem where="dropbox">
          Pick <strong>Scoped access</strong>, then{" "}
          <strong>Full Dropbox</strong> — an App folder app can only see a
          directory it created itself, which is never the one your head unit
          already uploads to. Dropbox cannot change this afterwards; getting it
          wrong means registering another app.
        </ChecklistItem>
        <ChecklistItem where="dropbox">
          On the app&apos;s <strong>Permissions</strong> tab, tick{" "}
          <code className="font-mono">account_info.read</code>,{" "}
          <code className="font-mono">files.metadata.read</code> and{" "}
          <code className="font-mono">files.content.read</code>, then submit.
        </ChecklistItem>
        <RedirectUriStep />
        <ChecklistItem where="dropbox">
          Copy the <strong>App key</strong> from the app&apos;s Settings tab.
          There is no app secret to take with it: arc connects with PKCE.
        </ChecklistItem>
        <ChecklistItem where="arc">
          Paste the App key into the field below, and save it.
        </ChecklistItem>
      </ol>
      <form
        className="flex w-full flex-col items-start gap-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          // The previous refusal described a key that is no longer in the
          // field; react-query holds it until the next `mutate()`.
          save.reset();
          save.mutate({ body: { app_key: trimmed } });
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
            aria-invalid={hinting || undefined}
            aria-describedby={hinting ? `${base}-app-key-hint` : undefined}
            onChange={(event) => setAppKey(event.target.value)}
          />
          {hinting ? (
            <p
              id={`${base}-app-key-hint`}
              data-testid="app-key-hint"
              className="max-w-[42ch] text-destructive text-xs"
            >
              That does not look like an App key. An App key is a short run of
              letters and digits, on your app&apos;s Settings tab — a web
              address pasted here is the page the key is printed on, not the
              key.
            </p>
          ) : null}
        </Field>
        <Button type="submit" disabled={save.isPending || checking || !shaped}>
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
    // Still badged for the console: it is the answer to "what do I put under
    // Redirect URIs?", asked while standing on that page.
    return (
      <ChecklistItem where="dropbox">
        Leave <strong>Redirect URIs</strong> empty. arc is reached at{" "}
        <code className="font-mono">{origin}</code>, and Dropbox only redirects
        to https addresses or to localhost — so it will show you a code to copy
        at the end instead of sending you back here.
      </ChecklistItem>
    );
  }
  const uri = redirectUriFor(origin);
  return (
    <ChecklistItem where="dropbox">
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
    </ChecklistItem>
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
 * What a finished connect says: the account, and that arc has read it.
 *
 * Rendered by both flows and worded identically — the paste flow inline in
 * the step below, the redirect flow on the callback page — because which one
 * a deployment can offer is arc's problem and not something the athlete
 * should be able to read off a success screen.
 *
 * It exists at all because success used to be signalled by the connect step
 * disappearing. Every other step of the add flow confirms itself; the one
 * that leaves the application, spends two minutes on dropbox.com and comes
 * back was the one that did not — and it is also the one where a stored
 * credential nobody had proved would surface two screens later as a sentence
 * about a folder path.
 *
 * The athlete moves the flow on, not the render: a confirmation that
 * dismisses itself is the vanishing screen again.
 */
export function DropboxConnected({
  connection,
  onContinue,
}: {
  readonly connection: components["schemas"]["DropboxConnectionRead"];
  readonly onContinue: () => void;
}) {
  return (
    <div
      data-testid="connect-confirmation"
      role="status"
      className="flex flex-col items-start gap-2.5 rounded-card border border-hairline bg-inset px-3.5 py-3"
    >
      <p className="max-w-[62ch] text-sm">
        Connected as{" "}
        <strong>{connection.account_label || "your Dropbox account"}</strong>.
      </p>
      <p className="max-w-[62ch] text-ink-muted text-sm">
        {/* The server's sentence when it has one: a connection it stored
            without being able to prove is a fact only the server knows, and
            paraphrasing it here would put the same copy in two places. */}
        {connection.verification_note ??
          "arc read your Dropbox to check the permission works, and it does."}
      </p>
      <Button type="button" onClick={onContinue}>
        Choose the folder
      </Button>
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
  // The completed connection, held until the athlete has read it. Nothing is
  // invalidated on success: the flow decides which step is owed from the
  // catalogue, so refetching it here would replace this step with the folder
  // picker before the confirmation had been on screen for a frame.
  const [connected, setConnected] = useState<
    components["schemas"]["DropboxConnectionRead"] | null
  >(null);
  const complete = $api.useMutation(
    "post",
    "/api/v1/connections/dropbox/complete",
    {
      onSuccess: (data) => {
        setCode("");
        setConnected(data);
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
      {/* Everything that explains the step goes away once the step is done:
          an instruction still on screen beside its own confirmation reads as
          something the athlete has to do again. */}
      {connected === null ? (
        <>
          <p className="max-w-[62ch] text-ink-muted text-sm">
            arc reads the folder your head unit already uploads to, so a ride
            never has to be uploaded by hand.
          </p>
          {setup.data?.app_key_set ? (
            <AppKeyInForce source={setup.data.source} />
          ) : null}
        </>
      ) : null}

      {connected !== null ? (
        <DropboxConnected
          connection={connected}
          onContinue={() => {
            queryClient.invalidateQueries();
            onConnected();
          }}
        />
      ) : canRedirect && !pasting ? (
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
