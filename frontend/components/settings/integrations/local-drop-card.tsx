"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Field, FieldRow } from "@/components/design/field";
import {
  describeProvides,
  type Integration,
  integrationsKey,
  Problems,
  Slot,
} from "@/components/settings/integrations/integration-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { components } from "@/generated/api/schema";
import { $api } from "@/lib/api/client";
import { apiErrorMessages } from "@/lib/api-errors";

/** The facet this card both reads and writes. */
const settingsKey = $api.queryOptions(
  "get",
  "/api/v1/integrations/local-drop/settings",
).queryKey;

/**
 * The local drop's entry: the oldest ingest path arc has, finally on screen.
 *
 * `data/inbox/` has been swept since WP-4.3, configured by `DATA__ROOT` and
 * `INGEST__SCAN_INTERVAL_SECONDS` in a file the athlete never sees. Every
 * other source in this list was something they chose; this one has been
 * running the whole time, and until now Settings said nothing about it — not
 * that it existed, not where it looked, not how often.
 *
 * So it reads as **active**, never as "not configured": there is nothing to
 * set up, and offering a remedy for a working sweep would send the athlete
 * looking for a problem that is not there. It also carries no remove control —
 * it is synthesized from settings and has no row a `DELETE` could find.
 *
 * **The path is shown; the interval is edited.** The asymmetry is the point
 * and the card says so out loud. `DATA__ROOT` roots `originals/`, `streams/`
 * and `quarantine/` as well as `inbox/`, and in Compose it is a mounted
 * volume: a form that moved it would strand every original arc has filed and
 * every stream it has written, so there is no such form and no endpoint behind
 * one. The interval had no such excuse — it was a deployment detail standing
 * in for a setting — and it is now the athlete's, applied to the running sweep
 * the moment they save.
 */
export function LocalDropCard({
  integration,
}: {
  readonly integration: Integration;
}) {
  const local = integration.local;
  return (
    <li>
      <section
        data-testid="integration"
        data-kind="local_drop"
        aria-label={integration.display_name}
        className="flex flex-col gap-2.5 rounded-card border border-hairline bg-inset px-3.5 py-3"
      >
        <h3 className="font-semibold text-ink text-sm">
          {integration.display_name}
        </h3>

        <Slot label="Brings in" testId="integration-provides">
          {describeProvides(integration.data_kinds)}
        </Slot>

        <Slot label="Where from" testId="integration-source">
          This arc server, at{" "}
          <span className="font-mono text-ink-secondary">
            {local?.inbox_path ?? "an unknown folder"}
          </span>
          . Drop a `.fit`, `.gpx` or `.tcx` file in there and arc collects it.
          <p className="mt-1 text-ink-faint text-xs">
            That folder is fixed here. It comes from `DATA__ROOT`, which also
            roots `originals/`, `streams/` and `quarantine/` and is a mounted
            volume in Docker — moving it from this page would leave every file
            arc has already stored behind. Change `DATA__ROOT` and restart to
            move the whole tree at once.
          </p>
        </Slot>

        <Slot label="To configure" testId="integration-setup">
          Nothing — already collecting. arc sweeps that folder every{" "}
          <span className="font-mono">{local?.scan_interval_seconds ?? 0}</span>{" "}
          seconds.
          <IntervalForm current={local?.scan_interval_seconds ?? 0} />
        </Slot>
      </section>
    </li>
  );
}

/**
 * How often the sweep runs, and which of the two places decided it.
 *
 * The bounds and the source come from the server rather than from constants
 * here: they are the rule the server enforces, and a copy of a rule is a copy
 * that can drift. The typed value is this component's own state and survives a
 * refusal — retyping a number you already typed is the one thing a rejected
 * form must never ask for.
 */
function IntervalForm({ current }: { readonly current: number }) {
  const queryClient = useQueryClient();
  const settings = $api.useQuery(
    "get",
    "/api/v1/integrations/local-drop/settings",
  );
  const [typed, setTyped] = useState(String(current));
  const save = $api.useMutation(
    "put",
    "/api/v1/integrations/local-drop/settings",
    {
      onSuccess: () => {
        // Both: this facet holds the source and the bounds, and the list holds
        // the sentence above — which would otherwise keep quoting the old
        // interval at an athlete who just changed it.
        queryClient.invalidateQueries({ queryKey: settingsKey });
        queryClient.invalidateQueries({ queryKey: integrationsKey });
      },
    },
  );

  const parsed = Number.parseInt(typed, 10);
  return (
    <form
      // The server is the authority on the range, and its refusal names both
      // limits and says why they are where they are. Native validation would
      // pre-empt that with "Value must be greater than or equal to 5".
      noValidate
      className="mt-2 flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({ body: { scan_interval_seconds: parsed } });
      }}
    >
      <FieldRow>
        <Field
          label="Sweep every"
          hint="seconds"
          htmlFor="local-drop-interval"
          className="w-[140px]"
        >
          <Input
            id="local-drop-interval"
            type="number"
            inputMode="numeric"
            min={settings.data?.minimum_seconds}
            max={settings.data?.maximum_seconds}
            value={typed}
            className="font-mono"
            onChange={(event) => setTyped(event.target.value)}
          />
        </Field>
        <Button
          type="submit"
          variant="secondary"
          disabled={save.isPending || Number.isNaN(parsed)}
        >
          Save
        </Button>
      </FieldRow>
      <IntervalSource source={settings.data?.source} />
      <Problems problems={apiErrorMessages(save.error)} />
    </form>
  );
}

/**
 * Which of the two sources the interval in force came from.
 *
 * Named because the two are undone differently: one is a click here, the other
 * is an edit to `.env` and a restart. A line that said only "every 30 seconds"
 * would leave an athlete on a config-as-code deployment wondering why their
 * file no longer wins.
 */
function IntervalSource({
  source,
}: {
  readonly source: components["schemas"]["SettingSource"] | undefined;
}) {
  if (source === undefined) {
    return null;
  }
  return (
    <p className="text-ink-faint text-xs">
      {source === "environment"
        ? "This is `INGEST__SCAN_INTERVAL_SECONDS`, from the server's environment. Saving here overrides it, on the running sweep, with no restart."
        : "Set here, and applied to the running sweep the moment you saved it."}
    </p>
  );
}
