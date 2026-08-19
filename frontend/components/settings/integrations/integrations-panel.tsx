"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { InlineConfirm } from "@/components/design/confirm";
import { Panel } from "@/components/design/panel";
import { SectionLabel } from "@/components/design/section-label";
import { AddIntegrationFlow } from "@/components/settings/integrations/add-integration-flow";
import {
  type Integration,
  IntegrationCard,
  integrationsKey,
  Problems,
} from "@/components/settings/integrations/integration-card";
import { LocalDropCard } from "@/components/settings/integrations/local-drop-card";
import { Button } from "@/components/ui/button";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages, loadFailureMessage } from "@/lib/api-errors";

type Connection = components["schemas"]["ConnectionRead"];

const connectionsKey = $api.queryOptions("get", "/api/v1/connections").queryKey;

/**
 * Every source arc collects from, in one place, named the way the athlete
 * thinks of them.
 *
 * This replaces `DropboxPanel`, and the replacement is the point rather than a
 * refactor. That panel was titled "Dropbox" and opened with "arc will watch a
 * folder in your Dropbox"; it offered `/apps/wahoofitness` with a file count
 * and never said the words "Wahoo" or "ELEMNT" outside its error copy. So at
 * the one moment the athlete decided something, arc showed them a filesystem
 * path and left them to work out it meant their bike computer — a fact arc
 * held all along. Meanwhile the `data/inbox/` sweep, running since WP-4.3,
 * appeared nowhere at all.
 *
 * **A panel, not a `/settings/integrations` route.** UI convention 1: this is
 * opened twice — once at setup and once when a ride stops arriving — and both
 * times by walking to Settings, never by following a link somebody sent.
 */
export function IntegrationsPanel({
  className,
}: {
  readonly className?: string;
}) {
  const integrations = $api.useQuery("get", "/api/v1/integrations");
  const [adding, setAdding] = useState(false);

  return (
    <Panel className={className} data-testid="integrations-panel">
      <div className="flex flex-col gap-3.5 px-5 py-4">
        <SectionLabel level={2}>What arc collects</SectionLabel>
        <p className="max-w-[62ch] text-ink-muted text-sm">
          Every source arc collects training data from, what each one brings in,
          and where it comes from.
        </p>

        {integrations.isPending ? (
          <p className="text-ink-muted text-sm">Reading your sources…</p>
        ) : // `!integrations.data` second, for the reason `ProfileForm` does
        // it: a background refetch that fails must not tear down a list that
        // has perfectly good entries — and a half-finished add flow — on
        // screen.
        !integrations.data ? (
          <p role="alert" className="text-destructive text-sm">
            {loadFailureMessage(integrations.error, "what arc collects")}
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {integrations.data.items.map((item) => (
              <Entry key={item.id} integration={item} />
            ))}
          </ul>
        )}

        {adding ? (
          <AddIntegrationFlow onDone={() => setAdding(false)} />
        ) : (
          <Button
            type="button"
            variant="secondary"
            className="self-start"
            onClick={() => setAdding(true)}
          >
            Add an integration
          </Button>
        )}

        <StorageAccounts integrations={integrations.data?.items ?? []} />
      </div>
    </Panel>
  );
}

/** The local drop's entry differs enough to be its own card (PR-4 owns it). */
function Entry({ integration }: { readonly integration: Integration }) {
  return integration.kind === "local_drop" ? (
    <LocalDropCard integration={integration} />
  ) : (
    <IntegrationCard integration={integration} />
  );
}

/**
 * The cloud accounts the folders above are collected through.
 *
 * Below the sources and deliberately smaller: an account is plumbing, and the
 * only thing an athlete ever does to it is disconnect. Absent entirely when
 * there is none — the add flow connects one as a step of adding a source, so a
 * "connect an account" control here would be a second, competing entrance to
 * the same ritual.
 */
function StorageAccounts({
  integrations,
}: {
  readonly integrations: readonly Integration[];
}) {
  const connections = $api.useQuery("get", "/api/v1/connections");
  const items = connections.data?.items ?? [];

  if (items.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-2 border-hairline border-t pt-3">
      <SectionLabel>Accounts arc collects through</SectionLabel>
      {items.map((connection) => (
        <AccountLine
          key={connection.id}
          connection={connection}
          integrations={integrations}
        />
      ))}
    </div>
  );
}

/** One account: who it is, what state it is in, and the way to forget it. */
function AccountLine({
  connection,
  integrations,
}: {
  readonly connection: Connection;
  readonly integrations: readonly Integration[];
}) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const disconnect = $api.useMutation(
    "delete",
    "/api/v1/connections/{connection_id}",
    {
      onSuccess: () => {
        setAsking(false);
        queryClient.invalidateQueries({ queryKey: connectionsKey });
        queryClient.invalidateQueries({ queryKey: integrationsKey });
      },
    },
  );

  return (
    <div className="flex flex-col items-start gap-2">
      <div className="flex w-full flex-wrap items-baseline gap-2">
        <span className="mr-auto text-ink-secondary text-sm">
          Dropbox — {connection.account_label ?? "an unnamed account"}
        </span>
        {asking ? null : (
          <Button
            type="button"
            size="xs"
            variant="destructive"
            onClick={() => setAsking(true)}
          >
            Disconnect Dropbox
          </Button>
        )}
      </div>
      {connection.status === "connected" ? null : (
        <p role="status" className="max-w-[62ch] text-destructive text-sm">
          {connection.last_error ??
            "arc cannot use this account, so nothing is being collected through it."}
        </p>
      )}
      {asking ? (
        <InlineConfirm
          // Names the *integrations*, not the folder count: the athlete added
          // Wahoo, and Wahoo is the thing they would miss. Rides already
          // collected stay in arc either way.
          question={`Disconnect ${connection.account_label ?? "this account"}? ${describeLosses(connection, integrations)}`}
          confirmLabel="Disconnect"
          cancelLabel="Keep it"
          disabled={disconnect.isPending}
          onCancel={() => setAsking(false)}
          onConfirm={() =>
            disconnect.mutate({
              params: { path: { connection_id: connection.id } },
            })
          }
        />
      ) : null}
      <Problems problems={apiErrorMessages(disconnect.error)} />
    </div>
  );
}

/**
 * What goes with an account: the integrations that exist only through it.
 *
 * One whose folders also live on another account survives, keeping that
 * folder, so it is not named here — the server applies the same rule.
 */
function describeLosses(
  connection: Connection,
  integrations: readonly Integration[],
): string {
  const doomed = integrations.filter(
    (integration) =>
      integration.folders.length > 0 &&
      integration.folders.every(
        (folder) => folder.connection_id === connection.id,
      ),
  );
  if (doomed.length === 0) {
    return "Nothing is being collected through it. Rides already collected stay in arc.";
  }
  const names = doomed.map((integration) => integration.display_name);
  return `${names.length} integration${names.length === 1 ? "" : "s"} go with it — ${names.join(", ")}. Rides already collected stay in arc.`;
}
