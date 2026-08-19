"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

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

type Schemas = components["schemas"];
type CatalogueEntry = Schemas["CatalogueEntry"];
type TransportOffer = Schemas["TransportOffer"];
type StorageStatus = Schemas["StorageStatusRead"];
type Proposal = Schemas["IntegrationProposalRead"];
type IntegrationKind = Schemas["IntegrationKind"];

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
 */
export function AddIntegrationFlow({
  onDone,
}: {
  readonly onDone: () => void;
}) {
  const catalogue = $api.useQuery("get", "/api/v1/integration-catalogue");
  const [kind, setKind] = useState<Schemas["IntegrationKind"] | null>(null);
  const [transport, setTransport] = useState<Schemas["TransportKind"] | null>(
    null,
  );

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
        <>
          {connectedStorage === null ? null : (
            <DiscoveredIntegrations
              connectionId={connectedStorage}
              entries={addable}
              onDone={onDone}
            />
          )}
          <PickIntegration entries={addable} onPick={setKind} />
        </>
      ) : activeTransport === null ? (
        <PickTransport offers={offers} onPick={setTransport} />
      ) : (
        <CloudFolderSteps
          entry={chosen}
          offer={activeTransport}
          storage={catalogue.data.storage}
          onRecheck={() => catalogue.refetch()}
          checking={catalogue.isFetching}
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
  onDone,
}: {
  readonly connectionId: string;
  readonly entries: readonly CatalogueEntry[];
  readonly onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const discovery = $api.useQuery(
    "get",
    "/api/v1/connections/{connection_id}/discover",
    { params: { path: { connection_id: connectionId } } },
  );
  const add = $api.useMutation("post", "/api/v1/integrations", {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: integrationsKey });
      onDone();
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

/**
 * The cloud-folder transport's remaining steps: app key, account, folder.
 *
 * Rendered one at a time and only while unanswered — see the module docstring.
 */
function CloudFolderSteps({
  entry,
  offer,
  storage,
  onRecheck,
  checking,
  onDone,
}: {
  readonly entry: CatalogueEntry;
  readonly offer: TransportOffer;
  readonly storage: readonly StorageStatus[];
  readonly onRecheck: () => void;
  readonly checking: boolean;
  readonly onDone: () => void;
}) {
  const provider =
    storage.find((row) => row.provider === offer.storage) ?? null;

  if (offer.kind !== "cloud_folder" || provider === null) {
    return (
      <p role="alert" className="text-destructive text-sm">
        arc cannot set up that transport yet.
      </p>
    );
  }
  if (!provider.app_configured) {
    return <DropboxAppKeyStep onRecheck={onRecheck} checking={checking} />;
  }
  if (provider.connection_id === null) {
    return <DropboxConnectStep onConnected={onRecheck} />;
  }
  return (
    <FolderStep
      entry={entry}
      offer={offer}
      connectionId={provider.connection_id}
      onDone={onDone}
    />
  );
}

/**
 * The last step: which folder, and the write that creates the integration.
 *
 * The catalogue's default path leads, because it is right almost every time
 * and nobody remembers how `/Apps/WahooFitness` is spelled. The tree is there
 * for the athlete whose head unit uploads somewhere else — and a typed path is
 * not offered at all, because a typo produces a folder that polls nothing and
 * reports nothing wrong.
 */
function FolderStep({
  entry,
  offer,
  connectionId,
  onDone,
}: {
  readonly entry: CatalogueEntry;
  readonly offer: TransportOffer;
  readonly connectionId: string;
  readonly onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
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
    { params: { path: { connection_id: connectionId }, query: { path } } },
  );
  const add = $api.useMutation("post", "/api/v1/integrations", {
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: integrationsKey });
      onDone();
    },
  });

  const collect = (remote_path: string) => {
    // Reset first: react-query holds the previous refusal until the next
    // `mutate()`, and a 409 about a folder the athlete has moved on from
    // would sit under the one they just picked.
    add.reset();
    add.mutate({
      body: {
        kind: entry.kind,
        transport: offer.kind,
        connection_id: connectionId,
        remote_path,
      },
    });
  };

  if (discovery.data?.access_type_suspect === "app_folder") {
    // An empty tree here would be a dead end with no remedy in it: the athlete
    // would browse a Dropbox arc cannot see and conclude their rides are gone.
    return (
      <div data-testid="folder-step" className="flex w-full flex-col gap-2">
        <SectionLabel>{`Which folder holds your ${entry.display_name} files?`}</SectionLabel>
        <AppFolderAlert />
      </div>
    );
  }
  return (
    <div data-testid="folder-step" className="flex w-full flex-col gap-2">
      <SectionLabel>{`Which folder holds your ${entry.display_name} files?`}</SectionLabel>
      {offer.default_path === null ? null : (
        <div className="flex flex-wrap items-baseline gap-2">
          <Button
            type="button"
            size="sm"
            disabled={add.isPending}
            onClick={() => collect(offer.default_path ?? "")}
          >
            {`Collect ${offer.default_path}`}
          </Button>
          <span className="text-ink-muted text-sm">
            {`where ${entry.display_name} usually writes`}
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
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
      </div>

      {folders.isPending ? (
        <p className="text-ink-muted text-sm">Reading your folders…</p>
      ) : !folders.data ? (
        <p role="alert" className="text-destructive text-sm">
          {loadFailureMessage(folders.error, "that folder")}
        </p>
      ) : folders.data.items.length === 0 ? (
        <p className="max-w-[62ch] text-ink-muted text-sm">
          Nothing but files in here. Collect this folder, or go back up.
        </p>
      ) : (
        <ul data-testid="folder-tree" className="flex w-full flex-col gap-1">
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
                disabled={add.isPending}
                onClick={() => collect(folder.path_lower)}
              >
                {`Collect ${folder.path_lower}`}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Problems problems={apiErrorMessages(add.error)} />
    </div>
  );
}

/** The folder one level up; `""` is the root and has no parent. */
function parentOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? "" : path.slice(0, cut);
}
