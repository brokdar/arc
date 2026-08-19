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
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";

type Schemas = components["schemas"];
type CatalogueEntry = Schemas["CatalogueEntry"];
type TransportOffer = Schemas["TransportOffer"];
type StorageStatus = Schemas["StorageStatusRead"];

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
 * PR-5 owns this file next, when discovery proposes the integration behind a
 * folder it found rather than offering the path.
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
        <PickIntegration entries={addable} onPick={setKind} />
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
