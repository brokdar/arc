"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { SectionLabel } from "@/components/design/section-label";
import {
  DropboxAppKeyStep,
  DropboxConnectStep,
} from "@/components/settings/integrations/dropbox-connect-step";
import {
  describeProvides,
  integrationsKey,
  Problems,
} from "@/components/settings/integrations/integration-card";
import { Button } from "@/components/ui/button";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";
import { useAthleteTimezone } from "@/lib/clock";
import { formatAthleteStamp } from "@/lib/format";
import { cn } from "@/lib/utils";

type Schemas = components["schemas"];
type CatalogueEntry = Schemas["CatalogueEntry"];
type TransportOffer = Schemas["TransportOffer"];
type StorageStatus = Schemas["StorageStatusRead"];
type Proposal = Schemas["IntegrationProposalRead"];
type IntegrationKind = Schemas["IntegrationKind"];

/** The folder arc was just told to watch, and the source it belongs to. */
interface Watching {
  /** What arc stored: Dropbox's `path_lower`, the feed row's identity. */
  readonly path: string;
  /**
   * The same folder as the athlete's Dropbox capitalises it.
   *
   * `""` when nothing on the road in knew it — discovery reports one path per
   * proposal and it is the stored one — and the completion then names the
   * folder as arc stored it rather than inventing a capitalisation.
   */
  readonly displayPath: string;
  readonly displayName: string;
}

/** The folder a completed flow names on screen. Display case wherever known. */
function watchedPath(watching: Watching): string {
  return watching.displayPath || watching.path;
}

/**
 * Adding a source: pick the integration, then pick how arc should collect it.
 *
 * **The athlete picks Wahoo, never Dropbox.** That ordering is the reason this
 * component exists: the panel it replaced opened on a file host and a folder
 * tree, so the one screen where the athlete makes a decision showed them a
 * filesystem path and left them to work out it meant their bike computer.
 *
 * Steps are **derived, not counted**. Each one asks for something arc does not
 * have yet — an app key, an account, a folder — so a step whose answer is
 * already stored is not rendered at all. A wizard with fixed pages would
 * re-ask the athlete for a Dropbox account they connected last month, and a
 * completed step re-asked is one nobody can tell from a failure.
 *
 * **Discovery leads, the catalogue is the fallback.** With an account already
 * connected arc looks first and says what it found — "Wahoo, 3 activity files,
 * newest 16.08 20:12" — because the athlete came here to make their rides
 * appear, not to describe their filesystem. Picking from the catalogue is
 * still right there for the source arc could not find.
 *
 * **And it ends on a sentence.** Whichever road got here, the last thing the
 * athlete did was tell arc to go and read a folder on a cadence; the flow used
 * to acknowledge that by closing itself, which is the same signal a crash
 * gives. `FlowComplete` says what arc will now do and how to stop it, and the
 * athlete dismisses it.
 */
export function AddIntegrationFlow({
  onDone,
  initialKind = null,
}: {
  readonly onDone: () => void;
  /**
   * The integration this flow was already on, when it is being resumed.
   *
   * Set when the athlete comes back from authorising Dropbox: the tab left
   * the application and returned, so the flow reopens where it was rather
   * than asking again for a source they already picked (see
   * `lib/dropbox-redirect`). `null` is a fresh start at the catalogue.
   */
  readonly initialKind?: Schemas["IntegrationKind"] | null;
}) {
  const catalogue = $api.useQuery("get", "/api/v1/integration-catalogue");
  const [kind, setKind] = useState<Schemas["IntegrationKind"] | null>(
    initialKind,
  );
  const [transport, setTransport] = useState<Schemas["TransportKind"] | null>(
    null,
  );
  // Set the moment a folder is watched, by either road in. It is the flow's
  // terminal state rather than a toast: the athlete reads it and closes it.
  const [watching, setWatching] = useState<Watching | null>(null);

  const addable = (catalogue.data?.items ?? []).filter((item) => item.addable);
  const chosen = addable.find((item) => item.kind === kind) ?? null;
  const offers = chosen?.transports ?? [];
  // One transport is not a question: asking "how?" with a single answer is a
  // page the athlete clicks through without reading.
  const only = offers.length === 1 ? offers[0] : null;
  const activeTransport =
    offers.find((offer) => offer.kind === transport) ?? only ?? null;
  // Discovery needs an account to look through. The first connected one, not
  // one per provider: there is a single athlete, and a second cloud account is
  // a thing arc has never been able to hold.
  const connectedStorage =
    (catalogue.data?.storage ?? []).find((row) => row.connection_id !== null)
      ?.connection_id ?? null;

  return (
    <div
      data-testid="add-integration-flow"
      className="flex flex-col items-start gap-3 rounded-card border border-hairline bg-inset px-3.5 py-3"
    >
      <div className="flex w-full flex-wrap items-baseline gap-2">
        <SectionLabel className="mr-auto">Add an integration</SectionLabel>
        <Button type="button" size="xs" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>

      {catalogue.isPending ? (
        <p className="text-ink-muted text-sm">Reading what arc can collect…</p>
      ) : !catalogue.data ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(catalogue.error, "what arc can collect")}
        </p>
      ) : chosen === null ? (
        watching !== null ? (
          <FlowComplete watching={watching} onDone={onDone} />
        ) : (
          <>
            {connectedStorage === null ? null : (
              <DiscoveredIntegrations
                connectionId={connectedStorage}
                entries={addable}
                onWatching={setWatching}
              />
            )}
            <PickIntegration entries={addable} onPick={setKind} />
          </>
        )
      ) : activeTransport === null ? (
        <PickTransport offers={offers} onPick={setTransport} />
      ) : (
        <CloudFolderSteps
          entry={chosen}
          offer={activeTransport}
          storage={catalogue.data.storage}
          onRecheck={() => catalogue.refetch()}
          checking={catalogue.isFetching}
          watching={watching}
          onWatching={setWatching}
          onDone={onDone}
        />
      )}
    </div>
  );
}

/**
 * What arc found in the connected account, named as the sources behind it.
 *
 * The moment this whole surface exists for. Before it, the athlete was offered
 * `/apps/wahoofitness` with a file count and left to work out that it meant
 * their bike computer — a fact arc held all along, in the catalogue, and never
 * said. Accepting posts the proposal to the **same** `POST /api/v1/integrations`
 * the manual path uses, so there is one write path and one set of refusals.
 */
function DiscoveredIntegrations({
  connectionId,
  entries,
  onWatching,
}: {
  readonly connectionId: string;
  readonly entries: readonly CatalogueEntry[];
  readonly onWatching: (watching: Watching) => void;
}) {
  const queryClient = useQueryClient();
  const discovery = $api.useQuery(
    "get",
    "/api/v1/connections/{connection_id}/discover",
    { params: { path: { connection_id: connectionId } } },
  );
  const add = $api.useMutation("post", "/api/v1/integrations", {
    // What was sent, not what was on screen: the proposal the athlete accepted
    // is the only thing that says which folder arc is now watching.
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: integrationsKey });
      onWatching({
        path: variables?.body?.remote_path ?? "",
        // Discovery reports the stored path and nothing else, so there is no
        // display spelling to carry here — `watchedPath` falls back to it.
        displayPath: "",
        displayName:
          entries.find((entry) => entry.kind === variables?.body?.kind)
            ?.display_name ?? "this source",
      });
    },
  });
  // Only for a proposal arc could not name: the source the athlete chose, by
  // folder, so two unnamed folders do not answer for each other.
  const [named, setNamed] = useState<Record<string, IntegrationKind>>({});

  const accept = (proposal: Proposal, kind: IntegrationKind) => {
    // Reset first: react-query holds the previous refusal until the next
    // `mutate()`, and a 409 about a folder the athlete has moved on from
    // would sit under the one they just picked.
    add.reset();
    add.mutate({
      body: {
        kind,
        transport: proposal.transport,
        connection_id: proposal.connection_id,
        remote_path: proposal.path,
      },
    });
  };

  if (discovery.isPending) {
    return (
      <p className="text-ink-muted text-sm">
        Looking for training data in your Dropbox…
      </p>
    );
  }
  if (!discovery.data) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {loadFailureMessage(discovery.error, "what is in your Dropbox")}
      </p>
    );
  }
  if (discovery.data.access_type_suspect === "app_folder") {
    return <AppFolderAlert />;
  }
  return (
    <div
      data-testid="discovery"
      className="flex w-full flex-col items-start gap-2"
    >
      <SectionLabel>What arc found</SectionLabel>
      {discovery.data.proposals.length === 0 ? (
        // UI convention 3: the missing input, and the action that supplies it
        // — which is the catalogue immediately below.
        <p className="max-w-[62ch] text-ink-muted text-sm">
          arc found no training data in the folders it can see. Pick the source
          below and choose the folder yourself.
        </p>
      ) : (
        <ul className="flex w-full flex-col gap-1.5">
          {discovery.data.proposals.map((proposal) => (
            <ProposalRow
              key={proposal.path}
              proposal={proposal}
              entries={entries}
              named={named[proposal.path] ?? null}
              onName={(kind) =>
                setNamed((current) => ({ ...current, [proposal.path]: kind }))
              }
              onAccept={(kind) => accept(proposal, kind)}
              busy={add.isPending}
            />
          ))}
        </ul>
      )}
      <Problems problems={apiErrorMessages(add.error)} />
    </div>
  );
}

/** One folder arc found: what it is, how much is in it, and how to take it. */
function ProposalRow({
  proposal,
  entries,
  named,
  onName,
  onAccept,
  busy,
}: {
  readonly proposal: Proposal;
  readonly entries: readonly CatalogueEntry[];
  readonly named: IntegrationKind | null;
  readonly onName: (kind: IntegrationKind) => void;
  readonly onAccept: (kind: IntegrationKind) => void;
  readonly busy: boolean;
}) {
  const timezone = useAthleteTimezone();
  const kind = proposal.kind ?? named;

  return (
    <li
      data-testid={`proposal-${proposal.kind ?? proposal.path}`}
      className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-card border border-hairline px-3 py-2"
    >
      <span className="font-medium text-ink-primary text-sm">
        {proposal.display_name}
      </span>
      <span className="font-mono text-ink-secondary text-sm">
        {`${proposal.activity_files} activity files`}
      </span>
      {proposal.newest_at === null ? null : (
        <span className="font-mono text-ink-muted text-sm">
          {`newest ${formatAthleteStamp(proposal.newest_at, timezone)}`}
        </span>
      )}
      <span className="mr-auto font-mono text-ink-muted text-sm">
        {proposal.path || "the Dropbox root"}
      </span>
      {proposal.configured ? (
        <span className="text-ink-muted text-sm">
          arc is already collecting this folder.
        </span>
      ) : proposal.kind === null ? (
        <>
          <NativeSelect
            size="sm"
            aria-label={`Which source writes to ${proposal.path || "the Dropbox root"}?`}
            value={named ?? ""}
            onChange={(event) => onName(event.target.value as IntegrationKind)}
          >
            <NativeSelectOption value="">Pick a source</NativeSelectOption>
            {entries.map((entry) => (
              <NativeSelectOption key={entry.kind} value={entry.kind}>
                {entry.display_name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <Button
            type="button"
            size="xs"
            disabled={busy || kind === null}
            onClick={() => kind !== null && onAccept(kind)}
          >
            Add this folder
          </Button>
        </>
      ) : (
        <Button
          type="button"
          size="xs"
          disabled={busy}
          onClick={() => onAccept(proposal.kind as IntegrationKind)}
        >
          {`Add ${proposal.display_name}`}
        </Button>
      )}
    </li>
  );
}

/**
 * The diagnosis that replaces an empty folder tree.
 *
 * An App-folder Dropbox app can only ever see one directory of its own, so arc
 * — which is not that directory — sees a Dropbox with nothing in it at all.
 * Worded as something to check rather than an accusation, because no Dropbox
 * API reports an app's access type and the remedy is not free: Dropbox cannot
 * change it after the app is created.
 */
function AppFolderAlert() {
  return (
    <p
      role="alert"
      className="max-w-[62ch] rounded-card border border-danger-border bg-danger-surface px-3.5 py-2.5 text-destructive text-sm"
    >
      arc can see nothing at all in this Dropbox — not even the /Apps folder.
      That usually means the Dropbox app was registered with{" "}
      <strong>App folder</strong> access, which limits it to one directory of
      its own. Dropbox cannot change an app&rsquo;s access type after it is
      created, so the fix is to register a new app with Full Dropbox access and
      connect it here.
    </p>
  );
}

/** Step one: what is the source, in the athlete's own words. */
function PickIntegration({
  entries,
  onPick,
}: {
  readonly entries: readonly CatalogueEntry[];
  readonly onPick: (kind: Schemas["IntegrationKind"]) => void;
}) {
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="max-w-[62ch] text-ink-muted text-sm">
        Pick where your training data comes from. arc lists only what it can
        actually collect today.
      </p>
      <ul className="flex flex-col gap-1.5">
        {entries.map((entry) => (
          <li key={entry.kind} className="flex flex-wrap items-baseline gap-2">
            <Button type="button" size="sm" onClick={() => onPick(entry.kind)}>
              {entry.display_name}
            </Button>
            <span className="text-ink-muted text-sm">
              {describeProvides(entry.data_kinds)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Step two, when there is more than one way to collect the same source. */
function PickTransport({
  offers,
  onPick,
}: {
  readonly offers: readonly TransportOffer[];
  readonly onPick: (kind: Schemas["TransportKind"]) => void;
}) {
  return (
    <div
      data-testid="transport-step"
      className="flex flex-col items-start gap-2"
    >
      <SectionLabel>How should arc collect it?</SectionLabel>
      <ul className="flex flex-col gap-1.5">
        {offers.map((offer) => (
          <li key={offer.kind}>
            <Button type="button" size="sm" onClick={() => onPick(offer.kind)}>
              {offer.kind === "cloud_folder"
                ? "From a folder in cloud storage"
                : "Directly from the vendor"}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Where a step of the map stands. Never carried by colour alone. */
type StepState = "done" | "current" | "upcoming";

/** What each marker is announced as, since a glyph has no accessible name. */
const STEP_STATE_WORDS: Readonly<Record<StepState, string>> = {
  done: "Done",
  current: "Doing this now",
  upcoming: "Still to come",
};

/**
 * The cloud-folder transport's steps — app key, account, folder — as a map.
 *
 * Steps stay **derived, not counted** (see the module docstring): the same
 * three predicates that used to choose which single step to render now choose
 * each row's *state*, so the map and the flow cannot disagree and a stored
 * answer is still never re-asked. What changed is that the derivation's output
 * is visible. This flow spans two applications and an OAuth round trip, and
 * rendering only the current step left the athlete unable to see how much
 * remained, what had already worked, or that a step they finished had counted.
 *
 * A completed row summarises its answer rather than hiding it, and the current
 * step's own content renders inside its row: a map that scrolls away from the
 * work it describes is a second thing to keep track of.
 */
function CloudFolderSteps({
  entry,
  offer,
  storage,
  onRecheck,
  checking,
  watching,
  onWatching,
  onDone,
}: {
  readonly entry: CatalogueEntry;
  readonly offer: TransportOffer;
  readonly storage: readonly StorageStatus[];
  readonly onRecheck: () => void;
  readonly checking: boolean;
  readonly watching: Watching | null;
  readonly onWatching: (watching: Watching) => void;
  readonly onDone: () => void;
}) {
  // The key as the athlete typed it, for as long as this flow is open. arc
  // can never read a stored key back — `GET /dropbox/setup` says whether one
  // is set and from which source, not what it is — so the tail is shown for a
  // key this flow watched go in, and the source is named for every other one.
  // Inventing the missing characters would be the same pretence the rest of
  // this feature exists to stop.
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const setup = $api.useQuery("get", "/api/v1/connections/dropbox/setup");
  const provider =
    storage.find((row) => row.provider === offer.storage) ?? null;

  if (offer.kind !== "cloud_folder" || provider === null) {
    return (
      <p role="alert" className="text-destructive text-sm">
        arc cannot set up that transport yet.
      </p>
    );
  }

  const connectionId = provider.connection_id;
  const appKeyState: StepState = provider.app_configured ? "done" : "current";
  const accountState: StepState = !provider.app_configured
    ? "upcoming"
    : connectionId === null
      ? "current"
      : "done";
  const folderState: StepState =
    watching !== null
      ? "done"
      : accountState === "done"
        ? "current"
        : "upcoming";

  return (
    <div
      data-testid="step-map"
      className="flex w-full flex-col items-start gap-2"
    >
      {/* Named on every step, because the next two are about Dropbox and the
          athlete came here to add a bike computer. */}
      <p className="max-w-[62ch] text-ink-muted text-sm">
        Adding{" "}
        <strong className="font-medium text-ink-secondary">
          {entry.display_name}
        </strong>
        . arc keeps each answer, so nothing here is asked twice.
      </p>
      <ol className="flex w-full flex-col">
        <StepRow
          id="app-key"
          name="Register a Dropbox app"
          state={appKeyState}
          answer={
            appKeyState === "done"
              ? describeAppKey(savedKey, setup.data?.source ?? null)
              : null
          }
        >
          {appKeyState === "current" ? (
            <DropboxAppKeyStep
              onSaved={(appKey) => {
                setSavedKey(appKey);
                onRecheck();
              }}
              checking={checking}
            />
          ) : null}
        </StepRow>

        <StepRow
          id="account"
          name="Connect the Dropbox account"
          state={accountState}
          answer={
            accountState === "done"
              ? (provider.account_label ?? "an unnamed account")
              : null
          }
        >
          {accountState === "current" ? (
            <DropboxConnectStep
              onConnected={onRecheck}
              integrationKind={entry.kind}
            />
          ) : null}
        </StepRow>

        <StepRow
          id="folder"
          name="Choose the folder arc watches"
          state={folderState}
          answer={
            watching === null ? null : (
              <span className="font-mono">
                {watchedPath(watching) || "the Dropbox root"}
              </span>
            )
          }
        >
          {folderState === "current" && connectionId !== null ? (
            <FolderStep
              entry={entry}
              offer={offer}
              connectionId={connectionId}
              onWatching={onWatching}
            />
          ) : watching !== null ? (
            <FlowComplete watching={watching} onDone={onDone} />
          ) : null}
        </StepRow>
      </ol>
    </div>
  );
}

/**
 * How a done app-key step names the answer it is holding.
 *
 * The tail of the key when this flow saw it entered — enough to recognise it
 * by, not enough to be it — and otherwise the *source*, which is the only
 * thing arc knows about a key it did not watch arrive and is the difference
 * the athlete acts on: a stored key is replaced here, `DROPBOX__APP_KEY`
 * needs an edit and a restart.
 */
function describeAppKey(
  savedKey: string | null,
  source: Schemas["SettingSource"] | null,
): React.ReactNode {
  if (savedKey !== null && source === "stored") {
    return (
      <span className="font-mono">
        {savedKey.length <= 4 ? "…" : `…${savedKey.slice(-4)}`}
      </span>
    );
  }
  if (source === "environment") {
    return "key from DROPBOX__APP_KEY";
  }
  return source === "stored" ? "key saved in arc" : "app key set";
}

/**
 * One step of the map: where it stands, what it answered, and its own work.
 *
 * Three channels say the state and none of them is colour on its own — a
 * glyph (tick, filled dot, empty ring) carrying an accessible name, the
 * weight of the step's name, and the ink it is drawn in.
 */
function StepRow({
  id,
  name,
  state,
  answer,
  children,
}: {
  readonly id: string;
  readonly name: string;
  readonly state: StepState;
  readonly answer?: React.ReactNode;
  readonly children?: React.ReactNode;
}) {
  return (
    <li
      data-testid={`step-${id}`}
      data-state={state}
      className="w-full border-hairline border-b py-2.5 first:pt-0 last:border-b-0 last:pb-0"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <StepMarker state={state} />
        <span
          className={cn(
            "text-sm",
            state === "current"
              ? "font-semibold text-ink"
              : state === "done"
                ? "text-ink-secondary"
                : "text-ink-faint",
          )}
        >
          {name}
        </span>
        {answer === null || answer === undefined ? null : (
          <span className="text-ink-muted text-sm">{answer}</span>
        )}
      </div>
      {children ? <div className="mt-2 pl-5">{children}</div> : null}
    </li>
  );
}

/** The tick, dot or ring in a fixed-width slot so the names line up. */
function StepMarker({ state }: { readonly state: StepState }) {
  return (
    <span
      role="img"
      aria-label={STEP_STATE_WORDS[state]}
      className="inline-flex w-3 shrink-0 items-center justify-center"
    >
      {state === "done" ? (
        <span
          aria-hidden
          className="text-status-completed text-sm leading-none"
        >
          ✓
        </span>
      ) : state === "current" ? (
        <span aria-hidden className="size-2 rounded-full bg-accent" />
      ) : (
        <span
          aria-hidden
          className="size-2 rounded-full border border-hairline-strong"
        />
      )}
    </span>
  );
}

/**
 * The end of the flow: what arc will do from now on, and how to stop it.
 *
 * The flow used to close itself here, which is the signal a crash gives. The
 * athlete has just told arc to read a folder on a cadence — a standing
 * arrangement, not a one-off — so it is stated: when the first check happens,
 * that nothing needs uploading afterwards, and where the two controls that
 * undo it live. The athlete dismisses it.
 */
function FlowComplete({
  watching,
  onDone,
}: {
  readonly watching: Watching;
  readonly onDone: () => void;
}) {
  return (
    <div
      data-testid="flow-complete"
      role="status"
      className="flex w-full flex-col items-start gap-2 rounded-card border border-hairline bg-inset px-3.5 py-3"
    >
      <p className="max-w-[62ch] text-sm">
        arc is watching{" "}
        <span className="font-mono text-ink-secondary">
          {watchedPath(watching) || "the whole of your Dropbox"}
        </span>{" "}
        for {watching.displayName}.
      </p>
      <p className="max-w-[62ch] text-ink-muted text-sm">
        The first check runs in the next few minutes, and arc keeps looking
        every few minutes after that. Rides appear here on their own — there is
        nothing left to upload by hand.
      </p>
      <p className="max-w-[62ch] text-ink-muted text-sm">
        Pause it or Stop watching it whenever you like: both controls are in
        Settings, under {watching.displayName}.
      </p>
      <Button type="button" onClick={onDone}>
        Done
      </Button>
    </div>
  );
}

/**
 * Where the picker is standing, in both of the spellings a folder has.
 *
 * `lower` is Dropbox's `path_lower`: what the listing is requested by, what a
 * feed row stores, and what `uq_feeds_connection_id_remote_path` is written
 * against. `display` is the athlete's own capitalisation, and the only one
 * that ever reaches the screen. Carrying both is what lets the breadcrumb read
 * `/Apps/WahooFitness` while the write still stores `/apps/wahoofitness`.
 */
interface Here {
  readonly lower: string;
  readonly display: string;
}

/** The Dropbox root, which Dropbox itself spells as the empty path. */
const DROPBOX_ROOT: Here = { lower: "", display: "" };

/** The listing on screen: which folder it describes, and what it said. */
interface Shown {
  readonly at: Here;
  readonly page: Schemas["FolderList"];
}

/**
 * The last step: which folder, and the write that creates the integration.
 *
 * **One decision, one action.** Every row used to carry a commit button beside
 * an open button, so the screen offered as many irreversible actions as there
 * were folders and named none of them in the athlete's vocabulary ("Collect").
 * Now a row does one thing — it opens — and watching is a single action scoped
 * to the folder the athlete is *standing in*, with what it means and how to
 * undo it beside it. The place is stated by the breadcrumb and by the contents
 * line above it, so the action never has to be read to know what it applies to.
 *
 * **Nothing on this screen is spelled the way arc stores it.** `path_lower` is
 * the identity of a folder and a lie about its name; showing it cost a real
 * run an hour chasing a case-sensitivity fault that did not exist.
 *
 * The catalogue's default path still leads, because it is right almost every
 * time and nobody remembers how `/Apps/WahooFitness` is spelled — but it
 * *navigates* there rather than committing, so the same contents line proves
 * the guess before the athlete acts on it. A typed path is not offered at all,
 * because a typo produces a folder that polls nothing and reports nothing
 * wrong.
 */
function FolderStep({
  entry,
  offer,
  connectionId,
  onWatching,
}: {
  readonly entry: CatalogueEntry;
  readonly offer: TransportOffer;
  readonly connectionId: string;
  readonly onWatching: (watching: Watching) => void;
}) {
  const queryClient = useQueryClient();
  const [here, setHere] = useState<Here>(DROPBOX_ROOT);
  // The last listing that came back, kept so a failed one does not take the
  // screen with it: the athlete stays where they were, with the tree they can
  // still click, and the failure is one line under the breadcrumb.
  const [shown, setShown] = useState<Shown | null>(null);
  // The folder the pending write is about. A ref rather than state because it
  // is read only by the success handler, and it is set immediately before
  // `mutate` — so it is by construction the folder that was sent.
  const wanted = useRef<Here>(DROPBOX_ROOT);
  // The same read the proposals came from, so it costs nothing: react-query
  // serves both from one request. Here it answers a different question —
  // whether a tree with nothing in it means the athlete has nothing, or that
  // arc is not allowed to see it.
  const discovery = $api.useQuery(
    "get",
    "/api/v1/connections/{connection_id}/discover",
    { params: { path: { connection_id: connectionId } } },
  );
  const folders = $api.useQuery(
    "get",
    "/api/v1/connections/{connection_id}/folders",
    {
      params: {
        path: { connection_id: connectionId },
        query: { path: here.lower },
      },
    },
  );
  const add = $api.useMutation("post", "/api/v1/integrations", {
    // The folder that was *sent*, so the sentence the athlete reads at the end
    // names what arc actually stored rather than what was last hovered.
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: integrationsKey });
      onWatching({
        path: variables?.body?.remote_path ?? "",
        displayPath: wanted.current.display,
        displayName: entry.display_name,
      });
    },
  });

  const page = folders.data;
  useEffect(() => {
    if (page !== undefined) {
      setShown({
        // The athlete's own spelling wins when the road in knew it — a row
        // click carries it. The server's is the fallback, for a jump made by
        // path alone: it derives the folder's display name from the entries
        // in it, and echoes the request only for a folder with nothing in it.
        at: { lower: here.lower, display: here.display || page.path_display },
        page,
      });
    }
  }, [page, here]);

  const watch = (target: Here) => {
    wanted.current = target;
    // Reset first: react-query holds the previous refusal until the next
    // `mutate()`, and a 409 about a folder the athlete has moved on from
    // would sit under the one they just picked.
    add.reset();
    add.mutate({
      body: {
        kind: entry.kind,
        transport: offer.kind,
        connection_id: connectionId,
        remote_path: target.lower,
      },
    });
  };

  if (discovery.data?.access_type_suspect === "app_folder") {
    // An empty tree here would be a dead end with no remedy in it: the athlete
    // would browse a Dropbox arc cannot see and conclude their rides are gone.
    return (
      <div data-testid="folder-step" className="flex w-full flex-col gap-2">
        {/* The step's name is on its row in the map; this is the question the
            athlete is actually answering, in their own vocabulary. */}
        <p className="max-w-[62ch] text-ink-secondary text-sm">
          {`Which folder holds your ${entry.display_name} files?`}
        </p>
        <AppFolderAlert />
      </div>
    );
  }

  const at = shown?.at ?? DROPBOX_ROOT;
  const listing = shown?.page ?? null;

  return (
    <div data-testid="folder-step" className="flex w-full flex-col gap-3">
      <SectionLabel>{`Which folder holds your ${entry.display_name} files?`}</SectionLabel>

      {offer.default_path === null ? null : (
        // AC-21: the shortcut and the tree are one sentence, not two
        // unexplained controls. The rationale used to live in this component's
        // docstring, where the athlete could not read it.
        <div className="flex flex-col items-start gap-1.5">
          <p className="max-w-[62ch] text-ink-muted text-sm">
            {`${entry.display_name} usually writes to one folder — go straight ` +
              "there. Browse below instead if your head unit files somewhere " +
              "else."}
          </p>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() =>
              setHere({ lower: offer.default_path ?? "", display: "" })
            }
          >
            {`Go to ${entry.display_name}'s folder`}
          </Button>
        </div>
      )}

      <FolderBreadcrumb at={at} onNavigate={setHere} />

      {folders.isError ? (
        <div
          role="alert"
          className="flex max-w-[62ch] flex-wrap items-center gap-x-3 gap-y-1.5 rounded-card border border-danger-border bg-danger-surface px-3 py-2 text-destructive text-sm"
        >
          <span className="mr-auto">
            {loadFailureMessage(folders.error, "that folder")}
          </span>
          <Button
            type="button"
            size="xs"
            variant="secondary"
            disabled={folders.isFetching}
            onClick={() => folders.refetch()}
          >
            Try again
          </Button>
        </div>
      ) : null}

      {listing === null ? (
        <p className="text-ink-muted text-sm">Reading your folders…</p>
      ) : (
        <div
          // Dimmed while the folder the athlete just clicked is still being
          // read: what is on screen describes the folder they came *from*, and
          // a tree that looks live while it answers for somewhere else is how
          // a slow Dropbox turns into a click on the wrong row.
          className={cn(
            "flex w-full flex-col gap-3",
            at.lower === here.lower ? null : "opacity-60",
          )}
        >
          <FolderContents listing={listing} />

          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <Button
              type="button"
              size="sm"
              disabled={add.isPending}
              onClick={() => watch(at)}
            >
              Watch this folder
            </Button>
            <span className="max-w-[52ch] text-ink-muted text-sm">
              arc checks it every few minutes for new rides. Pause or stop
              watching any time in Settings.
            </span>
          </div>

          {listing.items.length === 0 ? null : (
            <ul
              data-testid="folder-tree"
              className="flex w-full flex-col border-hairline border-t pt-2"
            >
              {listing.items.map((folder) => (
                <li key={folder.path_lower} className="w-full">
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-card border border-transparent px-2 py-1.5 text-left text-ink-secondary text-sm hover:border-hairline hover:bg-inset focus-visible:border-hairline focus-visible:bg-inset"
                    onClick={() =>
                      setHere({
                        lower: folder.path_lower,
                        display: folder.path_display,
                      })
                    }
                  >
                    <span className="mr-auto">{folder.name}</span>
                    <span aria-hidden className="text-ink-faint">
                      ›
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <Problems problems={apiErrorMessages(add.error)} />
    </div>
  );
}

/**
 * Where the athlete is, one navigable segment per folder.
 *
 * Replaces a flat monospace `path_lower` and a lone `Up one folder` button:
 * one told the athlete where they were in a spelling that matched nothing they
 * could see in Dropbox, and the other could only undo the last step, so
 * getting back to the root from four levels down was four clicks and no way to
 * see how far down four was.
 *
 * The **current** folder is never a button — there is nowhere for it to go,
 * and at the root that leaves exactly one plain segment.
 */
function FolderBreadcrumb({
  at,
  onNavigate,
}: {
  readonly at: Here;
  readonly onNavigate: (here: Here) => void;
}) {
  const lower = at.lower.split("/").filter(Boolean);
  const display = at.display.split("/").filter(Boolean);
  const segments = lower.map((_, index) => ({
    name: display[index] ?? lower[index],
    here: {
      lower: `/${lower.slice(0, index + 1).join("/")}`,
      display: `/${display.slice(0, index + 1).join("/")}`,
    },
  }));

  return (
    <nav aria-label="Folder path" className="w-full">
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-sm">
        <BreadcrumbSegment
          name="Dropbox"
          current={segments.length === 0}
          onNavigate={() => onNavigate(DROPBOX_ROOT)}
        />
        {segments.map((segment, index) => (
          <BreadcrumbSegment
            key={segment.here.lower}
            name={segment.name}
            current={index === segments.length - 1}
            onNavigate={() => onNavigate(segment.here)}
          />
        ))}
      </ol>
    </nav>
  );
}

/** One segment, and the separator that precedes every one but the first. */
function BreadcrumbSegment({
  name,
  current,
  onNavigate,
}: {
  readonly name: string;
  readonly current: boolean;
  readonly onNavigate: () => void;
}) {
  return (
    <li className="flex items-center gap-1">
      {name === "Dropbox" ? null : (
        <span aria-hidden className="text-ink-faint">
          /
        </span>
      )}
      {current ? (
        <span aria-current="page" className="font-medium text-ink">
          {name}
        </span>
      ) : (
        <button
          type="button"
          className="rounded-sm text-ink-muted underline decoration-hairline-strong underline-offset-2 hover:text-ink-secondary focus-visible:text-ink-secondary"
          onClick={onNavigate}
        >
          {name}
        </button>
      )}
    </li>
  );
}

/**
 * What is in the folder the athlete is standing in, as two counts and a claim.
 *
 * Replaces "Nothing but files in here", which the old response could not
 * support: it listed folders only, so an empty list meant "no subfolders" and
 * the sentence asserted files nobody had counted. The gap between the two
 * numbers is the screenshots and the CSV exports, and it is how an athlete
 * recognises the folder their head unit actually writes to.
 */
function FolderContents({
  listing,
}: {
  readonly listing: Schemas["FolderList"];
}) {
  const folders = listing.items.length;
  const files = listing.file_count;
  const readable = listing.supported_file_count;

  return (
    <p className="max-w-[62ch] text-ink-muted text-sm">
      {folders === 0 && files === 0 ? (
        "This folder is empty — no subfolders and no files."
      ) : files === 0 ? (
        "No files here yet, only subfolders."
      ) : (
        <>
          {folders === 0 ? "No subfolders. " : null}
          <span className="font-mono">{files}</span>
          {files === 1 ? " file here, " : " files here, "}
          {readable === 0 ? (
            "none arc can read."
          ) : (
            <>
              <span className="font-mono">{readable}</span> arc can read.
            </>
          )}
        </>
      )}
    </p>
  );
}
