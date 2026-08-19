"use client";

import {
  describeProvides,
  type Integration,
  Slot,
} from "@/components/settings/integrations/integration-card";

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
 * PR-4 owns this file next: the interval becomes editable in the app, and the
 * path gains the sentence explaining why it is not.
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
        </Slot>

        <Slot label="To configure" testId="integration-setup">
          Nothing — already collecting. arc sweeps that folder every{" "}
          <span className="font-mono">{local?.scan_interval_seconds ?? 0}</span>{" "}
          seconds.
        </Slot>
      </section>
    </li>
  );
}
