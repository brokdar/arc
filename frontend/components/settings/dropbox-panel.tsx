"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { InlineConfirm } from "@/components/design/confirm";
import { Field } from "@/components/design/field";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { useAthleteTimezone } from "@/lib/clock";
import { formatAthleteStamp } from "@/lib/format";

type Schemas = components["schemas"];
type Connection = Schemas["ConnectionRead"];
type Feed = Schemas["FeedRead"];

const connectionsKey = $api.queryOptions("get", "/api/v1/connections").queryKey;

/**
 * What each Dropbox scope lets arc do, in words the athlete is deciding with.
 *
 * `files.metadata.read` is not a permission anybody can consent to; "list the
 * folders in your Dropbox" is. An unknown scope falls through to its own
 * string rather than being hidden — a permission arc cannot describe is still
 * a permission the athlete granted, and silently dropping it from this list
 * would be the panel lying about what it holds.
 */
const SCOPE_WORDS: Readonly<Record<string, string>> = {
  "account_info.read": "See which Dropbox account this is",
  "files.metadata.read": "List the folders in your Dropbox",
  "files.content.read": "Download the activity files it finds",
  "files.content.write": "Delete files arc has already taken",
};

/**
 * Dropbox, on the settings page: connect an account, pick the folders.
 *
 * **A panel, not a `/settings/connections` route.** UI convention 1 says to
 * deep-link what a person would bookmark, and one provider's configuration is
 * not that: it is opened twice — once at setup and once when something breaks
 * — and both times by walking to Settings, never by following a link somebody
 * sent. A route would also put the connect ritual somewhere the athlete has to
 * find, when the whole point is that it is finished once and forgotten.
 *
 * The connect flow is deliberately two visible steps, because it *is* two
 * steps: arc renders a link, Dropbox shows the athlete a code, the athlete
 * pastes it back. There is no redirect and no callback — see
 * `app/connectors/dropbox.py` — so hiding the paste would mean hiding the only
 * part the athlete has to do.
 */
export function DropboxPanel({ className }: { readonly className?: string }) {
  const connections = $api.useQuery("get", "/api/v1/connections");
  const dropbox =
    connections.data?.items.find((row) => row.provider === "dropbox") ?? null;

  return (
    <Panel className={className} data-testid="dropbox-panel">
      <div className="flex flex-col gap-3.5 px-5 py-4">
        <SectionLabel level={2}>Dropbox</SectionLabel>
        {connections.isPending ? (
          <p className="text-ink-muted text-sm">Loading the connection…</p>
        ) : // `!connections.data` first, for the reason `ProfileForm` does it:
        // a background refetch that fails must not tear down a panel that has
        // a perfectly good connection on screen — and a half-typed
        // authorisation code inside it.
        !connections.data ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(connections.error, "the Dropbox connection")}
          </p>
        ) : dropbox === null ? (
          <ConnectFlow
            intro="arc will watch a folder in your Dropbox and ingest the ride files it finds there, so a session never has to be uploaded by hand."
            startLabel="Connect Dropbox"
          />
        ) : dropbox.status === "connected" ? (
          <ConnectedAccount connection={dropbox} />
        ) : (
          <BrokenConnection connection={dropbox} />
        )}
      </div>
    </Panel>
  );
}

/**
 * The two-step connect ritual: render the link, take the pasted code.
 *
 * The pasted code is **kept** when the exchange is refused. A 43-character
 * string copied off another site is exactly the thing a form must not throw
 * away on an error — and "already used" usually means the athlete needs a new
 * link, not a retyped code, so the field stays filled while they get one.
 */
function ConnectFlow({
  intro,
  startLabel,
}: {
  readonly intro: string;
  readonly startLabel: string;
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
        queryClient.invalidateQueries({ queryKey: connectionsKey });
      },
    },
  );

  const problems = [
    ...apiErrorMessages(start.error),
    ...apiErrorMessages(complete.error),
  ];

  return (
    <div className="flex flex-col items-start gap-2.5">
      <p className="max-w-[62ch] text-ink-muted text-sm">{intro}</p>
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
          {start.isPending ? "Starting…" : startLabel}
        </Button>
      )}
      <Problems problems={problems} />
    </div>
  );
}

/** A connected account: who it is, what arc may do, and which folders. */
function ConnectedAccount({ connection }: { readonly connection: Connection }) {
  const [picking, setPicking] = useState(false);

  return (
    <div className="flex flex-col gap-3.5">
      <AccountLine connection={connection} />
      <div>
        <SectionLabel>What arc may do</SectionLabel>
        <ul className="mt-1 flex flex-col gap-0.5 text-ink-muted text-sm">
          {connection.scopes.map((scope) => (
            <li key={scope}>{SCOPE_WORDS[scope] ?? scope}</li>
          ))}
        </ul>
      </div>
      <Feeds connection={connection} />
      {picking ? (
        <FolderPicker
          connection={connection}
          onDone={() => setPicking(false)}
        />
      ) : (
        <Button
          type="button"
          variant="secondary"
          className="self-start"
          onClick={() => setPicking(true)}
        >
          Add a folder
        </Button>
      )}
      <Disconnect connection={connection} />
    </div>
  );
}

/**
 * A connection arc cannot use: the account, the reason, and the one remedy.
 *
 * `needs_reauth` and `error` are separated because their remedies are: the
 * first is fixed by the athlete reconnecting, the second by the operator
 * restoring `SECRETS__ENCRYPTION_KEY`, and offering "reconnect" for a key
 * problem would paper over a misconfiguration that is also hiding every other
 * secret.
 */
function BrokenConnection({ connection }: { readonly connection: Connection }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const forget = $api.useMutation(
    "delete",
    "/api/v1/connections/{connection_id}",
    {
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: connectionsKey }),
    },
  );

  return (
    <div className="flex flex-col items-start gap-2.5">
      <AccountLine connection={connection} />
      <p role="status" className="max-w-[62ch] text-ink-muted text-sm">
        {connection.status === "needs_reauth"
          ? (connection.last_error ??
            "Dropbox refused arc's credential, so nothing is being collected.")
          : (connection.last_error ??
            "arc cannot read its own stored credential.")}
      </p>
      {connection.status === "needs_reauth" ? (
        asking ? (
          <InlineConfirm
            question="Reconnecting forgets the dead credential and the folders arc was watching. Continue?"
            confirmLabel="Forget and reconnect"
            cancelLabel="Keep it"
            disabled={forget.isPending}
            onCancel={() => setAsking(false)}
            onConfirm={() =>
              forget.mutate({
                params: { path: { connection_id: connection.id } },
              })
            }
          />
        ) : (
          <Button type="button" onClick={() => setAsking(true)}>
            Reconnect Dropbox
          </Button>
        )
      ) : (
        <p className="max-w-[62ch] text-ink-muted text-sm">
          Restore the SECRETS__ENCRYPTION_KEY this connection was created under
          and restart arc. Reconnecting would hide a configuration problem that
          affects every stored secret, not just this one.
        </p>
      )}
      <Problems problems={apiErrorMessages(forget.error)} />
    </div>
  );
}

/** The account label and when it was connected. */
function AccountLine({ connection }: { readonly connection: Connection }) {
  const timezone = useAthleteTimezone();
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5">
      <span className="text-ink text-sm">
        {connection.account_label ?? "An unnamed Dropbox account"}
      </span>
      <span
        data-testid="dropbox-connected-at"
        className="font-mono text-ink-faint text-xs"
      >
        {formatAthleteStamp(connection.created_at, timezone)}
      </span>
    </div>
  );
}

/** The folders arc is watching on this connection. */
function Feeds({ connection }: { readonly connection: Connection }) {
  return (
    <div>
      <SectionLabel>
        Folders watched{" "}
        <span data-testid="dropbox-feed-count" className="font-mono">
          {connection.feeds.length}
        </span>
      </SectionLabel>
      {connection.feeds.length === 0 ? (
        <p className="mt-1 max-w-[62ch] text-ink-muted text-sm">
          No folder yet — pick the one your head unit already uploads to, and
          arc will collect from it.
        </p>
      ) : (
        <ul className="mt-1 flex flex-col gap-1">
          {connection.feeds.map((feed) => (
            <FeedRow key={feed.id} feed={feed} />
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One watched folder: where it points, what it last delivered, what broke.
 *
 * The delivery line is the reason this row is more than a path and two
 * buttons. A folder that has stopped collecting looks exactly like a folder
 * with nothing to collect — the athlete's only other evidence is a calendar
 * that is missing rides, which is noticed weeks late and blamed on arc losing
 * them. `data-enabled` carries the pause into the DOM rather than leaving it
 * to the button labels: "paused" and "quiet" are the two states this panel
 * exists to keep apart, and a reader scanning the list should not have to
 * read a button to tell which one they are looking at.
 */
function FeedRow({ feed }: { readonly feed: Feed }) {
  // The athlete reads "last checked" against *now* to judge whether the feed
  // is alive, so it is a moment on their clock, not the server's. In UTC a
  // poll from ten minutes ago looks fourteen hours stale to an athlete at
  // UTC+14 — and telling a broken feed from a quiet one is the whole job of
  // this row, which is the judgement a stamp wrong by the offset breaks.
  const timezone = useAthleteTimezone();
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: connectionsKey });
  const update = $api.useMutation("patch", "/api/v1/feeds/{feed_id}", {
    onSuccess: invalidate,
  });
  const remove = $api.useMutation("delete", "/api/v1/feeds/{feed_id}", {
    onSuccess: invalidate,
  });
  const params = { path: { feed_id: feed.id } };

  return (
    <li
      data-testid="dropbox-feed"
      data-enabled={feed.enabled}
      className={`flex flex-col gap-1 ${feed.enabled ? "" : "opacity-60"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-auto font-mono text-ink-secondary text-sm">
          {feed.remote_path === "" ? "/ (the whole Dropbox)" : feed.remote_path}
        </span>
        {feed.enabled ? null : (
          <span className="text-ink-faint text-xs">paused</span>
        )}
        <Button
          type="button"
          size="xs"
          variant="secondary"
          disabled={update.isPending}
          onClick={() =>
            update.mutate({ params, body: { enabled: !feed.enabled } })
          }
        >
          {feed.enabled ? "Pause" : "Resume"}
        </Button>
        <Button
          type="button"
          size="xs"
          variant="ghost"
          disabled={remove.isPending}
          onClick={() => remove.mutate({ params })}
        >
          Remove
        </Button>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
        {feed.last_delivery_at === null ? (
          // Not an em dash in a slot: "nothing has come through yet" is a
          // fact about setup, and it is what the athlete came here to read.
          <span className="text-ink-faint">not checked yet</span>
        ) : (
          // "Checked", not "delivered": the stamp moves on every poll arc
          // completes, including one that found an empty folder, because the
          // field means "arc heard from Dropbox at all" (see `FeedRead`). A
          // bare timestamp here read as "a ride arrived then", so a fresh
          // stamp on a rest week looked like a delivery and a broken feed
          // could not be told from a quiet one — the exact confusion the two
          // states on this row exist to keep apart.
          <span className="text-ink-faint">
            last checked{" "}
            <span data-testid="dropbox-feed-delivery" className="font-mono">
              {formatAthleteStamp(feed.last_delivery_at, timezone)}
            </span>
          </span>
        )}
        {feed.last_error === null ? null : (
          <span className="text-destructive">{feed.last_error}</span>
        )}
      </div>
    </li>
  );
}

/**
 * Walk the athlete's Dropbox and pick a folder.
 *
 * Reads the real folder tree rather than asking for a typed path: the folder
 * this is almost always pointed at is `/Apps/WahooFitness`, which nobody
 * remembers the spelling of, and a typo would produce a feed that polls a
 * folder that does not exist and reports nothing wrong.
 */
function FolderPicker({
  connection,
  onDone,
}: {
  readonly connection: Connection;
  readonly onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const folders = $api.useQuery(
    "get",
    "/api/v1/connections/{connection_id}/folders",
    {
      params: {
        path: { connection_id: connection.id },
        query: { path },
      },
    },
  );
  const watch = $api.useMutation("post", "/api/v1/feeds", {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: connectionsKey });
      onDone();
    },
  });

  const start = (remote_path: string) =>
    watch.mutate({ body: { connection_id: connection.id, remote_path } });

  return (
    <div
      data-testid="dropbox-folder-picker"
      className="flex flex-col items-start gap-2 rounded-card border border-hairline bg-inset px-3.5 py-2.5"
    >
      <div className="flex w-full flex-wrap items-center gap-2">
        <span className="mr-auto font-mono text-ink-secondary text-sm">
          {path === "" ? "/" : path}
        </span>
        {path === "" ? null : (
          <Button
            type="button"
            size="xs"
            variant="secondary"
            onClick={() => setPath(parentOf(path))}
          >
            Up one folder
          </Button>
        )}
        <Button type="button" size="xs" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>

      {folders.isPending ? (
        <p className="text-ink-muted text-sm">Reading your Dropbox…</p>
      ) : !folders.data ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(folders.error, "that Dropbox folder")}
        </p>
      ) : folders.data.items.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <p className="max-w-[62ch] text-ink-muted text-sm">
            {path === "" ? "Your Dropbox" : path} has no folders inside it.
            Watch it as it is, or start from the top.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={watch.isPending}
              onClick={() => start(path)}
            >
              Watch this folder
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={watch.isPending}
              onClick={() => start("")}
            >
              Watch the whole Dropbox
            </Button>
          </div>
        </div>
      ) : (
        <ul className="flex w-full flex-col gap-1">
          {folders.data.items.map((folder) => (
            <li
              key={folder.path_lower}
              className="flex flex-wrap items-center gap-2"
            >
              <span className="mr-auto font-mono text-ink-secondary text-sm">
                {folder.name}
              </span>
              <Button
                type="button"
                size="xs"
                variant="secondary"
                onClick={() => setPath(folder.path_lower)}
              >
                {`Open ${folder.name}`}
              </Button>
              <Button
                type="button"
                size="xs"
                disabled={watch.isPending}
                onClick={() => start(folder.path_lower)}
              >
                {`Watch ${folder.name}`}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Problems problems={apiErrorMessages(watch.error)} />
    </div>
  );
}

/** The folder one level up; `""` is the root and has no parent. */
function parentOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? "" : path.slice(0, cut);
}

/** Forgetting the account, behind a question that says what goes with it. */
function Disconnect({ connection }: { readonly connection: Connection }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const disconnect = $api.useMutation(
    "delete",
    "/api/v1/connections/{connection_id}",
    {
      onSuccess: () => {
        setAsking(false);
        queryClient.invalidateQueries({ queryKey: connectionsKey });
      },
    },
  );

  if (!asking) {
    return (
      <Button
        type="button"
        variant="destructive"
        className="self-start"
        onClick={() => setAsking(true)}
      >
        Disconnect
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <InlineConfirm
        question={`Disconnect ${connection.account_label ?? "this account"}? The ${connection.feeds.length} watched folder(s) go too, and reconnecting is the whole setup again.`}
        confirmLabel="Disconnect Dropbox"
        cancelLabel="Keep it"
        disabled={disconnect.isPending}
        onCancel={() => setAsking(false)}
        onConfirm={() =>
          disconnect.mutate({
            params: { path: { connection_id: connection.id } },
          })
        }
      />
      <Problems problems={apiErrorMessages(disconnect.error)} />
    </div>
  );
}

/** Whatever the server refused, said where the control that failed is. */
function Problems({ problems }: { readonly problems: readonly string[] }) {
  if (problems.length === 0) {
    return null;
  }
  return (
    <ul
      role="alert"
      className="flex flex-col gap-1 rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
    >
      {problems.map((entry) => (
        <li key={entry}>{entry}</li>
      ))}
    </ul>
  );
}
