"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { InlineConfirm } from "@/components/design/confirm";
import { SectionLabel } from "@/components/design/section-label";
import { Button } from "@/components/ui/button";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";
import { useAthleteTimezone } from "@/lib/clock";
import { formatAthleteStamp } from "@/lib/format";

type Schemas = components["schemas"];
export type Integration = Schemas["IntegrationRead"];
type IntegrationFolder = Schemas["IntegrationFolderRead"];
type DataKind = Schemas["DataKind"];

/** The query every write on this surface invalidates. */
export const integrationsKey = $api.queryOptions(
  "get",
  "/api/v1/integrations",
).queryKey;

/**
 * What each `DataKind` means to the athlete.
 *
 * "Rides and workouts", not "recordings": `DataKind` names arc's *destination
 * subsystem*, which is the right vocabulary for the code and the wrong one for
 * a person deciding whether to add a source. Deliberately says nothing about
 * the sport — a folder cannot tell you what wrote a file, which is the whole
 * argument in `app.domain.integrations.DataKind`.
 */
const PROVIDES_WORDS: Readonly<Record<DataKind, string>> = {
  recordings: "Rides and workouts",
  wellness: "Wellness readings",
};

/** How the storage provider is named on screen. */
const STORAGE_WORDS: Readonly<Record<Schemas["ConnectionProvider"], string>> = {
  dropbox: "Dropbox",
};

/** The three sentences every entry states, whatever kind of source it is. */
export function describeProvides(kinds: readonly DataKind[]): string {
  return kinds.length === 0
    ? "arc has not been told what this brings in."
    : kinds.map((kind) => PROVIDES_WORDS[kind]).join(" and ");
}

/**
 * One source arc collects from, as one entry in the settings list.
 *
 * Every entry answers the same three questions in the same three slots —
 * **what data**, **where from**, **what to configure** — because that is the
 * gap this whole surface exists to close. Before it, Settings showed the
 * athlete a filesystem path under a heading that named a file host, and left
 * them to work out that it meant their bike computer.
 *
 * A `<section>` with an `aria-label`, so the entry has an accessible name and
 * that name is what the athlete calls the source: "Wahoo", never "Dropbox".
 */
export function IntegrationCard({
  integration,
}: {
  readonly integration: Integration;
}) {
  return (
    <li>
      <section
        data-testid="integration"
        data-kind={integration.kind ?? "unclassified"}
        aria-label={integration.display_name}
        className="flex flex-col gap-2.5 rounded-card border border-hairline bg-inset px-3.5 py-3"
      >
        <div className="flex flex-wrap items-baseline gap-x-2.5">
          <h3 className="mr-auto font-semibold text-ink text-sm">
            {integration.display_name}
          </h3>
          <RemoveControl integration={integration} />
        </div>

        <Slot label="Brings in" testId="integration-provides">
          {describeProvides(integration.data_kinds)}
        </Slot>

        <Slot label="Where from" testId="integration-source">
          {integration.storage === null
            ? "Nowhere yet"
            : STORAGE_WORDS[integration.storage]}
          {integration.folders.length === 0 ? null : (
            <ul className="mt-1 flex flex-col gap-0.5">
              {integration.folders.map((folder) => (
                <FolderLine
                  key={folder.feed_id}
                  folder={folder}
                  integrationId={integration.id}
                />
              ))}
            </ul>
          )}
        </Slot>

        <Slot label="To configure" testId="integration-setup">
          {integration.prompt ?? describeSetup(integration)}
        </Slot>
      </section>
    </li>
  );
}

/** What is left to do when the server has no prompt of its own. */
function describeSetup(integration: Integration): string {
  const count = integration.folders.length;
  const broken = integration.folders.find(
    (folder) => folder.connection_status !== "connected",
  );
  if (broken) {
    return (
      broken.connection_error ??
      "The account this collects through needs attention before anything will arrive."
    );
  }
  return count === 1
    ? "Collecting from one folder. Add another, or remove this source."
    : `Collecting from ${count} folders. Add another, or remove this source.`;
}

/** One of the three things every entry states, with the caption that says which. */
export function Slot({
  label,
  testId,
  children,
}: {
  readonly label: string;
  readonly testId: string;
  readonly children: React.ReactNode;
}) {
  return (
    <div data-testid={testId}>
      <SectionLabel>{label}</SectionLabel>
      <div className="mt-0.5 max-w-[62ch] text-ink-muted text-sm">
        {children}
      </div>
    </div>
  );
}

/**
 * One watched folder: the path, and whether anything is arriving through it.
 *
 * The delivery stamp is on the athlete's clock, not the server's: they read
 * "last checked" against *now* to tell a broken folder from a quiet one, and
 * in UTC a poll from ten minutes ago looks fourteen hours stale at UTC+14.
 */
function FolderLine({
  folder,
  integrationId,
}: {
  readonly folder: IntegrationFolder;
  readonly integrationId: string;
}) {
  const timezone = useAthleteTimezone();
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: integrationsKey });
  const params = {
    path: { integration_id: integrationId, folder_id: folder.feed_id },
  };
  const update = $api.useMutation(
    "patch",
    "/api/v1/integrations/{integration_id}/folders/{folder_id}",
    { onSuccess: invalidate },
  );
  const remove = $api.useMutation(
    "delete",
    "/api/v1/integrations/{integration_id}/folders/{folder_id}",
    { onSuccess: invalidate },
  );

  return (
    <li
      data-testid="integration-folder"
      data-state={folder.state}
      data-enabled={folder.enabled}
      className={`flex flex-wrap items-baseline gap-x-2 ${folder.enabled ? "" : "opacity-60"}`}
    >
      <span className="font-mono text-ink-secondary text-sm">
        {folder.remote_path === "" ? "/ (everything)" : folder.remote_path}
      </span>
      {folder.enabled ? null : (
        <span className="text-ink-faint text-xs">paused</span>
      )}
      {folder.last_delivery_at === null ? (
        <span className="text-ink-faint text-xs">not checked yet</span>
      ) : (
        <span className="text-ink-faint text-xs">
          last checked{" "}
          <span className="font-mono">
            {formatAthleteStamp(folder.last_delivery_at, timezone)}
          </span>
        </span>
      )}
      {folder.last_error === null ? null : (
        <span className="text-destructive text-xs">{folder.last_error}</span>
      )}
      {/* Pause keeps the cursor, so a folder switched off for a week resumes
          where it stopped instead of re-listing from scratch. Removing the
          last folder removes the integration — an entry with no transport is
          a source arc claims to collect from and cannot reach. */}
      <Button
        type="button"
        size="xs"
        variant="secondary"
        disabled={update.isPending}
        onClick={() =>
          update.mutate({ params, body: { enabled: !folder.enabled } })
        }
      >
        {folder.enabled ? "Pause" : "Resume"}
      </Button>
      <Button
        type="button"
        size="xs"
        variant="ghost"
        disabled={remove.isPending}
        onClick={() => remove.mutate({ params })}
      >
        {`Stop watching ${folder.remote_path || "the root"}`}
      </Button>
      <Problems
        problems={[
          ...apiErrorMessages(update.error),
          ...apiErrorMessages(remove.error),
        ]}
      />
    </li>
  );
}

/**
 * Forgetting a source, behind a question that says what survives it.
 *
 * Absent entirely when `removable` is false. The local drop has no row to
 * delete, and a disabled button would invite the athlete to work out why.
 */
function RemoveControl({ integration }: { readonly integration: Integration }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const remove = $api.useMutation(
    "delete",
    "/api/v1/integrations/{integration_id}",
    {
      onSuccess: () => {
        setAsking(false);
        queryClient.invalidateQueries({ queryKey: integrationsKey });
      },
    },
  );

  if (!integration.removable) {
    return null;
  }
  if (!asking) {
    return (
      <Button
        type="button"
        size="xs"
        variant="ghost"
        onClick={() => setAsking(true)}
      >
        {`Remove ${integration.display_name}`}
      </Button>
    );
  }
  return (
    <div className="flex w-full flex-col items-start gap-2">
      <InlineConfirm
        question={`Stop collecting from ${integration.display_name}? The account stays connected and every ride already collected stays in arc.`}
        confirmLabel={`Remove ${integration.display_name}`}
        cancelLabel="Keep it"
        disabled={remove.isPending}
        onCancel={() => setAsking(false)}
        onConfirm={() =>
          remove.mutate({
            params: { path: { integration_id: integration.id } },
          })
        }
      />
      <Problems problems={apiErrorMessages(remove.error)} />
    </div>
  );
}

/** Whatever the server refused, beside the control that failed. */
export function Problems({
  problems,
}: {
  readonly problems: readonly string[];
}) {
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
